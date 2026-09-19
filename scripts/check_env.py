"""Check presence only. Never display configuration values."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.config import DB_BACKEND, ENV_NAMES


def main() -> int:
    for name in ENV_NAMES:
        print(f"{name}: {'set' if os.getenv(name) else 'missing'}")
    required = {"FEATHERLESS_API_KEY", "APIFY_TOKEN", "FLASK_SECRET_KEY"}
    if DB_BACKEND == "supabase":
        required |= {"SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"}
    return int(any(not os.getenv(name) for name in required))


if __name__ == "__main__":
    raise SystemExit(main())
