"""Choose a backend once at process startup."""

from store.base import Store


def get_store() -> Store:
    from engine.config import DB_BACKEND, SQLITE_PATH
    from store.sqlite_store import SQLiteStore

    if DB_BACKEND != "sqlite":
        raise RuntimeError("Use DB_BACKEND=sqlite until the Supabase switch is implemented")
    return SQLiteStore(SQLITE_PATH)
