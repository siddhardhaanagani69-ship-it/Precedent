"""Memory/cache boundaries without scraping or model requests."""

from types import SimpleNamespace
import numpy as np

from engine.stages import memory, plan as planning
from test_phase1 import decision_plan
from store.sqlite_store import SQLiteStore


def test_cache_requires_matching_taxonomy_and_copies_no_identity(tmp_path):
    store = SQLiteStore(tmp_path / 'cache.db')
    plan = decision_plan()
    first = store.create_council('previous-visitor', 'Should I change jobs this year?')
    vector = (np.ones(384) / np.sqrt(384)).tolist()
    store.update_council(first['id'], status='done', question_embedding=vector, plan=plan)
    store.insert_stories(first['id'], [{'label': 'S1', 'source': 'test', 'url': 'https://example.com/test',
        'option_id': 'A', 'outcome': 'glad', 'context': {}, 'summary': 'Synthetic cache fixture.',
        'reasons': [], 'similarity': .7, 'embedding': vector}])
    current = store.create_council('new-visitor', first['question'])
    notices = []
    ctx = SimpleNamespace(store=store, cid=current['id'], council=current,
        embedder=SimpleNamespace(embed=lambda texts: np.tile(vector, (len(texts), 1))),
        progress=lambda **kwargs: None, notice=notices.append)
    previous = memory.find(ctx)
    assert previous['id'] == first['id']
    changed = decision_plan()
    changed['options'][0]['label'] = 'A different choice'
    assert memory.reuse(ctx, changed, previous) is None
    rows = memory.reuse(ctx, plan, previous)
    assert len(rows) == 1 and rows[0]['id'] != store.get_stories(first['id'])[0]['id']
    assert rows[0]['council_id'] == current['id'] and rows[0]['similarity'] > .99
    assert 'Reused 1 stories' in notices[0]
    assert store.get_profile('new-visitor') is None


def test_planner_receives_memory_and_prefills_known_fact(tmp_path):
    store = SQLiteStore(tmp_path / 'memory.db')
    council = store.create_council('visitor', 'Should I change jobs this year?')
    store.save_profile('visitor', {'stability': 5}, {'savings': 'high'})
    contexts, notices = [], []
    def respond(role, schema, instruction, context, **kwargs):
        contexts.append(context)
        return decision_plan()
    ctx = SimpleNamespace(store=store, cid=council['id'], council=council,
                          llm=SimpleNamespace(structured=respond), notice=notices.append)
    plan = planning.run(ctx)
    assert contexts[0]['profile']['facts']['savings'] == 'high'
    assert plan['situational'][0]['user_value'] == 'high'
    assert any('The council remembers' in note for note in notices)
    assert store.get_profile('another-visitor') is None
