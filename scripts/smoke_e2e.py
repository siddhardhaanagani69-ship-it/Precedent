"""Run one real council outside the browser and print a sanitized summary."""

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.embeddings import Embeddings
from engine.worker import run_council
from store import get_store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--featured", action="store_true", help="Make this completed council readable as a demo from the home page")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    embedder = Embeddings()
    store = get_store()
    council = store.create_council(str(uuid4()), args.question)
    council = store.claim_next_council(council["id"])
    if council is None:
        raise SystemExit("The running worker already claimed this council. Inspect it in the browser.")
    started = time.monotonic()
    print(f"Council: {council['id']}", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        run_council(store, council, pool, embedder)
    final = store.get_council(council["id"])
    if args.featured and final["status"] == "done":
        store.update_council(council["id"], is_featured=True)
    report = {"id": council["id"], "status": final["status"], "elapsed_seconds": round(time.monotonic() - started, 2),
              "progress": final["progress"], "agents": [{k:a[k] for k in ("name", "family", "story_count", "stance")} for a in store.get_agents(council["id"])],
              "turn_count": len(store.get_turns(council["id"])), "verdict": store.get_verdict(council["id"])}
    destination = Path(__file__).resolve().parents[1] / "data" / f"smoke-{council['id']}.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    print(f"Room: http://localhost:5050/c/{council['id']}", flush=True)
    store.close()
    return int(final["status"] != "done")


if __name__ == "__main__":
    raise SystemExit(main())
