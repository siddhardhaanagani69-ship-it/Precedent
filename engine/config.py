"""Shared configuration; the only place that loads environment files."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

ENV_NAMES = (
    "FEATHERLESS_API_KEY", "FEATHERLESS_CONCURRENCY_UNITS",
    "FEATHERLESS_MAX_CONTEXT", "APIFY_TOKEN", "DB_BACKEND", "SQLITE_PATH",
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "FLASK_SECRET_KEY",
)
FEATHERLESS_API_KEY = os.getenv("FEATHERLESS_API_KEY", "")
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
DB_BACKEND = os.getenv("DB_BACKEND") or "sqlite"
SQLITE_PATH = ROOT / (os.getenv("SQLITE_PATH") or "data/precedent.db")


def positive_int(name: str, default: int) -> int:
    """Reject malformed limits without exposing their values."""
    try:
        value = int(os.getenv(name) or default)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


CONCURRENCY_UNITS = positive_int("FEATHERLESS_CONCURRENCY_UNITS", 4)
MAX_CONTEXT = positive_int("FEATHERLESS_MAX_CONTEXT", 8192)
LIMITS = {
    "max_rounds": 3, "max_verifications": 2, "reddit_max_items": 250,
    "prefilter": 150, "extraction_batch": 6, "min_cohort": 5,
    "apify_wait": 150, "verification_timeout": 45, "llm_timeout": 60,
}
STANCE_TEMPERATURE = 4.0
# ponytail: conservative BGE relevance floor; calibrate on labeled decisions if recall is poor.
MIN_SOURCE_SIMILARITY = 0.55
MODEL_ROLES = {
    "extractor": [
        {"id": "Qwen/Qwen2.5-7B-Instruct", "family": "Qwen", "weight": 1},
        {"id": "meta-llama/Meta-Llama-3.1-8B-Instruct", "family": "Llama", "weight": 1},
    ],
    "agents": [
        {"id": "Qwen/Qwen2.5-14B-Instruct", "family": "Qwen", "weight": 1},
        {"id": "meta-llama/Meta-Llama-3.1-8B-Instruct", "family": "Llama", "weight": 1},
        {"id": "google/gemma-3-12b-it", "family": "Gemma", "weight": 1},
        {"id": "mistralai/Mistral-Nemo-Instruct-2407", "family": "Mistral", "weight": 1},
    ],
    "moderator": [
        {"id": "Qwen/Qwen2.5-32B-Instruct", "family": "Qwen", "weight": 2},
        {"id": "google/gemma-3-27b-it", "family": "Gemma", "weight": 2},
    ],
}


def require_keys(*names: str) -> None:
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing configuration: " + ", ".join(missing))
