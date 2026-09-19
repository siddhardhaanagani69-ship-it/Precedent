# Precedent: build brief for Claude Code (Python + Flask)

You are my lead engineer for a 6-hour hackathon build. Read this entire brief before doing anything.

First actions, in order:
1. If this brief isn't already at `docs/SPEC.md`, save it there verbatim.
2. Create `.gitignore` with `.env`, `.venv/`, `data/`, `__pycache__/`, and `*.db` before anything else.
3. Write `CLAUDE.md` (40 lines max): stack, commands, secret-handling rules, and the rules in section 15.
4. Start Phase 0 (section 14). Stop at every CHECKPOINT and wait for me.

Assumed setup (verify in Phase 0 and tell me at Checkpoint 0 if anything is missing):
- `/.env` exists at the repo root with the Featherless and Apify keys.
- Python 3.11 or 3.12 is installed.
- I test-ran `trudax/reddit-scraper-lite` and `apify/rag-web-browser` in the Apify console.
- Supabase keys are NOT available yet. Build everything on SQLite first (section 6).

I understand Python much better than JavaScript. Keep the code plain and readable: functions over frameworks, type hints, short docstrings. Keep JavaScript to one small vanilla file.

## 1. The product

Precedent is a decision council. A user types a decision they're stuck on ("Should I leave my stable job for a startup offer?"). The app:
1. scrapes a few hundred stories from people who already made that choice and wrote about how it went;
2. extracts each story into a structured record (what they chose, their situation, how it turned out, why);
3. splits the stories into up to 4 cohorts: chose A and glad, chose A and regret it, chose B and glad, chose B and regret it;
4. turns each cohort into an agent, each running on a different open-weight model family, that argues for those real people and may only cite its own cohort's stories;
5. runs a moderated debate where:
   - stances are computed from evidence-backed beliefs and data-derived priorities;
   - factual clashes are settled by pooled data or a live web check;
   - agents cannot change their minds without new evidence;
6. asks the user at most ONE question, and only when the answer could change the outcome;
7. returns a verdict: recommendation + confidence, "most people online vs people like you", the crux, the strongest surviving dissent, one cheap test to run this week, and receipts.

Judges score:
- works live end to end without crashing;
- real engineering, not a thin wrapper;
- agents with real memory, tool use, and autonomous reasoning;
- non-obvious useful output;
- usable without explanation;
- novelty;
- performance beyond the happy path.

Optimize for a reliable live demo.

## 2. Hard constraints

- **Time:** 6 hours total, including my testing and rehearsal. Prefer simple, working, observable code over clever code.
- **Stack:** Python 3.11+ and Flask (server-rendered Jinja pages plus a small JSON API), with vanilla JS and one CSS file. No Node, no build step.
- **LLMs:** Featherless only (OpenAI-compatible API). The app never calls OpenAI or Anthropic.
- **Scraping:** Apify only, via the `apify-client` Python package.
- **Storage:** SQLite now, with Supabase Postgres (pgvector) as a drop-in backend once keys arrive. The app must run fully on SQLite.
- **Processes:** two of them, the Flask web app and a separate worker. They communicate only through the database.
- **Dependencies** (`requirements.txt`): flask, python-dotenv, openai, apify-client, pydantic, numpy, fastembed, supabase, pytest. Ask before adding anything else.

## 3. Secrets and env

`/.env` at the repo root already contains:
`FEATHERLESS_API_KEY`, `FEATHERLESS_CONCURRENCY_UNITS`, `FEATHERLESS_MAX_CONTEXT`, `APIFY_TOKEN`, `DB_BACKEND` (sqlite or supabase), `SQLITE_PATH`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (empty for now), `FLASK_SECRET_KEY`.

- Load it with python-dotenv in one place (`engine/config.py`); both processes import from there.
- Never print, log, echo, or commit secret values, not even partially.
- Never `cat`, open, or display `.env`. To check a variable, print only "set" or "missing".
- Create `.env.example` with the names and no values.
- Secrets never reach the browser. The browser only talks to Flask.

