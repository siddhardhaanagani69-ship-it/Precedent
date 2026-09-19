"""SQLite persistence with per-thread connections and atomic worker claims."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

JSON_FIELDS = {
    "weights", "facts", "plan", "progress", "question_embedding", "context",
    "reasons", "embedding", "beliefs", "stance", "prev_stance", "claims",
    "belief_changes", "candidate", "answers", "dissent", "crowd_vs_you", "receipts",
}
CONTENT_TABLES = {"stories", "agents", "turns", "evidence", "questions", "answers", "verdicts"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(key: str, value):
    return json.dumps(value, allow_nan=False) if key in JSON_FIELDS and value is not None else value


def decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {k: json.loads(v) if k in JSON_FIELDS and v is not None else v for k, v in dict(row).items()}


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a temporary file so worker threads share the database")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.connection.executescript(Path(__file__).with_name("schema.sql").read_text())
        self._columns = {
            table: {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            for table in CONTENT_TABLES | {"councils", "profiles", "worker_heartbeat"}
        }

    @property
    def connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "connection"):
            conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._local.connection = conn
        return self._local.connection

    def close(self) -> None:
        """Close only the calling thread's connection."""
        if hasattr(self._local, "connection"):
            self._local.connection.close()
            del self._local.connection

    @contextmanager
    def transaction(self):
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def _insert(self, table: str, fields: dict) -> dict:
        if table not in self._columns or not fields.keys() <= self._columns[table]:
            raise ValueError("Unknown storage fields")
        columns = ",".join(fields)
        placeholders = ",".join("?" for _ in fields)
        self.connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            [encode(k, v) for k, v in fields.items()],
        )
        return decode(self.connection.execute(
            f"SELECT * FROM {table} WHERE id = ?", (fields["id"],)
        ).fetchone())

    def _content(self, table: str, council_id: str, fields: dict) -> dict:
        if table not in CONTENT_TABLES:
            raise ValueError("Unknown content table")
        fields = {**fields, "id": fields.get("id") or str(uuid4()), "council_id": council_id}
        if "created_at" in self._columns[table]:
            fields.setdefault("created_at", now())
        return self._insert(table, fields)

    def _all(self, table: str, council_id: str) -> list[dict]:
        if table not in CONTENT_TABLES:
            raise ValueError("Unknown content table")
        order = {"turns": "seq", "stories": "similarity DESC, label", "agents": "cohort_key"}.get(table, "created_at, rowid")
        return [decode(row) for row in self.connection.execute(
            f"SELECT * FROM {table} WHERE council_id = ? ORDER BY {order}", (council_id,)
        )]

    def _one(self, table: str, council_id: str) -> dict | None:
        rows = self._all(table, council_id)
        return rows[0] if rows else None

    def create_council(self, visitor_id: str, question: str) -> dict:
        question = question.strip()
        if not visitor_id or not 10 <= len(question) <= 2000:
            raise ValueError("A visitor and a decision of 10–2000 characters are required")
        return self._insert("councils", {
            "id": str(uuid4()), "visitor_id": visitor_id, "question": question,
            "created_at": now(), "progress": {
                "stories_found": 0, "stories_kept": 0, "cohorts": 0, "round": 0,
                "verifications": 0, "llm_calls": 0, "valid_citation_pct": None,
                "started_at": None, "first_argument_at": None,
            },
        })

    def get_council(self, council_id: str) -> dict | None:
        return decode(self.connection.execute("SELECT * FROM councils WHERE id = ?", (council_id,)).fetchone())

    def claim_next_council(self, council_id: str | None = None) -> dict | None:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT id, status FROM councils WHERE status IN ('queued','answered') "
                "AND (? IS NULL OR id = ?) ORDER BY created_at LIMIT 1", (council_id, council_id)
            ).fetchone()
            if not row:
                return None
            status = "planning" if row["status"] == "queued" else "finalizing"
            changed = conn.execute(
                "UPDATE councils SET status = ?, claimed_at = ? WHERE id = ? AND status = ?",
                (status, now(), row["id"], row["status"]),
            ).rowcount
            return self.get_council(row["id"]) if changed == 1 else None

    def update_council(self, council_id: str, **fields) -> None:
        allowed = self._columns["councils"] - {"id", "visitor_id", "question", "created_at"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid council update")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        changed = self.connection.execute(
            f"UPDATE councils SET {assignments} WHERE id = ?",
            [*[encode(k, v) for k, v in fields.items()], council_id],
        ).rowcount
        if changed != 1:
            raise ValueError("Council does not exist")

    def list_featured(self) -> list[dict]:
        return [decode(r) for r in self.connection.execute(
            "SELECT * FROM councils WHERE is_featured = 1 AND status = 'done' ORDER BY created_at DESC"
        )]

    def find_recent_councils(self, since: str) -> list[dict]:
        return [decode(r) for r in self.connection.execute(
            "SELECT * FROM councils WHERE created_at >= ? AND status = 'done' AND question_embedding IS NOT NULL ORDER BY created_at DESC",
            (since,),
        )]

    def insert_stories(self, council_id: str, stories: list[dict]) -> None:
        with self.transaction():
            for story in stories:
                self._content("stories", council_id, story)

    def get_stories(self, council_id: str) -> list[dict]:
        return self._all("stories", council_id)

    def upsert_agent(self, council_id: str, agent: dict) -> dict:
        if not agent.keys() <= self._columns["agents"]:
            raise ValueError("Unknown agent fields")
        with self.transaction() as conn:
            existing = conn.execute("SELECT id FROM agents WHERE council_id = ? AND cohort_key = ?",
                                    (council_id, agent["cohort_key"])).fetchone()
            if not existing:
                return self._content("agents", council_id, agent)
            fields = {k: v for k, v in agent.items() if k not in {"id", "council_id", "cohort_key"}}
            if fields:
                assignments = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(f"UPDATE agents SET {assignments} WHERE id = ?",
                             [*[encode(k, v) for k, v in fields.items()], existing["id"]])
            return decode(conn.execute("SELECT * FROM agents WHERE id = ?", (existing["id"],)).fetchone())

    def get_agents(self, council_id: str) -> list[dict]:
        return self._all("agents", council_id)

    def insert_turn(self, council_id: str, **fields) -> dict:
        with self.transaction() as conn:
            seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM turns WHERE council_id = ?", (council_id,)).fetchone()[0]
            return self._content("turns", council_id, {**fields, "seq": seq})

    def get_turns(self, council_id: str) -> list[dict]:
        return self._all("turns", council_id)

    def insert_evidence(self, council_id: str, **fields) -> dict:
        with self.transaction() as conn:
            number = conn.execute("SELECT COUNT(*) + 1 FROM evidence WHERE council_id = ?", (council_id,)).fetchone()[0]
            return self._content("evidence", council_id, {**fields, "label": f"E{number}"})

    def get_evidence(self, council_id: str) -> list[dict]:
        return self._all("evidence", council_id)

    def insert_question(self, council_id: str, **fields) -> dict:
        return self._content("questions", council_id, fields)

    def get_question(self, council_id: str) -> dict | None:
        return self._one("questions", council_id)

    def insert_answer(self, council_id: str, visitor_id: str, answer_id: str) -> dict:
        """Validate ownership and choices, save the answer, and resume atomically."""
        with self.transaction():
            council = self.get_council(council_id)
            if council is None or council["visitor_id"] != visitor_id:
                raise PermissionError("Only the council's visitor may answer")
            question = self.get_question(council_id)
            if not question or answer_id not in {a["id"] for a in question["answers"]}:
                raise ValueError("Invalid answer choice")
            if council["status"] != "awaiting_user":
                raise ValueError("Council is not awaiting an answer")
            answer = self._content("answers", council_id, {
                "question_id": question["id"], "visitor_id": visitor_id, "answer_id": answer_id,
            })
            self.update_council(council_id, status="answered")
            return answer

    def get_answer(self, council_id: str) -> dict | None:
        return self._one("answers", council_id)

    def insert_verdict(self, council_id: str, **fields) -> dict:
        return self._content("verdicts", council_id, fields)

    def get_verdict(self, council_id: str) -> dict | None:
        return self._one("verdicts", council_id)

    def get_profile(self, visitor_id: str) -> dict | None:
        return decode(self.connection.execute("SELECT * FROM profiles WHERE visitor_id = ?", (visitor_id,)).fetchone())

    def save_profile(self, visitor_id: str, weights: dict, facts: dict) -> None:
        with self.transaction():
            previous = self.get_profile(visitor_id) or {"weights": {}, "facts": {}}
            self.connection.execute(
                "INSERT INTO profiles(visitor_id, weights, facts, updated_at) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(visitor_id) DO UPDATE SET weights=excluded.weights, facts=excluded.facts, updated_at=excluded.updated_at",
                (visitor_id, encode("weights", {**previous["weights"], **weights}),
                 encode("facts", {**previous["facts"], **facts}), now()),
            )

    def heartbeat(self) -> None:
        self.connection.execute("INSERT INTO worker_heartbeat(id, at) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET at=excluded.at", (now(),))

    def get_heartbeat(self) -> str | None:
        row = self.connection.execute("SELECT at FROM worker_heartbeat WHERE id = 1").fetchone()
        return row["at"] if row else None

    def cleanup_stale(self, before: str) -> int:
        return self.connection.execute(
            "UPDATE councils SET status='failed', error='worker restarted', finished_at=? "
            "WHERE status IN ('planning','scouting','mining','forming','debating','finalizing') AND claimed_at < ?",
            (now(), before),
        ).rowcount
