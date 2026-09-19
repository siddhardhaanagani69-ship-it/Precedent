"""Copy finished featured councils from SQLite into Supabase for the demo.

Reads with the SQLite backend and writes with the Supabase one, in dependency
order, so the featured replays survive the backend switch. Nothing here is on
the demo's critical path: on any failure, set DB_BACKEND=sqlite.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.config import SQLITE_PATH, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from store.sqlite_store import SQLiteStore
from store.supabase_store import SupabaseStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Copy every finished council, not only featured ones")
    args = parser.parse_args()

    source = SQLiteStore(SQLITE_PATH)
    target = SupabaseStore(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    councils = source.list_featured()
    if args.all:
        councils = [c for c in source.find_recent_councils("0000") if c["status"] == "done"] or councils

    copied = 0
    for council in councils:
        cid = council["id"]
        if target.get_council(cid):
            print(f"  {cid[:8]}: already present, skipped")
            continue
        try:
            target.client.table("councils").insert(council).execute()
            target.insert_stories(cid, source.get_stories(cid))
            for agent in source.get_agents(cid):
                target.client.table("agents").insert(agent).execute()
            for turn in source.get_turns(cid):
                target.client.table("turns").insert(turn).execute()
            for item in source.get_evidence(cid):
                target.client.table("evidence").insert(item).execute()
            for table, row in (("questions", source.get_question(cid)),
                               ("answers", source.get_answer(cid)),
                               ("verdicts", source.get_verdict(cid))):
                if row:
                    target.client.table(table).insert(row).execute()
            copied += 1
            print(f"  {cid[:8]}: copied")
        except Exception as exc:
            # Never expose upstream request detail.
            print(f"  {cid[:8]}: fail ({type(exc).__name__})")
    print(f"Copied {copied} of {len(councils)} councils")
    source.close()
    return int(copied != len(councils))


if __name__ == "__main__":
    raise SystemExit(main())