## 4. Repo layout and commands

```
/app                    Flask web app
  __init__.py           create_app()
  routes.py             pages + JSON API
  templates/            base.html, index.html, room.html
  static/               style.css, room.js
/engine                 the pipeline (worker process)
  config.py             env, limits, model roles
  worker.py             main loop: python -m engine.worker
  llm.py                Featherless client, JSON extraction/repair, retries
  scheduler.py          weighted priority semaphore
  apify_tools.py        scouting + verification
  embeddings.py         fastembed wrapper
  math_core.py          stance, swap test, question scoring (pure functions)
  schemas.py            pydantic models for every LLM output
  stages/               plan.py, scout.py, mine.py, cohorts.py, debate.py,
                        moderator.py, values.py, verdict.py
/store                  storage layer
  base.py               Store interface
  sqlite_store.py       default backend
  supabase_store.py     drop-in backend (section 6)
  schema.sql            SQLite schema
/supabase/migrations/001_init.sql   Postgres schema for later
/scripts                check_env.py, check_models.py, smoke_apify.py, smoke_e2e.py,
                        demo_seed.py, check_supabase.py, copy_to_supabase.py
/tests                  pytest
/docs                   SPEC.md
requirements.txt, README.md, CLAUDE.md
```

Commands (document these in the README, with venv activation for both Windows and macOS):
- Setup: `python -m venv .venv`, activate it, then `pip install -r requirements.txt`
- Web: `flask --app app --debug run --port 5050`. Use 5050, because macOS often has port 5000 taken.
- Worker: `python -m engine.worker`
- Tests: `pytest -q`
- Scripts: `python scripts/<name>.py`

The Flask debug reloader is fine because the pipeline runs in the separate worker process. Never start pipeline threads inside Flask.

## 5. Architecture

- The browser talks only to Flask (pages + JSON API) and polls it every 1s for the council state.
- Flask reads and writes the Store.
- The worker process claims councils from the Store and writes every stage back.
- Identity: on first visit, Flask sets a signed session cookie with a random `visitor_id` (uuid4). Memory is keyed by visitor_id. There's no login.

Council status machine: `queued → planning → scouting → mining → forming → debating → awaiting_user → answered → finalizing → done`, or `failed` with a human-readable `error`. Skip `awaiting_user` when no question is worth asking.

Worker:
- **Main loop:** every 1s, claim one council whose status is `queued` or `answered` with a conditional update (`UPDATE … SET status = <next>, claimed_at = now WHERE id = ? AND status = <current>`). The claim succeeds only if exactly one row changed.
- **Council concurrency:** run up to 2 councils at once in a ThreadPoolExecutor.
- **Parallel work inside a council:** extraction batches, agent turns, and Apify runs share a ThreadPoolExecutor (8 workers). The Featherless scheduler (section 7) is what actually limits LLM concurrency.
- **Heartbeat:** write a heartbeat row every 5s.
- **Startup cleanup:** mark councils stuck mid-pipeline for more than 10 minutes as `failed` ("worker restarted").
- **Progress:** each stage updates `councils.progress` (JSON) with `{stories_found, stories_kept, cohorts, round, verifications, llm_calls, valid_citation_pct, started_at, first_argument_at}`. Throttle to at most 2 writes per second.
- **Logging:** log every stage transition with timing, e.g. `[council 3f2a] mining done in 18.2s, 97 stories kept`.

