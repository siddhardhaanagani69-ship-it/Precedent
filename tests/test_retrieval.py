"""Calibration for source selection, using the real embedding model.

Checkpoint 1's live run kept 22 candidates and extracted zero stories. Scoring
candidates against the question alone was ranking restatements of the dilemma
and generic advice above the short first-person outcome reports a council needs.
These tests pin the corrected behaviour to the actual model, because a mocked
embedder cannot catch a regression in that ranking.
"""

from types import SimpleNamespace

import pytest

from engine.config import MIN_SOURCE_SIMILARITY
from engine.stages.scout import intent_scores, relevant_candidates

QUESTION = "Should I leave my stable job for a startup offer?"
QUERIES = ["left stable job startup regret", "joined startup glad I did",
           "quit corporate job for startup one year later", "startup offer update worth it",
           "regret leaving big company for startup", "took startup offer looking back"]

# Labelled by hand. Outcomes report a lived result; the rest are advice, a
# restatement of the same dilemma, or thread noise.
OUTCOMES = [
    "I did this in 2019 and regret it. Equity was worthless.",
    "Best decision of my life, no regrets at all",
    "Glad I did it. Learned more in a year than five at my old job.",
    "I took the offer, got laid off eight months later, wish I'd stayed put honestly.",
]
OTHERS = [
    "Startups are a scam, stay where you are",
    "Depends on your savings honestly",
    "Should I take a startup job? I have an offer from a Series B company. Thoughts?",
    "This.",
]


@pytest.fixture(scope="module")
def ctx():
    embeddings = pytest.importorskip("engine.embeddings")
    try:
        embedder = embeddings.Embeddings()
    except Exception:
        pytest.skip("Embedding model is not available on this machine")
    return SimpleNamespace(council={"question": QUESTION}, embedder=embedder)


def test_outcome_stories_survive_the_relevance_floor(ctx):
    """Every labelled outcome clears the floor that the old scoring dropped it under."""
    candidates = [{"text": text} for text in OUTCOMES]
    scores = intent_scores(ctx, {"search_queries": QUERIES}, candidates)
    assert min(scores) >= MIN_SOURCE_SIMILARITY, dict(zip(OUTCOMES, scores))

    # The superseded behaviour: plain cosine against the question, no outcome signal.
    question = ctx.embedder.embed([QUESTION])[0]
    previous = ctx.embedder.embed(OUTCOMES) @ question
    assert min(previous) < MIN_SOURCE_SIMILARITY, "question-only cosine no longer loses outcomes"


def test_selection_prefers_outcomes_and_returns_a_usable_set(ctx):
    plan = {"search_queries": QUERIES}
    candidates = [{"text": text} for text in OUTCOMES + OTHERS]
    kept = {c["text"] for c in relevant_candidates(ctx, plan, candidates, keep=6)}
    assert set(OUTCOMES) <= kept
    assert "This." not in kept


def test_selection_never_returns_nothing(ctx):
    """Thin, weakly-matching input still reaches the extractor rather than failing."""
    plan = {"search_queries": QUERIES}
    candidates = [{"text": "Congrats!!"}, {"text": "Thanks for sharing this write-up."}]
    assert relevant_candidates(ctx, plan, candidates, keep=6)
