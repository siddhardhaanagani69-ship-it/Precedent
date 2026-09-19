"""Small live actor runs. Print counts and field names, never story bodies."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.apify_tools import normalize_reddit_items, run_actor
from engine.config import ROOT
from engine.config import APIFY_TOKEN, require_keys
from apify_client import ApifyClient


def first_fields(items: list[dict], kind: str) -> list[str]:
    for item in items:
        actual = str(item.get("dataType") or item.get("type") or "post").lower()
        if actual in ({"comment", "comments", "t1"} if kind == "comment" else {"post", "posts", "t3"}):
            return sorted(item)
        if kind == "comment" and isinstance(item.get("comments"), list) and item["comments"]:
            return sorted(item["comments"][0])
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-last", action="store_true", help="Inspect latest existing actor runs without starting new runs")
    args = parser.parse_args()
    report = {}
    calls = [
        ("reddit", "trudax/reddit-scraper-lite", {
            "searches": ["left stable job startup regret", "startup job one year later"],
            "searchPosts": True, "searchComments": False, "skipComments": False,
            "sort": "relevance", "maxItems": 20, "maxPostCount": 4, "maxComments": 3,
        }, 150),
        ("rag", "apify/rag-web-browser", {"query": "startup job career outcomes", "maxResults": 1, "outputFormats": ["markdown"]}, 45),
    ]
    for name, actor, inputs, timeout in calls:
        started = time.monotonic()
        try:
            if args.read_last:
                require_keys("APIFY_TOKEN")
                client = ApifyClient(APIFY_TOKEN, max_retries=0, timeout_secs=40)
                run = client.actor(actor).runs().list(limit=1, desc=True).items
                if not run or run[0]["status"] != "SUCCEEDED":
                    raise RuntimeError("Latest actor run has not succeeded")
                items = list(client.dataset(run[0]["defaultDatasetId"]).iterate_items())
            else:
                items = run_actor(actor, inputs, timeout)
            result = {"ok": bool(items), "count": len(items), "fields": sorted(items[0]) if items else []}
            if args.read_last:
                result["actor_seconds"] = round((run[0]["finishedAt"] - run[0]["startedAt"]).total_seconds(), 2)
                result["started_at"] = run[0]["startedAt"].isoformat()
            if name == "reddit":
                normalized = normalize_reddit_items(items)
                result.update(post_fields=first_fields(items, "post"), comment_fields=first_fields(items, "comment"),
                              usable_count=len(normalized), posts=sum(x["kind"] == "post" for x in normalized),
                              comments=sum(x["kind"] == "comment" for x in normalized))
        except Exception as exc:
            # SDK exception bodies may contain credentials: keep only the class/status.
            result = {"ok": False, "error": type(exc).__name__, "http_status": getattr(exc, "status_code", None)}
        result["latency_seconds"] = round(time.monotonic() - started, 2)
        report[name] = result
        print(f"{name}: {json.dumps(result)}", flush=True)
    destination = ROOT / ("data/apify_recovered.json" if args.read_last else "data/apify_smoke.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    return int(not all(result["ok"] for result in report.values()))


if __name__ == "__main__":
    raise SystemExit(main())
