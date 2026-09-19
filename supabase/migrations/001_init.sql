-- Precedent: Postgres schema mirroring store/schema.sql.
-- Paste this into the Supabase SQL editor before setting DB_BACKEND=supabase.
-- Every table has RLS enabled with no policies, so only the server-side
-- service role reaches this data; the browser never talks to Supabase.

create extension if not exists vector with schema extensions;

create table if not exists profiles (
    visitor_id text primary key,
    weights jsonb not null default '{}'::jsonb,
    facts jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);

create table if not exists councils (
    id uuid primary key default gen_random_uuid(),
    visitor_id text not null,
    question text not null,
    status text not null default 'queued' check (status in
      ('queued','planning','scouting','mining','forming','debating',
       'awaiting_user','answered','finalizing','done','failed')),
    error text,
    plan jsonb not null default '{}'::jsonb,
    progress jsonb not null default '{}'::jsonb,
    question_embedding extensions.vector(384),
    is_featured boolean not null default false,
    created_at timestamptz not null default now(),
    claimed_at timestamptz,
    finished_at timestamptz
);
create index if not exists councils_status on councils(status, created_at);
create index if not exists councils_recent on councils(created_at);

create table if not exists stories (
    id uuid primary key default gen_random_uuid(),
    council_id uuid not null references councils(id) on delete cascade,
    label text not null,
    source text not null,
    url text not null,
    option_id text not null,
    outcome text not null check (outcome in ('glad','regret')),
    context jsonb not null default '{}'::jsonb,
    reasons jsonb not null default '[]'::jsonb,
    summary text not null,
    months_after real,
    similarity real not null check (similarity between 0.2 and 1),
    embedding extensions.vector(384),
    created_at timestamptz not null default now(),
    unique (council_id, label)
);
create index if not exists stories_council_id on stories(council_id);

create table if not exists agents (
    id uuid primary key default gen_random_uuid(),
    council_id uuid not null references councils(id) on delete cascade,
    cohort_key text not null,
    name text not null,
    persona text not null,
    model text not null,
    family text not null,
    color text not null,
    story_count integer not null check (story_count >= 0),
    weights jsonb not null,
    beliefs jsonb not null,
    stance jsonb not null,
    prev_stance jsonb not null default '{}'::jsonb,
    unique (council_id, cohort_key),
    unique (council_id, id)
);
create index if not exists agents_council_id on agents(council_id);

create table if not exists turns (
    id uuid primary key default gen_random_uuid(),
    council_id uuid not null references councils(id) on delete cascade,
    seq integer not null,
    agent_id uuid,
    round integer not null default 0,
    kind text not null check (kind in
      ('argument','moderator','factcheck','verification','rejected_update','system')),
    message text not null,
    claims jsonb not null default '[]'::jsonb,
    belief_changes jsonb not null default '[]'::jsonb,
    stance jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (council_id, seq),
    foreign key (council_id, agent_id) references agents(council_id, id)
);
create index if not exists turns_council_id on turns(council_id);

create table if not exists evidence (
    id uuid primary key default gen_random_uuid(),
    council_id uuid not null references councils(id) on delete cascade,
    label text not null,
    kind text not null check (kind in ('datacheck','web')),
    title text not null,
    body text not null,
    url text,
    option_id text not null,
    consequence_id text not null,
    value real not null check (value between 0 and 1),
    n integer check (n >= 0),
    created_at timestamptz not null default now(),
    unique (council_id, label)
);
create index if not exists evidence_council_id on evidence(council_id);

create table if not exists questions (
    id uuid primary key default gen_random_uuid(),
    council_id uuid not null unique references councils(id) on delete cascade,
    text text not null,
    why text not null,
    candidate jsonb not null,
    answers jsonb not null,
    created_at timestamptz not null default now(),
    unique (council_id, id)
);
create index if not exists questions_council_id on questions(council_id);

create table if not exists answers (
    id uuid primary key default gen_random_uuid(),
    question_id uuid not null,
    council_id uuid not null unique references councils(id) on delete cascade,
    visitor_id text not null,
    answer_id text not null,
    created_at timestamptz not null default now(),
    foreign key (council_id, question_id) references questions(council_id, id)
);
create index if not exists answers_council_id on answers(council_id);

create table if not exists verdicts (
    id uuid primary key default gen_random_uuid(),
    council_id uuid not null unique references councils(id) on delete cascade,
    recommendation text not null,
    confidence real check (confidence between 0 and 1),
    summary text not null,
    crux text not null,
    dissent jsonb not null default '{}'::jsonb,
    cheap_test text not null,
    crowd_vs_you jsonb not null default '{}'::jsonb,
    receipts jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists verdicts_council_id on verdicts(council_id);

create table if not exists worker_heartbeat (
    id integer primary key check (id = 1),
    at timestamptz not null
);

-- No policies are defined, so RLS denies every anon and authenticated request.
-- The service role bypasses RLS and is used only from the server.
alter table profiles          enable row level security;
alter table councils          enable row level security;
alter table stories           enable row level security;
alter table agents            enable row level security;
alter table turns             enable row level security;
alter table evidence          enable row level security;
alter table questions         enable row level security;
alter table answers           enable row level security;
alter table verdicts          enable row level security;
alter table worker_heartbeat  enable row level security;

-- Story cache lookup: the most similar recent council above a cosine floor.
-- Embeddings are stored normalized, so cosine distance is 1 - dot product.
create or replace function match_recent_council(
    query_embedding extensions.vector(384),
    min_sim float,
    since timestamptz
)
returns table (id uuid, question text, plan jsonb, similarity float)
language sql
stable
security definer
set search_path = public, extensions
as $$
    select c.id, c.question, c.plan,
           1 - (c.question_embedding <=> query_embedding) as similarity
    from councils c
    where c.question_embedding is not null
      and c.created_at >= since
      and c.status = 'done'
      and 1 - (c.question_embedding <=> query_embedding) >= min_sim
    order by c.question_embedding <=> query_embedding
    limit 5;
$$;

revoke all on function match_recent_council(extensions.vector(384), float, timestamptz) from public, anon, authenticated;
