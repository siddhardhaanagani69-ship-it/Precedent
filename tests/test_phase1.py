"""Boundaries that matter: citations, JSON, queue ownership and the runnable spine."""

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest

from app import create_app
from engine.llm import CouncilLLM, parse_json
from engine.math_core import stance
from engine.scheduler import Scheduler
from engine.schemas import Argument, Plan
from engine.stages.cohorts import fact_table
from engine.stages.debate import gate_argument
from store.sqlite_store import SQLiteStore


def decision_plan():
    return {"title": "A job choice", "options": [{"id": "A", "label": "Join startup"}, {"id": "B", "label": "Stay"}],
            "attributes": ["money", "stability", "growth", "stress"],
            "consequences": [{"id": f"c{i}", "label": f"Outcome {i}", "attribute": "money" if i % 2 else "stability",
                              "impact": 2 if i % 2 else -2, "checkable_online": False} for i in range(1, 7)],
            "situational": [{"key": "savings", "label": "Savings", "values": ["low", "high"], "user_value": None},
                            {"key": "family", "label": "Dependents", "values": ["yes", "no"], "user_value": None}],
            "user_summary": "Considering a new job", "search_queries": [f"job update {i}" for i in range(6)]}


def test_json_parser_and_single_repair(monkeypatch):
    assert parse_json('<think>{"ignore":true}</think>```json\n{"text":"a } brace"}\n```') == {"text": "a } brace"}
    with pytest.raises(ValueError):
        parse_json("No result")
    calls = []
    llm = CouncilLLM(lambda: None, lambda text: None)

    def complete(*args, **kwargs):
        calls.append(args)
        return "invalid" if len(calls) == 1 else '{"message":"Recovered","claims":[],"belief_changes":[]}'

    monkeypatch.setattr(llm, "complete", complete)
    assert llm.structured("agents", Argument, "Test", {}, fallback=None)["message"] == "Recovered"
    assert len(calls) == 2
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "bad")
    assert llm.structured("agents", Argument, "Test", {}, fallback="safe") == "safe"
    llm.client.close()


def test_plan_references_and_fact_table_use_all_outcomes():
    plan = decision_plan()
    assert Plan.model_validate(plan)
    invalid = copy.deepcopy(plan)
    invalid["consequences"][0]["attribute"] = "not-a-real-attribute"
    with pytest.raises(ValueError):
        Plan.model_validate(invalid)
    stories = [{"option_id": "A", "outcome": "glad", "similarity": 1, "reasons": [{"consequence_id": "c1"}, {"consequence_id": "c1"}]},
               {"option_id": "A", "outcome": "regret", "similarity": 0.5, "reasons": []}]
    table = fact_table(plan, stories)
    assert table["A"]["c1"] == {"p": 0.6, "n": 2, "mentions": 1}


def test_citations_and_fresh_matching_evidence_gate():
    agent = {"cohort_key": "A:glad", "beliefs": {"A": {"c1": 0.5}}}
    stories = [{"label": "S1", "option_id": "A", "outcome": "glad"}, {"label": "S2", "option_id": "B", "outcome": "regret"}]
    evidence = [{"label": "E1", "created_at": "2026-09-19T18:00:01+00:00", "option_id": "A", "consequence_id": "c1"}]
    output = {"message": "Own [S1], foreign [S2], unknown [S99], grouped [S1, S888]", "claims": [{"text": "Test", "cites": ["S1", "S2"]}],
              "belief_changes": [{"option_id": "A", "consequence_id": "c1", "new_p": 0.8, "cites": ["E1"], "reason": "Test"}]}
    clean, rejected, valid, total = gate_argument(output, agent, stories, evidence, 1, None)
    assert "[S2]" not in clean["message"] and "[S99]" not in clean["message"]
    assert "S888" not in clean["message"]
    assert clean["claims"][0]["cites"] == ["S1"] and rejected
    assert valid < total
    clean, rejected, _, _ = gate_argument(output, agent, stories, evidence, 2, "2026-09-19T18:00:00+00:00")
    assert len(clean["belief_changes"]) == 1 and not rejected
    assert gate_argument(output, agent, stories, evidence, 2, evidence[0]["created_at"])[1]
    evidence[0]["consequence_id"] = "c2"
    assert gate_argument(output, agent, stories, evidence, 2, "2026-09-19T18:00:00+00:00")[1]


