"""Checkpoint checks: decision math, persistence, and concurrent safety."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from engine.math_core import score_question, stance, swap_test
from store.sqlite_store import SQLiteStore

CONSEQUENCES = [
    {"id": "pay", "attribute": "money", "impact": 3},
    {"id": "layoff", "attribute": "stability", "impact": -3},
]
BELIEFS = {"A": {"pay": 0.9, "layoff": 0.8}, "B": {"pay": 0.2, "layoff": 0.1}}
GROWTH = {"money": 5, "stability": 1}
SECURITY = {"money": 1, "stability": 5}


def test_same_beliefs_is_values_disagreement():
    result = swap_test(BELIEFS, GROWTH, BELIEFS, SECURITY, CONSEQUENCES, "A")
    assert result["total"] > 0
    assert result["factual"] == pytest.approx(0)
    assert result["values"] == pytest.approx(result["total"])


def test_same_weights_is_factual_disagreement():
    reversed_beliefs = {"A": BELIEFS["B"], "B": BELIEFS["A"]}
    result = swap_test(BELIEFS, GROWTH, reversed_beliefs, GROWTH, CONSEQUENCES, "A")
    assert result["total"] > 0
    assert result["factual"] == pytest.approx(result["total"])
    assert result["values"] == pytest.approx(0)


def test_question_flip_and_no_change():
    current = stance(BELIEFS, GROWTH, CONSEQUENCES)
    other = stance(BELIEFS, SECURITY, CONSEQUENCES)
    result = score_question(current, [current, other])
    assert result["flips"] and result["ask"]
    assert not score_question(current, [current])["ask"]
    assert not score_question({"A": 0.51, "B": 0.49}, [{"A": 0.5, "B": 0.5}])["flips"]


def test_stance_stability_and_input_validation():
    result = stance(BELIEFS, GROWTH, CONSEQUENCES, temperature=0.00001)
    assert sum(result.values()) == pytest.approx(1)
    with pytest.raises(ValueError):
        stance(BELIEFS, GROWTH, CONSEQUENCES, temperature=0)
    with pytest.raises(ValueError):
        stance({"A": {"pay": float("nan"), "layoff": 0.2}}, GROWTH, CONSEQUENCES)
    with pytest.raises(ValueError):
        score_question({"A": 0.7, "B": 0.3}, [{"A": 0.2}])


def test_atomic_claims_and_turns(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    council = store.create_council("visitor", "Should I take the startup offer?")

    def claim(_):
        try:
            return store.claim_next_council()
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(claim, range(16)))
    assert len([c for c in claims if c]) == 1
    assert store.get_council(council["id"])["status"] == "planning"

    def insert(i):
        try:
            return store.insert_turn(council["id"], kind="system", message=str(i))
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(insert, range(16)))
    assert [t["seq"] for t in store.get_turns(council["id"])] == list(range(1, 17))
    assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.close()


def test_answer_ownership_and_atomic_resume(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    cid = store.create_council("owner", "Should I change jobs this year?")["id"]
    store.insert_question(cid, text="What matters?", why="It may change the outcome",
                          candidate={"type": "values"}, answers=[{"id": "even", "label": "Both"}])
    store.update_council(cid, status="awaiting_user")
    with pytest.raises(PermissionError):
        store.insert_answer(cid, "stranger", "even")
    with pytest.raises(ValueError):
        store.insert_answer(cid, "owner", "unknown")
    assert store.get_answer(cid) is None
    assert store.get_council(cid)["status"] == "awaiting_user"
    store.insert_answer(cid, "owner", "even")
    assert store.get_council(cid)["status"] == "answered"
    with pytest.raises(ValueError):
        store.insert_answer(cid, "owner", "even")
    assert store.claim_next_council()["status"] == "finalizing"
    store.close()


def test_story_batch_rollback_and_memory(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    cid = store.create_council("owner", "Should I change jobs this year?")["id"]
    story = {"label": "S1", "source": "test", "url": "https://example.com/story",
             "option_id": "A", "outcome": "glad", "summary": "Synthetic test fixture",
             "similarity": 0.8, "context": {"savings": "over 6"}, "reasons": [], "embedding": [0.0, 1.0]}
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_stories(cid, [story, story])
    assert store.get_stories(cid) == []
    store.insert_stories(cid, [story])
    assert store.get_stories(cid)[0]["context"] == {"savings": "over 6"}
    store.save_profile("owner", {"money": 3}, {"savings": "over 6"})
    store.save_profile("owner", {"stability": 5}, {"job": "engineer"})
    assert store.get_profile("owner")["weights"] == {"money": 3, "stability": 5}
    assert store.get_profile("owner")["facts"]["savings"] == "over 6"
    store.heartbeat()
    assert store.get_heartbeat()
    store.update_council(cid, status="mining", claimed_at="2000-01-01T00:00:00+00:00")
    assert store.cleanup_stale("2001-01-01T00:00:00+00:00") == 1
    assert store.get_council(cid)["error"] == "worker restarted"
    store.close()


def test_content_roundtrip_and_foreign_agent_rejected(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    cid = store.create_council("owner", "Should I change jobs this year?")["id"]
    other = store.create_council("other", "Should I stay at my current job?")["id"]
    agent = {"cohort_key": "A:glad", "name": "Changed jobs", "persona": "Test persona",
             "model": "test-model", "family": "test", "color": "#234567", "story_count": 5,
             "weights": GROWTH, "beliefs": BELIEFS, "stance": {"A": 0.7, "B": 0.3}}
    saved = store.upsert_agent(cid, agent)
    updated = store.upsert_agent(cid, {**agent, "story_count": 6})
    assert saved["id"] == updated["id"]
    assert store.get_agents(cid)[0]["story_count"] == 6
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_turn(other, agent_id=saved["id"], kind="argument", message="Wrong council")
    evidence = store.insert_evidence(cid, kind="datacheck", title="Pooled test data",
                                    body="Synthetic test fixture", option_id="A", consequence_id="pay", value=0.5, n=10)
    assert evidence["label"] == "E1" and store.get_evidence(cid)[0]["n"] == 10
    store.insert_verdict(cid, recommendation="A", confidence=0.7, summary="Test only",
                         crux="Test crux", cheap_test="Ask someone", receipts=[{"label": "E1"}])
    assert store.get_verdict(cid)["receipts"] == [{"label": "E1"}]
    store.update_council(cid, is_featured=True, status="done", question_embedding=[1.0, 0.0])
    assert store.list_featured()[0]["id"] == cid
    assert store.find_recent_councils("2000-01-01T00:00:00+00:00")[0]["id"] == cid
    with pytest.raises(ValueError):
        store.update_council(cid, **{"status = 'done'; --": "bad"})
    store.close()


def test_reddit_normalization_privacy_dedupe_and_comments():
    from engine.apify_tools import normalize_reddit_item, normalize_reddit_items

    item = {"dataType": "post", "title": "My experience", "body": "I am alice. u/bob agreed.",
            "username": "alice", "url": "https://reddit.com/r/jobs/comments/123/story?tracking=1",
            "comments": [{"body": "I regret switching jobs.", "author": "carol"}]}
    results = normalize_reddit_items([item, item])
    assert len(results) == 2
    assert results[1]["kind"] == "comment"
    assert results[1]["url"] == results[0]["url"]
    assert "alice" not in results[0]["text"] and "bob" not in results[0]["text"]
    assert "username" not in results[0] and "author" not in results[1]
    assert "?" not in results[0]["url"]
    assert normalize_reddit_item({"body": "profile", "url": "https://reddit.com/user/alice"}) is None
    assert normalize_reddit_item({"body": "bad", "url": "javascript:alert(1)"}) is None
    assert normalize_reddit_item({"body": "[deleted]", "url": item["url"]}) is None


def test_apify_long_poll_timeout_and_sanitized_failure(monkeypatch):
    import engine.apify_tools as apify

    captured = {}

    class FakeClient:
        def __init__(self, token, **options):
            captured.update(options)

        def actor(self, actor_id):
            return self

        def call(self, **options):
            captured["call"] = options
            return {"status": "SUCCEEDED", "defaultDatasetId": "fixture"}

        def dataset(self, dataset_id):
            return self

        def iterate_items(self):
            return iter([{"text": "Synthetic test fixture"}])

    monkeypatch.setattr(apify, "ApifyClient", FakeClient)
    monkeypatch.setattr(apify, "require_keys", lambda *names: None)
    assert len(apify.run_actor("test/actor", {}, 45)) == 1
    assert captured["timeout_secs"] > captured["call"]["wait_secs"]
    assert captured["call"]["timeout_secs"] == 45
    assert captured["call"]["logger"] is None

    def fail(self, **options):
        raise RuntimeError("simulated-sensitive-upstream-content")

    monkeypatch.setattr(FakeClient, "call", fail)
    with pytest.raises(RuntimeError) as error:
        apify.run_actor("test/actor", {}, 45)
    assert "simulated-sensitive" not in str(error.value)

    monkeypatch.setattr(FakeClient, "call", lambda self, **options: {"status": "TIMED-OUT", "defaultDatasetId": "fixture"})
    assert len(apify.run_actor("test/actor", {}, 45, partial=True)) == 1
    with pytest.raises(RuntimeError):
        apify.run_actor("test/actor", {}, 45)


def test_embedding_check_has_a_process_deadline(monkeypatch, capsys):
    import runpy
    import subprocess
    import sys
    from pathlib import Path

    def timeout(command, **options):
        assert command[-1] == "--worker"
        assert options["timeout"] == 120
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    monkeypatch.setattr(sys, "argv", ["check_embeddings.py"])
    script = Path(__file__).resolve().parents[1] / "scripts/check_embeddings.py"
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(script), run_name="__main__")
    assert result.value.code == 1
    assert "exceeded 120 seconds" in capsys.readouterr().out
