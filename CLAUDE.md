# Precedent
Python 3.11+, Flask/Jinja, vanilla JavaScript and CSS. No Node or build step.
Featherless only for LLMs; Apify only for scraping. SQLite first.
The web and worker are separate processes and communicate through Store.
Read docs/SPEC.md; implement in phases and stop at every checkpoint.
Setup: python -m venv .venv; activate; pip install -r requirements.txt
Web: flask --app app --debug run --port 5050
Worker: python -m engine.worker
Tests: pytest -q
Checks: python scripts/check_env.py; python scripts/check_models.py
Apify check: python scripts/smoke_apify.py
Load secrets only through engine/config.py using python-dotenv.
Never print, display, log, echo, or commit secret values, even partially.
Never display .env or precedent.env; checks print only set/missing.
Secrets stay server-side and never reach the browser.
Stances are computed from beliefs × weights; LLMs never set them.
Never block or crash the demo; degrade gracefully with a system turn.
No fabricated stories or statistics; numbers trace to stored data.
Keep Python plain and readable: functions, type hints, short docstrings.
Stop at every checkpoint and commit with a clear message.
Stop looping after an external service fails twice consecutively.
Report what failed and propose the fallback.
Learn from the spec and commands before asking questions.
Ask before changing architecture or adding dependencies beyond the spec.
Supabase switch only when the user says keys are in, never mid-phase.