Routes:
- `GET /`: home page.
- `POST /councils`: takes the question, creates a council (status `queued`, current visitor_id), and redirects to `/c/<id>`.
- `GET /c/<id>`: the room page.
- `GET /api/councils/<id>/state`: returns `{council, agents, turns, evidence, question, verdict}`. Keep it small: no story bodies.
- `GET /api/councils/<id>/cite/<label>`: returns one story (S#) or evidence item (E#) with its paraphrase and source URL.
- `POST /api/councils/<id>/answer`: takes `{answer_id}`. Only the council's own visitor may answer. Stores the answer and sets status to `answered`.
- `POST /api/councils/<id>/rerun`: creates a new council with the same question.
- `GET /api/health`: returns the backend name and the worker heartbeat age. The UI shows "Worker offline" when the heartbeat is older than 15s.

## 6. Storage: SQLite now, Supabase later

Pipeline and web code talk only to the `Store` interface (`store/base.py`). `DB_BACKEND` picks the implementation.

Methods:
- councils: `create_council`, `get_council`, `claim_next_council`, `update_council`, `list_featured`, `find_recent_councils`
- council contents: `insert_stories`, `get_stories`, `upsert_agent`, `get_agents`, `insert_turn`, `get_turns`, `insert_evidence`, `get_evidence`
- question and outcome: `insert_question`, `get_question`, `insert_answer`, `get_answer`, `insert_verdict`, `get_verdict`
- memory and health: `get_profile`, `save_profile`, `heartbeat`, `get_heartbeat`

Logical schema, identical in both backends. Index `council_id` everywhere.
- `profiles`: visitor_id pk, weights, facts, updated_at
- `councils`: id, visitor_id, question, status, error, plan, progress, question_embedding, is_featured, created_at, claimed_at, finished_at
- `stories`: id, council_id, label (S1, S2… by similarity rank), source, url, option_id, outcome ('glad' or 'regret'), context, reasons (`[{text, consequence_id, attribute, valence}]`), summary (paraphrase of 30 words max, no usernames), months_after, similarity, embedding, created_at
- `agents`: id, council_id, cohort_key ('A:regret'), name, persona, model, family, color, story_count, weights, beliefs (`{option_id: {consequence_id: p}}`), stance (`{option_id: p}`), prev_stance
- `turns`: id, council_id, seq (increasing), agent_id (null for moderator/system), round, kind ('argument', 'moderator', 'factcheck', 'verification', 'rejected_update', 'system'), message, claims, belief_changes, stance, created_at
- `evidence`: id, council_id, label (E1, E2…), kind ('datacheck' or 'web'), title, body, url, option_id, consequence_id, value, n, created_at
- `questions`: id, council_id, text, why, candidate, answers (`[{id, label}]`), created_at
- `answers`: id, question_id, council_id, visitor_id, answer_id, created_at
- `verdicts`: id, council_id, recommendation, confidence, summary, crux, dissent, cheap_test, crowd_vs_you, receipts, created_at
- `worker_heartbeat`: id = 1, at

SQLite (`sqlite_store.py`):
- ids are uuid4 strings; JSON and embeddings are stored as TEXT.
- Use WAL mode, `busy_timeout=5000`, and one connection per thread.
- Create tables on startup from `store/schema.sql`.

Supabase (`supabase_store.py`):
- Uses the `supabase` Python client with the service role key, server-side only.
- Postgres types: uuid, jsonb, and `vector(384)` via pgvector.
- `supabase/migrations/001_init.sql` must contain:
  - `create extension if not exists vector with schema extensions;`
  - the tables and council_id indexes;
  - RLS enabled on every table with NO public policies, so only the server-side service role can access data;
  - an RPC `match_recent_council(query_embedding vector(384), min_sim float, since timestamptz)` that returns council ids by cosine similarity.

The Supabase switch happens when I tell you the keys are in, never mid-phase:
1. I paste `001_init.sql` into the Supabase SQL editor, add `SUPABASE_SERVICE_ROLE_KEY` to `.env`, and set `DB_BACKEND=supabase`.
2. `python scripts/check_supabase.py` verifies the connection, tables, and RPC, printing only pass/fail.
3. `python scripts/copy_to_supabase.py` copies the featured councils over from SQLite.

If anything fails, switch back to sqlite. The demo must never depend on Supabase.

## 7. LLM layer (Featherless)

Client:
- Use the `openai` package: `OpenAI(base_url="https://api.featherless.ai/v1", api_key=..., max_retries=0)`, with `timeout=60` per call.
- Send each request as ONE user message with the instructions merged in, because some model chat templates reject a system role.

Model roles:
- Roles live in `engine/config.py` as ordered candidates with `{id, family, weight}`. Concurrency weight by size: up to 15B = 1, 24–34B = 2, 70B and up = 4.
  - extractor (weight 1): `Qwen/Qwen2.5-7B-Instruct`, `meta-llama/Meta-Llama-3.1-8B-Instruct`
  - agents (4 different families, weight 1): `Qwen/Qwen2.5-14B-Instruct`, `meta-llama/Meta-Llama-3.1-8B-Instruct`, `google/gemma-3-12b-it`, `mistralai/Mistral-Nemo-Instruct-2407`
  - moderator, which handles planning, personas, notes, the question, and the verdict (weight 2): `Qwen/Qwen2.5-32B-Instruct`, `google/gemma-3-27b-it`. If neither works, use the best agent model.
- `scripts/check_models.py`:
  - Send a 1-token completion to every candidate (this also warms them up) and record ok/latency/error.
  - Write the first working model per role to `engine/models_resolved.json`.
  - Pick agents from distinct families where possible. If fewer than 4 families work, reuse a family with a different model and say so.
  - Model IDs are case-sensitive.
  - If you call the public `GET /v1/models` catalog, set an explicit User-Agent header; the endpoint rejects some default user agents.

Scheduler (`engine/scheduler.py`):
- A thread-safe weighted semaphore (`threading.Condition`) with capacity `FEATHERLESS_CONCURRENCY_UNITS`.
- Three priority lanes: debate > verify > bulk. Serve waiters in priority order, first-come within a lane.
- Usage: `with scheduler.slot(weight, lane): ...`. Never exceed capacity.

Every call:
- Retry once on 429, 5xx, or timeout after 2s.
- Increment `llm_calls`.
- Temperatures: extraction 0.1, moderator 0.3, agents 0.7.
- Set sensible `max_tokens` per call site.

Structured output:
- Don't rely on native tool calling or `response_format`; only some Featherless model families support it.
- Ask for "ONLY JSON matching this schema", strip `<think>…</think>` blocks and code fences, extract the first balanced JSON value, and validate with pydantic.
- On failure, make ONE repair call to the extractor with the validation error. If it's still invalid, use a documented safe fallback.
- One bad JSON response never crashes a council.

Prompt size: keep each prompt under about 60% of `FEATHERLESS_MAX_CONTEXT` (estimate tokens as chars/4). Trim story lists, never instructions.

## 8. Tool layer (Apify)

- **Every run:** `ApifyClient(token).actor(id).call(run_input=..., timeout_secs=...)`, then `client.dataset(run["defaultDatasetId"]).iterate_items()`. Leave proxy settings at each actor's defaults.
- **Primary story source:** `trudax/reddit-scraper-lite`, with run_input roughly `{"searches": queries, "searchPosts": True, "searchComments": False, "skipComments": False, "sort": "relevance", "maxItems": 250, "maxPostCount": 40, "maxComments": 15}`.
  - Posts AND comments are story candidates; comments are often where people say what they chose and how it went.
  - Split the queries across 2 parallel runs to cut wall time.
- **Fallback** (when Reddit errors or returns fewer than 40 usable items): run `apify/google-search-scraper` with the queries plus `site:reddit.com`. Then fetch the top ~15 result URLs with `apify/rag-web-browser` and split the pages into candidates (paragraphs of 200+ characters).
- **Verification tool:** `apify/rag-web-browser` with `{"query": ..., "maxResults": 3}` and markdown output.
  - Use its Standby HTTP endpoint only if you confirm the URL and auth method from the actor README; otherwise use `.call()`.
  - Hard timeout 45s. On timeout, post a system turn and move on.
- **`scripts/smoke_apify.py`:**
  - Run the Reddit actor on 2 real queries at small limits.
  - Print the item count and the field NAMES of the first post and first comment, not their content.
  - Write `normalize_reddit_item()`, mapping whatever fields exist (title, body, text, url, createdAt, dataType…) to `{source, url, text, created_at, kind}`.
  - Also run the RAG Web Browser once.
  - If an input is rejected, read that actor's input docs.
- Never store usernames. Dedupe by URL plus a text hash.

## 9. Pipeline

### 9.1 plan (moderator model)

Input: the decision text plus the visitor's profile (memory). Output, validated with pydantic:

```json
{
  "title": "...",
  "options": [{"id": "A", "label": "..."}, {"id": "B", "label": "..."}],
  "attributes": ["money", "stability", "growth", "stress"],
  "consequences": [
    {"id": "c1", "label": "laid off within a year", "attribute": "stability", "impact": -3, "checkable_online": false}
  ],
  "situational": [
    {"key": "savings", "label": "months of savings", "values": ["under 3", "3 to 6", "over 6"], "user_value": null}
  ],
  "user_summary": "one sentence about the user's situation",
  "search_queries": ["..."]
}
```

- 2 options (3 max) and 4–6 attributes.
- 6–10 option-agnostic consequences, each with impact from −3 to +3. Set `checkable_online: true` when a consequence depends on current real-world facts (markets, prices, laws, a specific company).
- 2–4 situational facts that plausibly change outcomes. Prefill `user_value` from the text or the profile.
- 6 search queries aimed at OUTCOME stories for both options ("regret", "one year later", "glad I", "update").

### 9.2 scout

Run section 8. Update `stories_found` as items arrive. Cap the total wait at 150s and proceed with whatever arrived.

### 9.3 mine (extractor model, bulk lane)

1. Embed all candidates and the decision text (section 10). Keep the top 150 by cosine similarity before any LLM sees them.
2. Extract in batches of 6. For each candidate return:
   `{ idx, relevant, option_id or null, outcome: "glad"|"regret"|"mixed"|"unknown", months_after or null, context: {situational key: value or null}, reasons: [{text (15 words max), consequence_id (from the plan, or "other"), attribute, valence: -1 or 1}], summary: "third-person paraphrase, 30 words max, no names or usernames" }`.
   Keep relevant stories that have an option and a glad or regret outcome.
3. Embed `summary + context` for each kept story. `similarity` is the cosine against the embedding of `user_summary` plus known situational facts, rescaled to [0.2, 1]. Label stories S1…Sn by similarity rank.

### 9.4 form cohorts

- **Buckets:** group stories by `option:outcome`, giving up to 4 agents. Drop buckets with fewer than 5 stories.
- **Low-precedent mode:** if fewer than 2 buckets survive, skip the debate and write an honest verdict about what's missing. Never fabricate.
- **Fact table (the anti-bias step):** `P(c|o)` is the similarity-weighted share of ALL stories that chose o, glad AND regret together, whose reasons include c. Laplace-smooth it as `(w_c + 0.5) / (W_o + 1)` and also store the unweighted n. Save it in `plan["fact_table"]`.
- **`crowd_vs_you`:** per option, the glad rate over all its stories (unweighted) vs the similarity-weighted glad rate.
- **Per agent:**
  - `weights` (its cohort's revealed priorities): for each attribute, the share of the cohort's reasons on that attribute, mapped to 1..5 as `1 + 4 * share / max_share`.
  - initial `beliefs` ("what my people lived through"): cohort-internal shares for its own option (smoothed), fact table values for the other options.
  - persona: ONE moderator call writes all the names (e.g. "Left for the startup, regrets it") and a 2-sentence voice for each, from 5 sample summaries each.
  - Assign distinct model families and colors.
- Post a system turn announcing the council: cohorts, story counts, and model families.

### 9.5 debate (up to 3 rounds; agents in parallel on the debate lane)

Math lives in `engine/math_core.py` and is covered by pytest:
- `U(o) = Σ_c P(c|o) · impact(c) · weight[attribute(c)]`, and `stance = softmax(U / τ)`. Keep τ in config and tune it so typical stances land between 0.2 and 0.8.
- Stances are COMPUTED from beliefs and weights. LLMs never set stances.

Agent turn input:
- its persona, the decision, the options, the consequences, and its own weights and beliefs;
- its top 8 stories (labels + summaries) and the evidence board (latest 10 E-items);
- from round 2 on, the other agents' latest turns and the moderator's note.

Round 1 is blind: agents don't see each other's turns.

Agent turn output:
`{ message: "80 words max, first person, in character, cites like [S3] or [E2]", claims: [{text, cites: ["S3","E2"]}], belief_changes: [{option_id, consequence_id, new_p, cites: [], reason}] }`

Evidence gating is enforced in Python code, not by the LLM:
- S-labels must belong to the agent's own cohort; E-labels are shared. Drop unknown or foreign labels and count them in `valid_citation_pct`.
- Round 1: no belief changes.
- Round 2 and later: a belief change must cite at least one E-item created after the agent's previous turn.
- Invalid changes are not applied. Each one creates a visible `rejected_update` turn ("Blocked: <agent> tried to change <consequence> without new evidence"). This is a demo feature.
- Apply the valid changes, recompute the stance, store `prev_stance`, and insert the turn with a stance snapshot. Set `first_argument_at` on the first argument.

### 9.6 moderator (after every round; math first)

1. Take the agent pair with the largest stance gap on the leading option.
2. Run the swap test in both directions and average:
   - `total = |s(bA,wA) − s(bB,wB)|`
   - `factual = total − |s(bB,wA) − s(bB,wB)|` (gap closed if A adopts B's beliefs)
   - `values = total − |s(bA,wB) − s(bB,wB)|` (gap closed if A adopts B's weights)
3. If factual ≥ values, find the crux (o*, c*) by swapping one belief at a time and keeping the swap that closes the most gap. Then:
   - **Verification:** if c* is `checkable_online` or the fact table's n for o* is under 15, and fewer than 2 verifications have run, the moderator writes a search query and sends it to the RAG Web Browser. It summarizes the best source into an E-item `{finding, suggested p, url}` and posts a `verification` turn.
   - **Data check:** otherwise, post an E-item with the pooled fact-table value and its n, plus a `factcheck` turn ("Among 143 people like you who chose A, 31% mentioned a layoff in year one").
   - **Reset:** next round, every agent's belief on (o*, c*) resets to that E-item's value, unless its turn cites a different E-item created after its previous turn.
4. Stop the debate when any of these is true: values is at least 70% of total for the top pair; all stances are within 0.1 of each other; round 3 is done.
5. Otherwise the moderator writes a one-line note for the next round saying what's still contested.

### 9.7 values check (at most one question)

Candidates:
- **Values tradeoff:** among the top pair, find attrX (the attribute agent A weights more whose weight swap closes the most gap) and attrY (the same for agent B). Answer ids:
  - `x`: attrX = 5, attrY = 2
  - `even`: both 3.5
  - `y`: attrX = 2, attrY = 5

  Every other attribute takes the similarity-weighted average of the agents' weights. If no opposing attribute exists, ask a single-attribute question ("a lot / somewhat / a little"). The moderator writes plain-word button labels.
- **Situational splitters:** for each situational key whose user value is unknown (not in the plan or the profile), look at stories that chose the currently leading option and compare regret rates across the key's values. It's a candidate if the gap is at least 0.2 with at least 5 stories per group. The answers are the key's values.
- Drop anything memory already knows.

Scoring (pure math, zero LLM calls):
- Consensus beliefs are the similarity-weighted average of the agents' current beliefs.
- For each answer, recompute the verdict. Values answers swap in the user's weights. Situational answers recompute the fact table on that subgroup, falling back to the full table if the subgroup has fewer than 8 stories.
- Score = mean |v(answer) − v_now|.
- Ask only if some answer flips the recommendation OR the score is at least 0.10.
- On ties, prefer situational questions, because people answer facts more reliably than preferences.

If asking:
- The moderator phrases ONE concrete tradeoff using real numbers from the fact table, e.g. "Would you take 20% more pay if it came with about a 1-in-3 chance of a layoff in year one?".
- Add a one-line "why we're asking" that names the agents who agree on the facts but weigh them differently.
- The buttons are the candidate's answers.
- Insert the question and set status to `awaiting_user`.

If not asking: post a system turn "Nothing you could tell us would change this", then finalize.

### 9.8 verdict

- Final stance: consensus beliefs × weights. Use the user's weights if a values question was answered, otherwise the similarity-weighted average weights. Use the subgroup fact table if a situational question was answered.
- Fields:
  - `recommendation`
  - `confidence`: P(top option)
  - `crux`: one sentence on the fact that got settled and/or the tradeoff
  - `dissent`: the agent that disagrees most, with its strongest cited claim
  - `crowd_vs_you`: with n
  - `cheap_test`: one concrete action to take this week before committing
  - `receipts`: the 5–8 most-cited items with URLs
  - `summary`: 2–3 plain sentences
- Save the answer to the visitor's profile (weights or facts). Set status to `done`.

## 10. Embeddings

Only the worker needs embeddings.
- Use `fastembed` with `TextEmbedding("BAAI/bge-small-en-v1.5")`: 384 dimensions, no torch.
- Normalize the vectors so cosine is just a dot product.
- Load and warm up once at worker start. Batch size 32.
- If fastembed won't install or load, stop and tell me. The fallback is sentence-transformers `all-MiniLM-L6-v2`, which is a heavier install.

## 11. Web UI (Flask + Jinja + vanilla JS)

`/` (home):
- One large input ("What decision are you stuck on?").
- 3 example dilemmas to click.
- A list of featured councils (seeded demo runs) for instant replay.
- "The council remembers: …" when this visitor has a profile.

`/c/<id>` (the room): Flask renders the shell. `static/room.js` polls `/api/councils/<id>/state` every 1s, renders only turns it hasn't seen (by id), and updates agent meters in place. When the status reaches `done` or `failed`, it fetches once more and stops polling.
- Header: the decision plus a live pipeline strip driven by `progress` (stories found → kept → cohorts → round n).
- Agent row: one card per agent with its name, "based on 37 stories", its model family, and a stance meter for the leading option. The meter animates (CSS transition) only when the stance changes and keeps a faint marker at `prev_stance` so movement is obvious.
- Transcript: argument bubbles in agent colors. Moderator, fact-check, and verification turns look visually distinct. `rejected_update` turns appear as a clear blocked notice. [S3] and [E2] chips fetch `/cite/<label>` and show the paraphrase with its source link.
- Evidence panel, collapsible on mobile.
- Question card: big answer buttons plus the "why we're asking" line. Clicking POSTs to `/answer`.
- Verdict card: recommendation and confidence, "Most people online" vs "People like you", the crux, the dissent, the cheap test, and receipts.
- Metrics footer: time to first argument, stories kept, LLM calls, % valid citations, verifications.
- Every status has a visible state. `failed` shows the error and a "Run again" button. If the worker heartbeat is stale, show "Worker offline". Never a blank screen.

General:
- Plain CSS in `static/style.css` with CSS variables. No CSS frameworks, no build step.
- Mobile-first: judges will open this on their phones. The README explains the optional quick tunnel for phones on other networks (`cloudflared tunnel --url http://localhost:5050`).
- Design: a calm, trustworthy "deliberation room". Pick a distinct typeface pairing (Google Fonts is fine) and a restrained palette, and spend the boldness in one place: the stance meters moving.
- Avoid template tropes: all-caps eyebrow labels, gradient washes, identical rounded cards with soft grey shadows, cream-and-terracotta palettes.
- Sentence case everywhere. Respect prefers-reduced-motion. Keep keyboard focus visible.

## 12. Memory (the Agents-track requirement)

- **Profiles:** keyed by visitor_id and persisted across councils. The planner prefills situational `user_value`s from the profile, and the values check skips what's already known. When memory was used, show "The council remembers: <fact>" in the room.
- **Story cache:** before scouting, embed the question. If a council from the last 24h has cosine ≥ 0.92, copy its stories instead of scraping and post a system turn ("Reused 212 stories from a similar council").
  - SQLite mode: compute the cosine in Python over recent councils' question embeddings.
  - Supabase mode: use the `match_recent_council` RPC.

## 13. Limits and fallbacks (in `engine/config.py`)

Limits: max_rounds 3, max_verifications 2, reddit maxItems 250, prefilter 150, extraction batch 6, min cohort 5, Apify wait 150s, verification timeout 45s, LLM timeout 60s.

Fallbacks:
- Thin scrape → fallback source → still thin → low-precedent verdict.
- A model failing mid-council → switch that role to the next working model and post a system turn.
- One bad LLM response never fails a council.

## 14. Phases, checkpoints, and cut rules

**Phase 0 (target 30 min)**
- Gitignore, venv, and requirements.
- `scripts/check_env.py`, printing only set/missing per variable.
- SQLite store and schema.
- `check_models.py` and `smoke_apify.py`.
- `engine/math_core.py` with pytest tests, including:
  - same beliefs + different weights → values ≈ total, factual ≈ 0;
  - different beliefs + same weights → factual ≈ total, values ≈ 0;
  - a question-scoring test where one answer flips the recommendation.

CHECKPOINT 0: show the resolved models with latencies, the Apify counts and field names, and the test results. Wait for me.

**Phase 1 (target 2h)**
- The end-to-end spine on SQLite: plan → scout → mine → cohorts → 3 rounds with computed stances and evidence gating → a basic verdict (no question yet).
- The Flask room with polling, stance meters, and the verdict card.
- `python scripts/smoke_e2e.py "<dilemma>"` runs one council without the browser and prints stage timings plus a transcript summary.

CHECKPOINT 1: run `smoke_e2e.py` on "Should I leave my stable job for a startup offer?" and report timings. Then I test in the browser. Wait.

**Phase 2 (target 1.5h)**
- The moderator loop: swap test, data checks, up to 2 verifications, stop rule.
- `rejected_update` turns.
- The values check with question scoring, the question card, and resume-on-answer.
- crowd_vs_you and the full verdict.

CHECKPOINT 2: run a full council, answering the question from the script, and summarize the transcript and verdict. Wait.

**Phase 3 (target 45 min)**
- Memory ("The council remembers") and the story cache.
- Metrics footer, low-precedent mode, UI polish.
- `scripts/demo_seed.py`: run "Should I leave my stable job for a startup offer?" and "Should I buy a discounted house in a Houston floodplain or keep renting?" to completion, answering any question with the middle option, and mark them featured.
- The README.
- If time allows, write `001_init.sql`, `supabase_store.py`, `check_supabase.py`, and `copy_to_supabase.py` now (untested) so the switch is quick later.

CHECKPOINT 3: give me a 2-minute demo checklist.

**Supabase switch (about 30 min, only when I say the keys are in)**
Finish and test the Supabase backend per section 6. If it isn't working within 30 minutes, we demo on SQLite.

Cut rules:
- If Phase 1 isn't passing by 2h45m elapsed, do only `demo_seed.py` from Phase 3.
- If Phase 2 runs long, ship the values question without scoring (ask the values-tradeoff candidate directly).
- Always protect the working spine over new features. Supabase is never on the demo's critical path.

## 15. Non-negotiable rules

- Never print, display, or commit secrets.
- Stances are computed from beliefs × weights; LLMs never set them.
- Never block or crash the demo. Degrade gracefully and explain what happened in a system turn.
- No fabricated stories or statistics: every number shown traces back to stored data.
- Keep the Python plain and readable; I need to understand and present it.
- Stop at every checkpoint, and commit at each one with a clear message.
- If an external service fails twice in a row, stop looping. Report what you tried and propose the fallback.
- Don't ask me things you can learn from this spec or by running a command. Do ask before changing the architecture.