def test_weighted_scheduler_priorities_and_release():
    scheduler = Scheduler(2)
    order = []

    def acquire(name):
        with scheduler.slot(1, name):
            order.append(name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with scheduler.slot(2, "bulk"):
            bulk = pool.submit(acquire, "bulk")
            debate = pool.submit(acquire, "debate")
            # Wait for both threads to register while capacity is fully held.
            with scheduler.condition:
                assert scheduler.condition.wait_for(lambda: len(scheduler.waiters) == 2, timeout=2)
        bulk.result(timeout=2)
        debate.result(timeout=2)
    assert order == ["debate", "bulk"]
    with pytest.raises(RuntimeError):
        with scheduler.slot(2, "verify"):
            raise RuntimeError("Test release")
    assert scheduler.used == 0


def test_web_ownership_csrf_validation_and_state_privacy(tmp_path):
    store = SQLiteStore(tmp_path / "web.db")
    app = create_app({"TESTING": True, "SECRET_KEY": "test-session-key", "STORE": store})
    owner, stranger = app.test_client(), app.test_client()
    assert owner.get("/").status_code == 200
    with owner.session_transaction() as session:
        csrf = session["csrf"]
    assert owner.post("/councils", data={"question": "Should I change jobs?"}).status_code == 403
    assert owner.post("/councils", data={"csrf": csrf, "question": "short"}).status_code == 400
    result = owner.post("/councils", data={"csrf": csrf, "question": "Should I change jobs?"})
    cid = result.location.rsplit("/", 1)[1]
    state = owner.get(f"/api/councils/{cid}/state").json
    assert state["council"]["status"] == "queued"
    assert "visitor_id" not in state["council"] and "question_embedding" not in state["council"]
    assert stranger.get(f"/api/councils/{cid}/state").status_code == 404
    assert stranger.get(f"/api/councils/{cid}/cite/S1").status_code == 404
    store.update_council(cid, is_featured=True)
    assert stranger.get(f"/c/{cid}").status_code == 200
    with stranger.session_transaction() as session:
        stranger_csrf = session["csrf"]
    assert stranger.post(f"/api/councils/{cid}/answer", json={"answer_id": "x"}, headers={"X-CSRF-Token": stranger_csrf}).status_code == 404
    assert owner.get("/api/health").json["worker_online"] is False
    store.heartbeat()
    assert owner.get("/api/health").json["worker_online"] is True
    store.close()


@pytest.mark.parametrize("candidate_count,expected_agents", [(24, 4), (0, 0)])
def test_pipeline_spine_and_low_precedent(tmp_path, monkeypatch, candidate_count, expected_agents):
    from engine import worker

    class FakeLLM:
        def __init__(self, on_call, notice):
            self.on_call = on_call
            self.models = {"agents": [{"id": "test-model", "family": "test-family", "weight": 1}]}
            self.client = SimpleNamespace(close=lambda: None)

        def structured(self, role, schema, instruction, context, *, fallback, **kwargs):
            self.on_call()
            if schema.__name__ == "Plan":
                return decision_plan()
            if schema.__name__ == "Extraction":
                rows = []
                for c in context["candidates"]:
                    i = int(c["text"].split()[-1])
                    rows.append({"idx": c["idx"], "relevant": True, "option_id": "A" if i % 4 < 2 else "B",
                        "outcome": "glad" if i % 2 else "regret", "months_after": 12, "context": {},
                        "summary": "A synthetic test outcome.", "evidence_quote": c["text"], "reasons": [{"text": "Test reason", "consequence_id": "c1" if i % 2 else "c2",
                        "attribute": "money" if i % 2 else "stability", "valence": 1 if i % 2 else -1}]})
                return {"stories": rows}
            return fallback

    class FakeEmbeddings:
        def embed(self, texts):
            return np.tile(np.ones(384) / np.sqrt(384), (len(texts), 1))

    monkeypatch.setattr(worker, "CouncilLLM", FakeLLM)
    monkeypatch.setattr(worker.scout, "run", lambda ctx, plan: [{"text": f"I changed my job and this is a synthetic test fixture {i}", "source": "test",
        "url": f"https://example.com/test/{i}"} for i in range(candidate_count)])
    store = SQLiteStore(tmp_path / "pipeline.db")
    other = store.create_council("other", "Should I move somewhere else?")
    council = store.create_council("visitor", "Should I change jobs this year?")
    claimed = store.claim_next_council(council["id"])
    assert claimed["id"] == council["id"] and store.get_council(other["id"])["status"] == "queued"
    with ThreadPoolExecutor(max_workers=8) as pool:
        worker.run_council(store, claimed, pool, FakeEmbeddings())
    final = store.get_council(council["id"])
    assert final["status"] == "done"
    agents = store.get_agents(council["id"])
    assert len(agents) == expected_agents
    turns = store.get_turns(council["id"])
    assert len([t for t in turns if t["kind"] == "argument"]) == expected_agents * 3
    assert store.get_question(council["id"]) is None
    verdict = store.get_verdict(council["id"])
    if expected_agents:
        assert 0 <= verdict["confidence"] <= 1
        for agent in agents:
            assert agent["stance"] == stance(agent["beliefs"], agent["weights"], final["plan"]["consequences"])
        assert verdict["receipts"] and final["progress"]["valid_citation_pct"] == 100
    else:
        assert verdict["confidence"] is None and verdict["recommendation"] == "Not enough precedent"
    store.close()


def test_extraction_requires_verbatim_support():
    from engine.stages.mine import grounded_quote
    assert grounded_quote("I joined a startup and regretted the long hours.", "Last year, I joined a startup and regretted the long hours. I left.")
    assert not grounded_quote("I joined a startup and got a promotion.", "A completely unrelated source post about flowers.")
    assert not grounded_quote("I joined", "I joined something unrelated.")


def scout_ctx(vectors):
    """One intent (the question) plus one vector per candidate, in call order."""
    calls = iter(vectors)
    return SimpleNamespace(council={"question": "Job choice"},
                           embedder=SimpleNamespace(embed=lambda texts: np.array(next(calls))))


def test_source_relevance_and_outcome_heuristic():
    from engine.stages.scout import relevant_candidates, outcome_candidates
    plan = {"search_queries": []}
    ctx = scout_ctx([[[1., 0.]], [[0.8, 0.6], [0.2, 0.98]]])
    candidates = [{"text": "I regret the job change."}, {"text": "Unrelated results"}]
    # Only the first clears the floor; the second is below the absolute junk floor.
    assert relevant_candidates(ctx, plan, candidates, 10) == candidates[:1]
    assert outcome_candidates(candidates) == 1


def test_outcome_intents_rank_above_question_restatements():
    """A lived outcome must outrank a restatement of the same dilemma."""
    from engine.stages.scout import relevant_candidates
    plan = {"search_queries": ["startup regret one year later"]}
    # Question vector, then the outcome-query vector, then the two candidates.
    ctx = scout_ctx([[[1., 0.], [0., 1.]], [[0.1, 0.99], [0.9, 0.44]]])
    outcome = {"text": "Glad I did it, best year of my career."}
    restatement = {"text": "Should I take the startup offer? Thoughts?"}
    assert relevant_candidates(ctx, plan, [outcome, restatement], 10)[0] == outcome


def test_floor_never_starves_extraction():
    """Below the floor, ranking still yields candidates instead of an empty run."""
    from engine.stages.scout import relevant_candidates
    plan = {"search_queries": []}
    ctx = scout_ctx([[[1., 0.]], [[0.5, 0.87], [0.48, 0.88], [0.05, 0.999]]])
    candidates = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    kept = relevant_candidates(ctx, plan, candidates, 10)
    # Two clear the 0.45 junk floor; the third does not and stays dropped.
    assert kept == candidates[:2]


def test_retyped_punctuation_keeps_a_faithful_quote():
    """Curly quotes and dashes must not discard a genuinely copied passage."""
    from engine.stages.mine import grounded_quote
    source = "I took the offer - it's been rough - but I don't regret leaving."
    retyped = "I took the offer — it’s been rough — but I don’t regret leaving."
    assert grounded_quote(retyped, source)
    # Folding punctuation must not let invented content through.
    assert not grounded_quote("I took the offer and immediately doubled my salary.", source)
