"""Bounded Apify calls and a username-free Reddit normalization boundary."""

import hashlib
import logging
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from apify_client import ApifyClient
from engine.config import APIFY_TOKEN, require_keys

logging.getLogger("apify_client").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)


def run_actor(actor_id: str, run_input: dict, timeout: int) -> list[dict]:
    """Bound actor runtime and SDK wait; never expose upstream errors."""
    require_keys("APIFY_TOKEN")
    # The SDK long-polls for wait_secs; the HTTP timeout must outlast that wait.
    client = ApifyClient(APIFY_TOKEN, max_retries=0, timeout_secs=timeout + 10)
    try:
        run = client.actor(actor_id).call(run_input=run_input, timeout_secs=timeout, wait_secs=timeout, logger=None)
        if not run:
            raise RuntimeError("Actor returned no run")
        if run["status"] not in {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}:
            client.run(run["id"]).abort()
            raise RuntimeError("Actor exceeded the wait limit")
        if run["status"] != "SUCCEEDED":
            raise RuntimeError("Actor did not complete successfully")
        return list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        reason = f"HTTP {status}" if status else type(exc).__name__
        raise RuntimeError(f"Apify {actor_id} failed ({reason})") from None


def normalize_reddit_item(item: dict) -> dict | None:
    """Keep source text and provenance, dropping usernames and profile records."""
    kind = str(item.get("dataType") or item.get("type") or item.get("kind") or "").lower()
    if kind in {"user", "community", "subreddit"}:
        return None
    kind = "comment" if kind in {"comment", "comments", "t1"} else "post"
    title = str(item.get("title") or "")
    body = str(item.get("body") or item.get("text") or item.get("selftext") or "")
    text = "\n\n".join(part for part in (title, body) if part).strip()
    if not text or text in {"[removed]", "[deleted]"}:
        return None
    # Never carry a returned author field or a textual u/name into persistence.
    for field in ("username", "author", "userName"):
        author = item.get(field)
        if isinstance(author, str) and author:
            text = re.sub(r"(?<!\w)" + re.escape(author) + r"(?!\w)", "[person]", text, flags=re.I)
    text = re.sub(r"(?:https?://(?:www\.)?reddit\.com/(?:u|user)/|/?u/)[\w-]+", "[person]", text)
    raw_url = item.get("url") or item.get("permalink") or item.get("postUrl") or ""
    url = urljoin("https://www.reddit.com", str(raw_url)) if raw_url else ""
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or
            parts.hostname not in {"reddit.com", "www.reddit.com", "old.reddit.com", "redd.it"} or
            parts.username or parts.password or re.search(r"/(?:u|user)/", parts.path)):
        return None
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return {"source": "reddit", "url": url, "text": text,
            "created_at": item.get("createdAt") or item.get("created_utc") or item.get("created"), "kind": kind}


def normalize_reddit_items(items: list[dict]) -> list[dict]:
    """Flatten nested comments, then deduplicate by URL and text digest."""
    result, seen = [], set()

    def add(item: dict, parent_url: str = "") -> None:
        if parent_url and not (item.get("url") or item.get("permalink") or item.get("postUrl")):
            item = {**item, "postUrl": parent_url}
        normalized = normalize_reddit_item(item)
        if normalized:
            key = (normalized["url"], hashlib.sha256(normalized["text"].encode()).hexdigest())
            if key not in seen:
                seen.add(key)
                result.append(normalized)
        children = item.get("comments") or item.get("replies") or []
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    add({**child, "dataType": "comment"}, normalized["url"] if normalized else parent_url)

    for item in items:
        add(item)
    return result
