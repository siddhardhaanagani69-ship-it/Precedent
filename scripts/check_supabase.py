"""Verify the Supabase connection, tables and RPC. Prints pass/fail only."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

TABLES = ("profiles", "councils", "stories", "agents", "turns", "evidence",
          "questions", "answers", "verdicts", "worker_heartbeat")


def main() -> int:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("Supabase: fail (URL or service role key missing)")
        return 1
    try:
        from store.supabase_store import SupabaseStore

        store = SupabaseStore(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception as exc:
        print(f"Supabase: fail (client did not start: {type(exc).__name__})")
        return 1

    failures = 0
    for table in TABLES:
        try:
            store.client.table(table).select("*").limit(1).execute()
            print(f"  {table}: pass")
        except Exception as exc:
            print(f"  {table}: fail ({type(exc).__name__})")
            failures += 1
            # Two consecutive service failures mean the schema is not in place.
            if failures >= 2:
                print("Supabase: fail (stopping after repeated errors; run 001_init.sql)")
                return 1

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        store.match_recent_council([0.0] * 384, 0.92, since)
        print("  match_recent_council: pass")
    except Exception as exc:
        print(f"  match_recent_council: fail ({type(exc).__name__})")
        failures += 1

    print("Supabase: pass" if not failures else "Supabase: fail (set DB_BACKEND=sqlite to demo)")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
