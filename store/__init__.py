"""Choose a backend once at process startup."""

from store.base import Store


def get_store() -> Store:
    from engine.config import DB_BACKEND, SQLITE_PATH, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

    if DB_BACKEND == "sqlite":
        from store.sqlite_store import SQLiteStore

        return SQLiteStore(SQLITE_PATH)
    if DB_BACKEND == "supabase":
        from store.supabase_store import SupabaseStore

        return SupabaseStore(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    raise RuntimeError("DB_BACKEND must be sqlite or supabase")
