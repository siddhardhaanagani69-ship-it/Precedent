"""Supabase Postgres backend, swapped in by DB_BACKEND without touching callers.

Written to the same Store contract as SQLiteStore. Postgres holds JSON as jsonb
and embeddings as pgvector, so the JSON encoding SQLite needs disappears here;
the two remaining differences are the atomic council claim, done with a
conditional update, and the story cache, done with the match_recent_council RPC.

This backend is never on the demo's critical path. If anything fails, set
DB_BACKEND=sqlite and the app runs exactly as before.
"""

from datetime import datetime, timezone
from uuid import uuid4

CONTENT_TABLES = {"stories", "agents", "turns", "evidence", "questions", "answers", "verdicts"}
COUNCIL_UPDATABLE = {
    "status", "error", "plan", "progress", "question_embedding",
    "is_featured", "claimed_at", "finished_at",
}
RUNNING = ["planning", "scouting", "mining", "forming", "debating", "finalizing"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseStore:
    """One client per process; the service role key stays server-side."""

    def __init__(self, url: str, service_role_key: str):
        from supabase import create_client

        if not url or not service_role_key:
            raise RuntimeError("Supabase URL and service role key are required")
        self.client = create_client(url, service_role_key)

    def close(self) -> None:
        """The HTTP client needs no per-thread teardown."""

    def _table(self, name: str):
        return self.client.table(name)

    def _content(self, table: str, council_id: str, fields: dict) -> dict:
        if table not in CONTENT_TABLES:
            raise ValueError("Unknown content table")
        row = {**fields, "id": fields.get("id") or str(uuid4()), "council_id": council_id}
        row.setdefault("created_at", now())
        return self._table(table).insert(row).execute().data[0]

    def _all(self, table: str, council_id: str) -> list[dict]:
        if table not in CONTENT_TABLES:
            raise ValueError("Unknown content table")
        query = self._table(table).select("*").eq("council_id", council_id)
        if table == "turns":
            query = query.order("seq")
        elif table == "stories":
            query = query.order("similarity", desc=True).order("label")
        elif table == "agents":
            query = query.order("cohort_key")
        else:
            query = query.order("created_at")
        return query.execute().data

    def _one(self, table: str, council_id: str) -> dict | None:
        rows = self._all(table, council_id)
        return rows[0] if rows else None

    def create_council(self, visitor_id: str, question: str) -> dict:
        question = question.strip()
        if not visitor_id or not 10 <= len(question) <= 2000:
            raise ValueError("A visitor and a decision of 10–2000 characters are required")
        return self._table("councils").insert({
            "id": str(uuid4()), "visitor_id": visitor_id, "question": question,
            "created_at": now(), "progress": {
                "stories_found": 0, "stories_kept": 0, "cohorts": 0, "round": 0,
                "verifications": 0, "llm_calls": 0, "valid_citation_pct": None,
                "started_at": None, "first_argument_at": None,
            },
        }).execute().data[0]

    def get_council(self, council_id: str) -> dict | None:
        rows = self._table("councils").select("*").eq("id", council_id).execute().data
        return rows[0] if rows else None

    def claim_next_council(self, council_id: str | None = None) -> dict | None:
        """Claim with a conditional update, so two workers never take one council."""
        query = self._table("councils").select("id,status").in_("status", ["queued", "answered"])
        if council_id:
            query = query.eq("id", council_id)
        rows = query.order("created_at").limit(1).execute().data
        if not rows:
            return None
        current = rows[0]["status"]
        claimed = self._table("councils").update(
            {"status": "planning" if current == "queued" else "finalizing", "claimed_at": now()}
        ).eq("id", rows[0]["id"]).eq("status", current).execute().data
        return claimed[0] if len(claimed) == 1 else None

    def update_council(self, council_id: str, **fields) -> None:
        if not fields or not fields.keys() <= COUNCIL_UPDATABLE:
            raise ValueError("Invalid council update")
        changed = self._table("councils").update(fields).eq("id", council_id).execute().data
        if len(changed) != 1:
            raise ValueError("Council does not exist")

    def list_featured(self) -> list[dict]:
        return (self._table("councils").select("*").eq("is_featured", True)
                .eq("status", "done").order("created_at", desc=True).execute().data)

    def find_recent_councils(self, since: str) -> list[dict]:
        """Recent finished councils; memory.find scores them the same way as SQLite."""
        return (self._table("councils").select("*").gte("created_at", since)
                .eq("status", "done").not_.is_("question_embedding", "null")
                .order("created_at", desc=True).execute().data)

    def match_recent_council(self, embedding: list[float], min_similarity: float, since: str) -> list[dict]:
        """pgvector nearest-neighbour lookup, the Postgres path for the story cache."""
        return self.client.rpc("match_recent_council", {
            "query_embedding": embedding, "min_sim": min_similarity, "since": since,
        }).execute().data

    def insert_stories(self, council_id: str, stories: list[dict]) -> None:
        if stories:
            self._table("stories").insert([
                {**s, "id": s.get("id") or str(uuid4()), "council_id": council_id,
                 "created_at": s.get("created_at") or now()} for s in stories
            ]).execute()

    def get_stories(self, council_id: str) -> list[dict]:
        return self._all("stories", council_id)

    def upsert_agent(self, council_id: str, agent: dict) -> dict:
        existing = (self._table("agents").select("id").eq("council_id", council_id)
                    .eq("cohort_key", agent["cohort_key"]).execute().data)
        if not existing:
            return self._content("agents", council_id, agent)
        fields = {k: v for k, v in agent.items() if k not in {"id", "council_id", "cohort_key"}}
        if not fields:
            return self._table("agents").select("*").eq("id", existing[0]["id"]).execute().data[0]
        return self._table("agents").update(fields).eq("id", existing[0]["id"]).execute().data[0]

    def get_agents(self, council_id: str) -> list[dict]:
        return self._all("agents", council_id)

    def insert_turn(self, council_id: str, **fields) -> dict:
        rows = (self._table("turns").select("seq").eq("council_id", council_id)
                .order("seq", desc=True).limit(1).execute().data)
        return self._content("turns", council_id, {**fields, "seq": (rows[0]["seq"] if rows else 0) + 1})

    def get_turns(self, council_id: str) -> list[dict]:
        return self._all("turns", council_id)

    def insert_evidence(self, council_id: str, **fields) -> dict:
        count = (self._table("evidence").select("id", count="exact")
                 .eq("council_id", council_id).execute().count or 0)
        return self._content("evidence", council_id, {**fields, "label": f"E{count + 1}"})

    def get_evidence(self, council_id: str) -> list[dict]:
        return self._all("evidence", council_id)

    def insert_question(self, council_id: str, **fields) -> dict:
        return self._content("questions", council_id, fields)

    def get_question(self, council_id: str) -> dict | None:
        return self._one("questions", council_id)

    def insert_answer(self, council_id: str, visitor_id: str, answer_id: str) -> dict:
        """Validate ownership and the offered choices before resuming the council."""
        council = self.get_council(council_id)
        if council is None or council["visitor_id"] != visitor_id:
            raise PermissionError("Only the council's visitor may answer")
        question = self.get_question(council_id)
        if not question or answer_id not in {a["id"] for a in question["answers"]}:
            raise ValueError("Invalid answer choice")
        # The status check is part of the update, so a repeated answer cannot resume twice.
        resumed = (self._table("councils").update({"status": "answered"})
                   .eq("id", council_id).eq("status", "awaiting_user").execute().data)
        if len(resumed) != 1:
            raise ValueError("Council is not awaiting an answer")
        return self._content("answers", council_id, {
            "question_id": question["id"], "visitor_id": visitor_id, "answer_id": answer_id,
        })

    def get_answer(self, council_id: str) -> dict | None:
        return self._one("answers", council_id)

    def insert_verdict(self, council_id: str, **fields) -> dict:
        return self._content("verdicts", council_id, fields)

    def get_verdict(self, council_id: str) -> dict | None:
        return self._one("verdicts", council_id)

    def get_profile(self, visitor_id: str) -> dict | None:
        rows = self._table("profiles").select("*").eq("visitor_id", visitor_id).execute().data
        return rows[0] if rows else None

    def save_profile(self, visitor_id: str, weights: dict, facts: dict) -> None:
        previous = self.get_profile(visitor_id) or {"weights": {}, "facts": {}}
        self._table("profiles").upsert({
            "visitor_id": visitor_id,
            "weights": {**previous["weights"], **weights},
            "facts": {**previous["facts"], **facts},
            "updated_at": now(),
        }).execute()

    def heartbeat(self) -> None:
        self._table("worker_heartbeat").upsert({"id": 1, "at": now()}).execute()

    def get_heartbeat(self) -> str | None:
        rows = self._table("worker_heartbeat").select("at").eq("id", 1).execute().data
        return rows[0]["at"] if rows else None

    def cleanup_stale(self, before: str) -> int:
        changed = (self._table("councils")
                   .update({"status": "failed", "error": "worker restarted", "finished_at": now()})
                   .in_("status", RUNNING).lt("claimed_at", before).execute().data)
        return len(changed)
