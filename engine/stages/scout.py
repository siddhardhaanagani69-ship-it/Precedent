"""Two Reddit runs, with a bounded Google/RAG fallback for thin results."""

import hashlib
import re
import time
from concurrent.futures import wait, FIRST_COMPLETED
from urllib.parse import urlsplit

import numpy as np

from engine.apify_tools import normalize_reddit_items, run_actor
from engine.config import LIMITS, MIN_CANDIDATE_SIMILARITY, MIN_SOURCE_SIMILARITY


# Reddit's search ANDs every term, so the long natural phrases that a web search
# needs return almost no threads there. Trimming to the distinctive content words
# keeps the same intent while matching far more posts.
FILLER = {"a", "an", "the", "my", "i", "for", "to", "of", "in", "on", "at", "after",
          "before", "and", "or", "it", "its", "is", "was", "were", "am", "be", "been",
          "with", "from", "that", "this", "still", "initial", "instead", "about", "as"}


def reddit_query(phrase: str, words: int = 4) -> str:
    """Shorten a natural search phrase to its distinctive terms for Reddit search."""
    kept = [word for word in phrase.split() if word.strip(".,'\"").casefold() not in FILLER]
    return " ".join((kept or phrase.split())[:words])


# Fiction subreddits publish invented accounts, which the spec forbids treating as
# evidence; no extraction gate downstream can tell them from a real outcome.
FICTION = {"nosleep", "writingprompts", "shortstories", "creepypasta", "hfy",
           "libraryofshadows", "talesfromthecrypt"}


def fiction_source(url: str) -> bool:
    match = re.search(r"/r/([\w]+)", url or "")
    return bool(match) and match.group(1).casefold() in FICTION


OUTCOME_WORDS = re.compile(
    r"\b(regret\w*|glad|happy|happier|wish|mistake|worth|worked out|best decision|worst decision)\b", re.I)
FIRST_PERSON = re.compile(r"\b(I|my|we|our)\b", re.I)
# Short outcome reports lose on topical similarity alone, while cheers and thread
# noise can score well. First-person outcome language is the one cheap signal that
# separates them, so it nudges the ranking without deciding anything on its own.
OUTCOME_BONUS = 0.06


def outcome_language(text: str) -> bool:
    """True when a candidate reads like someone reporting their own result."""
    return bool(OUTCOME_WORDS.search(text)) and bool(FIRST_PERSON.search(text))


def intent_scores(ctx, plan: dict, candidates: list[dict]) -> np.ndarray:
    """Score each candidate against the best-matching outcome intent.

    The question alone is the wrong target: it ranks people re-asking the same
    dilemma and generic advice above the short first-person outcome reports the
    council needs. The plan's search queries are written to seek outcomes, so
    the best match across question and queries recovers those.
    """
    intents = ctx.embedder.embed([ctx.council["question"], *plan["search_queries"]])
    vectors = ctx.embedder.embed([c["text"] for c in candidates])
    bonus = np.array([OUTCOME_BONUS if outcome_language(c["text"]) else 0.0 for c in candidates])
    return (vectors @ intents.T).max(axis=1) + bonus


def relevant_candidates(ctx, plan: dict, candidates: list[dict], keep: int) -> list[dict]:
    """Rank first; the floor only trims a surplus and never starves extraction."""
    if not candidates:
        return []
    scores = intent_scores(ctx, plan, candidates)
    order = sorted(range(len(candidates)), key=lambda i: -scores[i])
    chosen = [i for i in order if scores[i] >= MIN_SOURCE_SIMILARITY][:keep]
    if len(chosen) < min(keep, LIMITS["min_candidates"]):
        taken = set(chosen)
        backfill = [i for i in order if i not in taken and scores[i] >= MIN_CANDIDATE_SIMILARITY]
        chosen += backfill[:min(keep, LIMITS["min_candidates"]) - len(chosen)]
    return [candidates[i] for i in chosen]


def outcome_candidates(candidates: list[dict]) -> int:
    """Count story-shaped candidates; this only decides whether to search wider."""
    return sum(outcome_language(c["text"]) for c in candidates)


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


