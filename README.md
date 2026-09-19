# Precedent

A decision council grounded in real stories. Python 3.11/3.12, Flask and
vanilla JavaScript; Featherless for models, Apify for sources, SQLite for storage.

**Current build: Phases 0–3 on SQLite.** The Flask room and separate worker run
planning → scouting → extraction → cohorts → three debate rounds → moderator checks
→ at most one scored question → a full verdict, with visitor memory and a story cache.
Thin evidence returns an explicit low-precedent result rather than a fabricated debate.
See [Checkpoint 3](docs/CHECKPOINT_3.md) for what is verified and what is not.

## Setup

Use Python 3.11 or 3.12 (the macOS system Python 3.9 is too old).

```sh
python -m venv .venv
```

macOS/Linux:

```sh
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Windows Command Prompt: `.venv\Scripts\activate.bat`.

```sh
pip install -r requirements.txt
```

The supplied `precedent.env` has been copied to `.env` locally. Both files are
ignored by Git and restricted to the file owner. On another machine, copy
`.env.example` to `.env` and fill it privately. Never paste keys into source code.

Set `DB_BACKEND=sqlite` and `SQLITE_PATH=data/precedent.db`. The spec's standard
limits are 4 Featherless concurrency units and 8192 context tokens; configure them
to match your plan. Supply a strong random `FLASK_SECRET_KEY` for signed sessions.
Supabase credentials are optional until the explicitly requested backend switch.

To switch: paste `supabase/migrations/001_init.sql` into the Supabase SQL editor,
add `SUPABASE_SERVICE_ROLE_KEY` to `.env`, set `DB_BACKEND=supabase`, then run
`python scripts/check_supabase.py` and `python scripts/copy_to_supabase.py`. Every
table has row level security enabled with no policies, so only the server-side
service role reaches the data. The backend is written but not live-tested; if
anything fails, set `DB_BACKEND=sqlite` and demo on SQLite.

## Checkpoint 0 checks

```sh
python scripts/check_env.py
pytest -q
python scripts/check_models.py
python scripts/smoke_apify.py
python scripts/check_embeddings.py
```

Use `python scripts/smoke_apify.py --read-last` to inspect existing runs without
launching or paying for new actor runs.

Environment checks print only set/missing. Model checks use one output token per
candidate and save sanitized metadata to `engine/models_resolved.json`. They stop
after two consecutive service failures. Apify checks launch small paid actor runs
(20 Reddit items and one web result) and save counts/field names, never raw items,
to ignored `data/apify_smoke.json`. Embeddings download the public BGE model into
ignored `data/` on the first run; no Hugging Face key is needed.
The embedding check stops after 120 seconds if download or loading stalls.
See [the checkpoint report](docs/CHECKPOINT_0.md) for verified results and blockers.

The SQLite store creates its tables on startup, uses WAL, and opens one connection
per thread. Worker claims, answer submission, and turn numbering are transactional.
Math checks cover factual versus values disagreements and recommendation flips.
Synthetic test records live only in temporary databases, never demo councils.

## Run the app

Use separate terminals with the virtual environment active:

```sh
flask --app app --debug run --port 5050
python -m engine.worker
```

Open **http://localhost:5050**. The header shows whether the worker is connected.

End-to-end check (calls real services):

```sh
python scripts/smoke_e2e.py "Should I leave my stable job for a startup offer?" --featured
```

`--featured` makes this completed example readable from the home page. Omit it
for private runs. Model calls and actor runs consume your existing service allowance.

`--fresh` skips the 24-hour story cache and scrapes again, which is what you want
when calibrating retrieval; without it a repeated question reuses the previous
council's stories. `--answer middle` answers any generated question automatically.

Set `PRECEDENT_AUDIT=1` to write `data/mine_audit.json`, recording why each
candidate was kept or rejected. Extraction is the narrowest stage in the pipeline
and this is the only way to see which gate discarded a candidate:

```sh
PRECEDENT_AUDIT=1 python scripts/smoke_e2e.py "<dilemma>" --fresh
```

Seed both demo councils with `python scripts/demo_seed.py`.
Stop both processes with Ctrl+C. The SQLite database preserves completed councils.
The web process must never start pipeline threads. Port 5050 avoids the usual macOS
port 5000 conflict. Once the web app is running, an optional phone preview is
`cloudflared tunnel --url http://localhost:5050`; install cloudflared separately.

## API references

- [Featherless chat completions](https://featherless.ai/docs/completions)
- [Reddit Scraper Lite inputs](https://apify.com/trudax/reddit-scraper-lite/input-schema)
- [RAG Web Browser inputs](https://apify.com/apify/rag-web-browser/input-schema)
- [Apify Python actor client](https://docs.apify.com/api/client/python/reference/class/ActorClient)

## What is implemented

- Server-rendered home/room, one-second polling, cohort meters and source dialogs.
- Signed visitor sessions, request size limits, CSRF tokens, and council ownership checks.
- A worker with two council slots, eight shared task slots, and weighted model scheduling.
- Validated model JSON with one repair, verified-model fallbacks, and safe failure notices.
- BGE relevance filtering and exact source-passage checks before extracted stories are retained.
- Computed beliefs/priorities/stances, three debate rounds, own-cohort citations, and evidence-gated belief updates.
- Moderator swap tests, pooled data checks, up to two bounded web verifications,
  fresh-evidence belief resets, and stopping rules.
- At most one question, chosen by scoring whether any answer would change the
  recommendation, with answer submission restricted to the council's own visitor.
- Full verdict with crowd-vs-you, crux, dissent, cheap test, receipts and metrics;
  an explicit low-precedent outcome and retry controls when evidence is thin.
- Visitor profiles that prefill situations and a 24-hour story cache that only
  reuses a council whose extraction taxonomy still matches and which gathered
  enough stories to seat cohorts.

**Live retrieval is the current limitation.** Reddit search returns few distinct
threads for these decisions, so runs have been ending with too few stories to seat
two cohorts and returning the low-precedent verdict. Candidates are ranked against
the plan's outcome-seeking queries rather than the question alone, fallback results
are restricted to Reddit threads, and fiction subreddits are excluded, but breadth
remains thin. Audit a run before changing any of this.

An exact source passage confirms provenance but does not independently prove that an
account is true or that its interpretation is correct. Online stories are not a
representative sample. Confidence describes model preference, not the probability of
success.

The currently verified models cover Qwen and Mistral families. Llama/Gemma access
is gated; the worker uses the available models and discloses repeated families.
Run `python scripts/check_models.py` after access is enabled to refresh the selection.

Read [docs/SPEC.md](docs/SPEC.md) for the complete phased build and checkpoint rules.
