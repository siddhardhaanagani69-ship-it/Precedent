"""Exercise evidence updates and an actual answer/resume boundary offline."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest

from engine import worker
from engine.math_core import stance
from engine.stages import cohorts, debate, moderator, values
from store.sqlite_store import SQLiteStore
from test_phase1 import decision_plan


def setup_council(tmp_path):
    store = SQLiteStore(tmp_path / 'phase2.db')
    council = store.create_council('visitor', 'Should I choose the startup or stay?')
    council = store.claim_next_council(council['id'])
    plan = decision_plan()
    plan['consequences'] = [
        {'id': 'c1', 'label': 'More income', 'attribute': 'money', 'impact': 3, 'checkable_online': False},
        {'id': 'c2', 'label': 'More stability', 'attribute': 'stability', 'impact': 3, 'checkable_online': False}]
    stories = [{'label': f'S{i+1}', 'source': 'test', 'url': f'https://example.com/{i}',
                'option_id': 'A' if i < 20 else 'B', 'outcome': 'glad', 'similarity': 1.,
                'summary': 'Synthetic test account.', 'context': {},
                'reasons': [{'consequence_id': 'c1' if i < 20 else 'c2', 'attribute': 'money' if i < 20 else 'stability', 'text': 'Test reason', 'valence': 1}]}
               for i in range(40)]
    store.insert_stories(council['id'], stories)
    beliefs = {'A': {'c1': .9, 'c2': .1}, 'B': {'c1': .1, 'c2': .9}}
    agents = []
    for i, oid in enumerate(('A', 'B')):
        weights = {'money': 5 if i == 0 else 1, 'stability': 1 if i == 0 else 5, 'growth': 1, 'stress': 1}
        agents.append(store.upsert_agent(council['id'], {'cohort_key': f'{oid}:glad', 'name': oid, 'persona': 'Test',
            'model': 'test', 'family': 'test', 'color': '#225c71', 'story_count': 20, 'weights': weights,
            'beliefs': deepcopy(beliefs), 'stance': stance(beliefs, weights, plan['consequences']), 'prev_stance': {}}))
    plan['fact_table'] = cohorts.fact_table(plan, stories)
    plan['crowd_vs_you'] = {}
    store.update_council(council['id'], plan=plan)
    council['plan'] = plan
    return store, council, plan, stories, agents


class OfflineLLM:
    def __init__(self, *args):
        self.models = {'agents': [{'id': 'test', 'family': 'test', 'weight': 1}]}
        self.client = SimpleNamespace(close=lambda: None)

    def structured(self, *args, fallback, **kwargs):
        return fallback


def test_scored_question_answer_resume_and_memory(tmp_path, monkeypatch):
    store, council, plan, stories, agents = setup_council(tmp_path)
    monkeypatch.setattr(worker, 'CouncilLLM', OfflineLLM)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ctx = worker.CouncilContext(store, council, pool, None)
        assert values.run(ctx, plan, stories, agents)
        question = store.get_question(council['id'])
        assert question['candidate']['flips']
        assert len(question['answers']) == 3
        assert store.get_council(council['id'])['status'] == 'awaiting_user'
        with pytest.raises(PermissionError):
            store.insert_answer(council['id'], 'stranger', 'x')
        store.insert_answer(council['id'], 'visitor', 'x')
        resumed = store.claim_next_council(council['id'])
        assert resumed['status'] == 'finalizing'
        worker.run_council(store, resumed, pool, None)
    assert store.get_council(council['id'])['status'] == 'done'
    assert store.get_verdict(council['id'])['recommendation'] == 'Join startup'
    assert store.get_profile('visitor')['weights'] == {'money': 5, 'stability': 2}
    # Memory removes the meaningful values tradeoff from the next question pool.
    beliefs, weights = values.baseline(ctx, plan, stories, agents)
    assert not values.candidates(plan, stories, agents, beliefs, weights, store.get_profile('visitor'))


def test_moderator_pooled_check_resets_next_round(tmp_path, monkeypatch):
    store, council, plan, stories, agents = setup_council(tmp_path)
    monkeypatch.setattr(worker, 'CouncilLLM', OfflineLLM)
    for a in agents:
        a['weights'] = {'money': 3, 'stability': 3, 'growth': 1, 'stress': 1}
    agents[0]['beliefs'] = {'A': {'c1': .9, 'c2': .9}, 'B': {'c1': .1, 'c2': .1}}
    agents[1]['beliefs'] = {'A': {'c1': .1, 'c2': .1}, 'B': {'c1': .9, 'c2': .9}}
    for a in agents:
        a['stance'] = stance(a['beliefs'], a['weights'], plan['consequences'])
        store.upsert_agent(council['id'], a)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ctx = worker.CouncilContext(store, council, pool, None)
        debate.run(ctx, plan, stories, agents)
    evidence = store.get_evidence(council['id'])
    assert evidence and all(e['kind'] == 'datacheck' for e in evidence)
    e = evidence[0]
    assert e['n'] == 20 and '20 stored accounts' in e['body']
    assert all(a['beliefs'][e['option_id']][e['consequence_id']] == e['value'] for a in agents)
    assert any(a['prev_stance'] != a['stance'] for a in agents)
    assert any(t['kind'] == 'factcheck' for t in store.get_turns(council['id']))


def test_verification_rejects_blocked_pages_and_invented_passages(tmp_path, monkeypatch):
    store, council, plan, stories, agents = setup_council(tmp_path)
    monkeypatch.setattr(worker, 'CouncilLLM', OfflineLLM)
    ctx = worker.CouncilContext(store, council, None, None)
    monkeypatch.setattr(moderator, 'run_actor', lambda *a, **k: [{'crawl': {'requestStatus': 'failed'}, 'text': ''}])
    assert moderator.verify(ctx, plan, plan['options'][0], plan['consequences'][0]) is None
    monkeypatch.setattr(moderator, 'run_actor', lambda *a, **k: [{'metadata': {'url': 'https://example.com'}, 'text': 'This is unrelated source text.'}])
    ctx.llm.structured = lambda role, schema, *args, fallback, **kwargs: ({'supported': True, 'source_index': 0,
        'evidence_quote': 'A completely invented supporting passage with no basis.', 'finding': 'Invented', 'suggested_p': .9}
        if schema.__name__ == 'Finding' else fallback)
    assert moderator.verify(ctx, plan, plan['options'][0], plan['consequences'][0]) is None
    assert not store.get_evidence(ctx.cid) and ctx.metrics['verifications'] == 2


def test_situational_splitter_scoring_and_sparse_fallback(tmp_path):
    store, council, plan, stories, agents = setup_council(tmp_path)
    for i, story in enumerate(stories):
        story['context'] = {'savings': 'low' if i % 20 < 10 else 'high'}
        story['outcome'] = 'regret' if i % 20 < 10 else 'glad'
        story['reasons'] = [{'consequence_id': 'c1' if (i < 20) == (i % 20 < 10) else 'c2'}]
    beliefs = {'A': {'c1': .5, 'c2': .5}, 'B': {'c1': .5, 'c2': .5}}
    weights = {'money': 5, 'stability': 1, 'growth': 1, 'stress': 1}
    choices = values.candidates(plan, stories, agents, beliefs, weights, {'weights': weights})
    assert choices[0]['kind'] == 'situational' and choices[0]['flips']
    assert len(choices[0]['outcomes']) == 2
