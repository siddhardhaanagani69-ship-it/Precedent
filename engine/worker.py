"""Separate worker process. Flask communicates with it only through Store."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from engine.config import require_keys
from engine.embeddings import Embeddings
from engine.llm import CouncilLLM, resolved_models
from engine.stages import plan, scout, mine, cohorts, debate, verdict, values
from store import get_store

log = logging.getLogger("precedent")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class CouncilContext:
    def __init__(self, store, council: dict, pool, embedder):
        self.store, self.council, self.pool, self.embedder = store, council, pool, embedder
        self.cid = council["id"]
        self.metrics = dict(council["progress"])
        self.metrics["started_at"] = self.metrics.get("started_at") or timestamp()
        self.metrics.setdefault("stage_timings", {})
        self.lock = threading.RLock()
        self.last_write = 0.0
        self.valid_cites = self.total_cites = 0
        self.llm = CouncilLLM(self.count_call, self.notice)

    def progress(self, force=False, **updates):
        with self.lock:
            self.metrics.update(updates)
            if force or time.monotonic() - self.last_write >= 0.5:
                self.store.update_council(self.cid, progress=self.metrics.copy())
                self.last_write = time.monotonic()

    def count_call(self):
        with self.lock:
            self.progress(llm_calls=self.metrics["llm_calls"] + 1)

    def citations(self, valid, total):
        with self.lock:
            self.valid_cites += valid
            self.total_cites += total
            self.progress(valid_citation_pct=100 * self.valid_cites / self.total_cites if self.total_cites else None)

    def first_argument(self):
        if not self.metrics["first_argument_at"]:
            self.progress(first_argument_at=timestamp())

    def notice(self, message):
        self.store.insert_turn(self.cid, kind="system", message=message)

    def stage(self, status, function, *args):
        started = time.monotonic()
        self.store.update_council(self.cid, status=status)
        self.progress(force=True)
        log.info("[council %s] %s started", self.cid[:4], status)
        result = function(self, *args)
        elapsed = round(time.monotonic() - started, 2)
        self.metrics["stage_timings"][status] = elapsed
        self.progress(force=True)
        log.info("[council %s] %s done in %.2fs, %s stories kept", self.cid[:4], status, elapsed, self.metrics["stories_kept"])
        return result


def run_council(store, council: dict, pool, embedder) -> None:
    ctx = None
    try:
        ctx = CouncilContext(store, council, pool, embedder)
        if council["status"] == "finalizing":
            ctx.stage("finalizing", verdict.run, council["plan"], store.get_stories(ctx.cid), store.get_agents(ctx.cid))
            return
        ctx.notice("This council compares firsthand accounts, checks disputed beliefs, and may ask one question if your answer could change its recommendation.")
        decision_plan = ctx.stage("planning", plan.run)
        candidates = ctx.stage("scouting", scout.run, decision_plan)
        stories = ctx.stage("mining", mine.run, decision_plan, candidates)
        agents = ctx.stage("forming", cohorts.run, decision_plan, stories)
        if agents:
            agents = ctx.stage("debating", debate.run, decision_plan, stories, agents)
            if values.run(ctx, decision_plan, stories, agents):
                return
        ctx.stage("finalizing", verdict.run, decision_plan, stories, agents)
    except Exception as exc:
        # Raw exceptions can contain upstream request data or credentials.
        message = "The council could not finish this run. Your stored sources are safe; please run again."
        if isinstance(exc, RuntimeError) and str(exc).startswith("The planner could not"):
            message = str(exc)
        store.insert_turn(council["id"], kind="system", message=message)
        store.update_council(council["id"], status="failed", error=message, finished_at=timestamp())
        log.error("[council %s] failed (%s)", council["id"][:4], type(exc).__name__)
    finally:
        if ctx:
            ctx.progress(force=True)
            ctx.llm.client.close()
        store.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    require_keys("FEATHERLESS_API_KEY", "APIFY_TOKEN")
    resolved_models()
    try:
        embedder = Embeddings()
    except Exception:
        raise SystemExit("Embedding model is not ready. Run python scripts/check_embeddings.py; do not change libraries without approval.") from None
    store = get_store()
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    store.cleanup_stale(stale)
    log.info("Worker ready: SQLite queue, two council slots, eight shared task slots.")
    with ThreadPoolExecutor(max_workers=8) as tasks, ThreadPoolExecutor(max_workers=2) as councils:
        active = set()
        last_heartbeat = 0
        try:
            while True:
                if time.monotonic() - last_heartbeat >= 5:
                    store.heartbeat()
                    last_heartbeat = time.monotonic()
                for future in list(active):
                    if future.done():
                        active.remove(future)
                        future.result()
                while len(active) < 2:
                    council = store.claim_next_council()
                    if not council:
                        break
                    active.add(councils.submit(run_council, store, council, tasks, embedder))
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Worker stopping after active councils finish.")
        finally:
            store.close()


if __name__ == "__main__":
    main()