def paragraphs(text: str, url: str) -> list[dict]:
    """Split fetched page text into story-sized candidates without usernames."""
    result = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if len(paragraph) >= 200:
            paragraph = re.sub(r"(?:https?://(?:www\.)?reddit\.com/(?:u|user)/|/?u/)[\w-]+", "[person]", paragraph)
            result.append({"source": "reddit-web", "url": url, "text": paragraph[:5000], "kind": "paragraph"})
    return result


def search_candidates(query: str, timeout: int) -> list[dict]:
    """Search and fetch in one actor run.

    Fetching Reddit thread URLs individually is routinely answered with 403, which
    left earlier runs with no fallback evidence at all. Letting the RAG browser run
    the search itself returns the page text it could actually retrieve.
    """
    pages = run_actor("apify/rag-web-browser", {"query": query, "maxResults": 3,
        "outputFormats": ["markdown"], "requestTimeoutSecs": max(1, timeout - 2)}, timeout, partial=True)
    result, usable = [], False
    for page in pages:
        if page.get("crawl", {}).get("requestStatus") == "failed":
            continue
        url = (page.get("metadata") or {}).get("url") or (page.get("searchResult") or {}).get("url") or ""
        parts = urlsplit(url)
        # The site: operator is advisory and the browser returns general web results:
        # one run pulled 22 paragraphs of vendor marketing from a company called Worth
        # and 11 from a cancer charity, both matched on "worth it". Only Reddit threads
        # are firsthand decision accounts, so anything else is dropped here.
        if (parts.scheme not in {"http", "https"}
                or parts.hostname not in {"reddit.com", "www.reddit.com", "old.reddit.com"}
                or "/comments/" not in parts.path
                or re.search(r"/(?:u|user)/", parts.path)):
            continue
        usable = True
        result.extend(paragraphs(page.get("markdown") or page.get("text") or "", url))
    if not usable:
        raise RuntimeError("Search results were blocked or unavailable")
    return result


def run(ctx, plan: dict) -> list[dict]:
    deadline = time.monotonic() + LIMITS["apify_wait"]
    queries = plan["search_queries"]

    def reddit(batch):
        items = run_actor("trudax/reddit-scraper-lite", {
            "searches": [reddit_query(q) for q in batch], "startUrls": [], "ignoreStartUrls": True,
            "searchPosts": True, "searchComments": False, "includeNSFW": False,
            "skipComments": False, "skipCommunity": True, "sort": "relevance",
            "maxItems": LIMITS["reddit_max_items"] // 2, "maxPostCount": 40, "maxComments": 15,
            "scrollTimeout": 10,
        }, 70, partial=True)
        return normalize_reddit_items(items)

    futures = [ctx.pool.submit(reddit, queries[::2]), ctx.pool.submit(reddit, queries[1::2])]
    candidates = relevant_candidates(ctx, plan, collect(futures, min(deadline, time.monotonic() + 80), ctx),
                                     LIMITS["reddit_max_items"])
    ctx.progress(stories_found=len(candidates))
    if outcome_candidates(candidates) < 40 and deadline - time.monotonic() > 10:
        ctx.notice("Reddit returned few usable stories. Searching indexed pages through Apify.")
        # Bound actor use and stop after two consecutive failures, per the spec.
        failures = 0
        for query in queries:
            remaining = int(deadline - time.monotonic())
            if remaining <= 10 or failures >= 2:
                break
            try:
                candidates.extend(search_candidates(f"{query} site:reddit.com",
                                                    min(LIMITS["verification_timeout"], remaining - 5)))
                failures = 0
            except RuntimeError:
                failures += 1
        if failures >= 2:
            ctx.notice("Indexed source pages were blocked or unavailable. Continuing with the Reddit results already received.")
    unique = {}
    for candidate in candidates:
        if fiction_source(candidate["url"]):
            continue
        key = (candidate["url"], hashlib.sha256(candidate["text"].encode()).hexdigest())
        unique.setdefault(key, candidate)
    candidates = relevant_candidates(ctx, plan, list(unique.values()), LIMITS["reddit_max_items"])
    ctx.progress(stories_found=len(candidates), force=True)
    ctx.notice(f"Found {len(candidates)} source candidates. Next: keep relevant firsthand outcomes.")
    return candidates
