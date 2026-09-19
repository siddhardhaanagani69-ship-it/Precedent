PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS profiles (
    visitor_id TEXT PRIMARY KEY, weights TEXT NOT NULL DEFAULT '{}',
    facts TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS councils (
    id TEXT PRIMARY KEY, visitor_id TEXT NOT NULL, question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
      ('queued','planning','scouting','mining','forming','debating',
       'awaiting_user','answered','finalizing','done','failed')),
    error TEXT, plan TEXT NOT NULL DEFAULT '{}', progress TEXT NOT NULL DEFAULT '{}',
    question_embedding TEXT, is_featured INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, claimed_at TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS councils_status ON councils(status, created_at);
CREATE INDEX IF NOT EXISTS councils_recent ON councils(created_at);
CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY, council_id TEXT NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
    label TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL, option_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('glad','regret')),
    context TEXT NOT NULL DEFAULT '{}', reasons TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL, months_after REAL,
    similarity REAL NOT NULL CHECK (similarity BETWEEN 0.2 AND 1),
    embedding TEXT, created_at TEXT NOT NULL, UNIQUE(council_id, label)
);
CREATE INDEX IF NOT EXISTS stories_council_id ON stories(council_id);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY, council_id TEXT NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
    cohort_key TEXT NOT NULL, name TEXT NOT NULL, persona TEXT NOT NULL,
    model TEXT NOT NULL, family TEXT NOT NULL, color TEXT NOT NULL,
    story_count INTEGER NOT NULL CHECK (story_count >= 0),
    weights TEXT NOT NULL, beliefs TEXT NOT NULL, stance TEXT NOT NULL,
    prev_stance TEXT NOT NULL DEFAULT '{}', UNIQUE(council_id, cohort_key),
    UNIQUE(council_id, id)
);
CREATE INDEX IF NOT EXISTS agents_council_id ON agents(council_id);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY, council_id TEXT NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, agent_id TEXT, round INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL CHECK (kind IN
      ('argument','moderator','factcheck','verification','rejected_update','system')),
    message TEXT NOT NULL, claims TEXT NOT NULL DEFAULT '[]',
    belief_changes TEXT NOT NULL DEFAULT '[]', stance TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, UNIQUE(council_id, seq),
    FOREIGN KEY(council_id, agent_id) REFERENCES agents(council_id, id)
);
CREATE INDEX IF NOT EXISTS turns_council_id ON turns(council_id);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY, council_id TEXT NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
    label TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('datacheck','web')),
    title TEXT NOT NULL, body TEXT NOT NULL, url TEXT,
    option_id TEXT NOT NULL, consequence_id TEXT NOT NULL,
    value REAL NOT NULL CHECK (value BETWEEN 0 AND 1), n INTEGER CHECK (n >= 0),
    created_at TEXT NOT NULL, UNIQUE(council_id, label)
);
CREATE INDEX IF NOT EXISTS evidence_council_id ON evidence(council_id);
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY, council_id TEXT NOT NULL UNIQUE REFERENCES councils(id) ON DELETE CASCADE,
    text TEXT NOT NULL, why TEXT NOT NULL, candidate TEXT NOT NULL,
    answers TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(council_id, id)
);
CREATE INDEX IF NOT EXISTS questions_council_id ON questions(council_id);
CREATE TABLE IF NOT EXISTS answers (
    id TEXT PRIMARY KEY, question_id TEXT NOT NULL,
    council_id TEXT NOT NULL UNIQUE REFERENCES councils(id) ON DELETE CASCADE,
    visitor_id TEXT NOT NULL, answer_id TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(council_id, question_id) REFERENCES questions(council_id, id)
);
CREATE INDEX IF NOT EXISTS answers_council_id ON answers(council_id);
CREATE TABLE IF NOT EXISTS verdicts (
    id TEXT PRIMARY KEY, council_id TEXT NOT NULL UNIQUE REFERENCES councils(id) ON DELETE CASCADE,
    recommendation TEXT NOT NULL, confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    summary TEXT NOT NULL, crux TEXT NOT NULL, dissent TEXT NOT NULL DEFAULT '{}',
    cheap_test TEXT NOT NULL, crowd_vs_you TEXT NOT NULL DEFAULT '{}',
    receipts TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS verdicts_council_id ON verdicts(council_id);
CREATE TABLE IF NOT EXISTS worker_heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1), at TEXT NOT NULL
);
