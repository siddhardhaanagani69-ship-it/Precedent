"""Probe Featherless candidates; persist only safe status and model metadata."""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.config import FEATHERLESS_API_KEY, LIMITS, MODEL_ROLES, ROOT, require_keys
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI


def main() -> int:
    require_keys("FEATHERLESS_API_KEY")
    client = OpenAI(base_url="https://api.featherless.ai/v1", api_key=FEATHERLESS_API_KEY,
                    max_retries=0, timeout=LIMITS["llm_timeout"])
    candidates = {m["id"]: m for role in MODEL_ROLES.values() for m in role}
    checks = []
    consecutive_failures = 0
    for candidate in candidates.values():
        started = time.monotonic()
        error = None
        for attempt in range(2):
            try:
                client.chat.completions.create(
                    model=candidate["id"], messages=[{"role": "user", "content": "Say OK."}],
                    max_tokens=1, temperature=0,
                )
                error = None
                consecutive_failures = 0
                break
            except (APIStatusError, APIConnectionError, APITimeoutError) as exc:
                status = getattr(exc, "status_code", None)
                # Do not print raw exceptions: HTTP bodies can echo credentials.
                error = f"HTTP {status}" if status else type(exc).__name__
                consecutive_failures += 1
                retryable = status is None or status == 429 or status >= 500
                if consecutive_failures >= 2 or not retryable or attempt == 1:
                    break
                time.sleep(2)
        check = {**candidate, "ok": error is None, "latency_seconds": round(time.monotonic() - started, 2), "error": error}
        checks.append(check)
        print(f"{candidate['id']}: {'ok' if check['ok'] else error} ({check['latency_seconds']}s)", flush=True)
        if consecutive_failures >= 2:
            print("Stopped: Featherless failed twice consecutively. Remaining candidates were not tested.")
            break
    client.close()
    working = {m["id"]: m for m in checks if m["ok"]}
    resolved = {"checked_at": datetime.now(timezone.utc).isoformat(), "checks": checks}
    agents = [m for m in MODEL_ROLES["agents"] if m["id"] in working]
    # Fill missing families from other working candidates, using distinct model IDs.
    for m in working.values():
        if len(agents) < 4 and m["id"] not in {a["id"] for a in agents}:
            agents.append({k: m[k] for k in ("id", "family", "weight")})
    resolved["agents"] = agents
    for role in ("extractor", "moderator"):
        resolved[role] = next((m for m in MODEL_ROLES[role] if m["id"] in working), None)
    if not resolved["moderator"] and agents:
        resolved["moderator"] = agents[0]
    resolved["ready"] = bool(resolved["extractor"] and resolved["moderator"] and len(agents) >= 4)
    if len({m["family"] for m in agents}) < 4:
        print("Fewer than four model families verified; distinct working models will reuse families where available.")
    destination = ROOT / "engine/models_resolved.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(resolved, indent=2) + "\n")
    temporary.replace(destination)
    return 0 if resolved["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
