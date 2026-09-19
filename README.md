# Precedent

A decision council grounded in real stories. Python 3.11/3.12, Flask and
vanilla JavaScript; Featherless for models, Apify for sources, SQLite for storage.

**Current build: Phase 0.** Configuration, SQLite storage, decision math and live
service checks are implemented. The Flask UI and worker pipeline start in Phase 1,
after the checkpoint required by [the spec](docs/SPEC.md).

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

## Commands for Phase 1 (not implemented yet)

Use separate terminals with the virtual environment active:

```sh
flask --app app --debug run --port 5050
python -m engine.worker
```

Future end-to-end check: `python scripts/smoke_e2e.py "Should I leave my stable job for a startup offer?"`.
The web process must never start pipeline threads. Port 5050 avoids the usual macOS
port 5000 conflict. Once the web app is running, an optional phone preview is
`cloudflared tunnel --url http://localhost:5050`; install cloudflared separately.

## API references

- [Featherless chat completions](https://featherless.ai/docs/completions)
- [Reddit Scraper Lite inputs](https://apify.com/trudax/reddit-scraper-lite/input-schema)
- [RAG Web Browser inputs](https://apify.com/apify/rag-web-browser/input-schema)
- [Apify Python actor client](https://docs.apify.com/api/client/python/reference/class/ActorClient)

Read [docs/SPEC.md](docs/SPEC.md) for the complete phased build and checkpoint rules.
