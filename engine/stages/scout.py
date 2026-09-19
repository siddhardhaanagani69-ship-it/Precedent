"""Two Reddit runs, with a bounded Google/RAG fallback for thin results."""

import hashlib
import re
import time
from concurrent.futures import wait, FIRST_COMPLETED
from urllib.parse import urlsplit

from engine.apify_tools import normalize_reddit_items, run_actor
from engine.config import LIMITS, MIN_SOURCE_SIMILARITY


def relevant_candidates(ctx, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    vectors = ctx.embedder.embed([ctx.council["question"], *[c["text"] for c in candidates]])
    return [c for c, score in zip(candidates, vectors[1:] @ vectors[0]) if score >= MIN_SOURCE_SIMILARITY]


def outcome_candidates(candidates: list[dict]) -> int:
    # ponytail: language heuristic only triggers a broader search; extraction makes the real decision.
    return sum(bool(re.search(r"\b(regret\w*|glad|happy|happier|wish|mistake|worth|worked out|best decision|worst decision)\b", c["text"], re.I))
               and bool(re.search(r"\b(I|my|we|our)\b", c["text"], re.I)) for c in candidates)


def collect(futures: list, deadline: float, ctx) -> list[dict]:
    result = []
    pending = set(futures)
    while pending and time.monotonic() < deadline:
        done, pending = wait(pending, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
        for future in done:
            try:
                result.extend(future.result())
            except Exception:
                ctx.notice("A source request did not finish successfully. Continuing with available sources.")
    for future in pending:
        future.cancel()
    if pending:
        ctx.notice("The source time limit was reached. Continuing with results already received.")
    return result


def page_candidates(url: str, timeout: int) -> list[dict]:
    pages = run_actor("apify/rag-web-browser", {"query": url, "maxResults": 1,
        "outputFormats": ["markdown"], "requestTimeoutSecs": max(1, timeout - 2)}, timeout, partial=True)
    result = []
    for page in pages:
        if page.get("crawl", {}).get("requestStatus") == "failed":
            raise RuntimeError("Source page blocked or unavailable")
        text = page.get("markdown") or page.get("text") or ""
        for paragraph in re.split(r"\n\s*\n", text):
            if len(paragraph) >= 200:
                paragraph = re.sub(r"(?:https?://(?:www\.)?reddit\.com/(?:u|user)/|/?u/)[\w-]+", "[person]", paragraph)
                result.append({"source": "reddit-web", "url": url, "text": paragraph[:5000], "kind": "paragraph"})
    return result


def run(ctx, plan: dict) -> list[dict]:
    deadline = time.monotonic() + LIMITS["apify_wait"]
    queries = plan["search_queries"]

    def reddit(batch):
        items = run_actor("trudax/reddit-scraper-lite", {
            "searches": batch, "startUrls": [], "ignoreStartUrls": True,
            "searchPosts": True, "searchComments": False, "includeNSFW": False,
            "skipComments": False, "skipCommunity": True, "sort": "relevance",
            "maxItems": LIMITS["reddit_max_items"] // 2, "maxPostCount": 40, "maxComments": 15,
            "scrollTimeout": 10,
        }, 70, partial=True)
        return normalize_reddit_items(items)

    futures = [ctx.pool.submit(reddit, queries[::2]), ctx.pool.submit(reddit, queries[1::2])]
    candidates = relevant_candidates(ctx, collect(futures, min(deadline, time.monotonic() + 80), ctx))
    ctx.progress(stories_found=len(candidates))
    if outcome_candidates(candidates) < 40 and deadline - time.monotonic() > 10:
        ctx.notice("Reddit returned few usable stories. Checking indexed Reddit pages through Apify.")
        remaining = max(1, min(20, int(deadline - time.monotonic() - 5)))
        future = ctx.pool.submit(run_actor, "apify/google-search-scraper", {
            "queries": "\n".join(q + " site:reddit.com" for q in queries), "maxPagesPerQuery": 1,
        }, remaining, partial=True)
        pages = collect([future], min(deadline, time.monotonic() + remaining + 3), ctx)
        urls = []
        for page in pages:
            for result in page.get("organicResults", []):
                url = result.get("url", "")
                if (urlsplit(url).hostname in {"reddit.com", "www.reddit.com", "old.reddit.com"}
                        and "/comments/" in url and url not in urls):
                    urls.append(url)
        # Bound actor concurrency and stop after two consecutive page failures.
        failures = 0
        for url in urls[:15]:
            remaining = int(deadline - time.monotonic())
            if remaining <= 5 or failures >= 2:
                break
            try:
                candidates.extend(page_candidates(url, min(45, remaining - 3)))
                failures = 0
            except RuntimeError:
                failures += 1
        if failures:
            ctx.notice("Indexed source pages were blocked or unavailable. Continuing with the Reddit results already received.")
    unique = {}
    for candidate in candidates:
        key = (candidate["url"], hashlib.sha256(candidate["text"].encode()).hexdigest())
        unique.setdefault(key, candidate)
    candidates = relevant_candidates(ctx, list(unique.values()))[:LIMITS["reddit_max_items"]]
    ctx.progress(stories_found=len(candidates), force=True)
    ctx.notice(f"Found {len(candidates)} source candidates. Next: keep relevant firsthand outcomes.")
    return candidates
