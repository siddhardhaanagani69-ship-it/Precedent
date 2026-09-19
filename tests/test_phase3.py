"""Memory/cache boundaries without scraping or model requests."""

from types import SimpleNamespace
import numpy as np
import pytest

from engine.stages import memory, plan as planning
from test_phase1 import decision_plan
from store.sqlite_store import SQLiteStore


def test_cache_requires_matching_taxonomy_and_copies_no_identity(tmp_path):
    store = SQLiteStore(tmp_path / 'cache.db')
    plan = decision_plan()
    first = store.create_council('previous-visitor', 'Should I change jobs this year?')
    vector = (np.ones(384) / np.sqrt(384)).tolist()
    store.update_council(first['id'], status='done', question_embedding=vector, plan=plan)
    store.insert_stories(first['id'], [{'label': f'S{i}', 'source': 'test', 'url': f'https://example.com/test{i}',
        'option_id': 'A', 'outcome': 'glad', 'context': {}, 'summary': f'Synthetic cache fixture {i}.',
        'reasons': [], 'similarity': .7, 'embedding': vector} for i in range(1, 11)])
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
    original = {row['id'] for row in store.get_stories(first['id'])}
    assert len(rows) == 10 and not original & {row['id'] for row in rows}
    assert rows[0]['council_id'] == current['id'] and rows[0]['similarity'] > .99
    assert 'Reused 10 stories' in notices[0]
    assert store.get_profile('new-visitor') is None


def test_cache_skips_a_council_too_thin_to_seat_cohorts(tmp_path):
    """Reusing a run that never gathered enough evidence would pass on its emptiness."""
    store = SQLiteStore(tmp_path / 'thin.db')
    plan = decision_plan()
    first = store.create_council('previous-visitor', 'Should I change jobs this year?')
    vector = (np.ones(384) / np.sqrt(384)).tolist()
    store.update_council(first['id'], status='done', question_embedding=vector, plan=plan)
    store.insert_stories(first['id'], [{'label': 'S1', 'source': 'test', 'url': 'https://example.com/thin',
        'option_id': 'A', 'outcome': 'glad', 'context': {}, 'summary': 'The only story that survived.',
        'reasons': [], 'similarity': .7, 'embedding': vector}])
    current = store.create_council('new-visitor', first['question'])
    ctx = SimpleNamespace(store=store, cid=current['id'], council=current,
        embedder=SimpleNamespace(embed=lambda texts: np.tile(vector, (len(texts), 1))),
        progress=lambda **kwargs: None, notice=lambda message: None)
    previous = memory.find(ctx)
    assert previous['id'] == first['id']
    # A fresh scrape must still happen rather than inheriting one unusable story.
    assert memory.reuse(ctx, plan, previous) is None


def test_cache_can_be_disabled_for_calibration_runs(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / 'nocache.db')
    first = store.create_council('previous-visitor', 'Should I change jobs this year?')
    vector = (np.ones(384) / np.sqrt(384)).tolist()
    store.update_council(first['id'], status='done', question_embedding=vector, plan=decision_plan())
    current = store.create_council('new-visitor', first['question'])
    ctx = SimpleNamespace(store=store, cid=current['id'], council=current,
        embedder=SimpleNamespace(embed=lambda texts: np.tile(vector, (len(texts), 1))),
        progress=lambda **kwargs: None, notice=lambda message: None)
    monkeypatch.setenv('PRECEDENT_NO_CACHE', '1')
    assert memory.find(ctx) is None


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


def test_backend_factory_rejects_an_unknown_backend(monkeypatch):
    """A typo in DB_BACKEND must fail loudly, not silently demo on the wrong store."""
    import store as store_module
    import engine.config as config
    monkeypatch.setattr(config, 'DB_BACKEND', 'postgres')
    with pytest.raises(RuntimeError):
        store_module.get_store()


def test_supabase_store_requires_credentials():
    from store.supabase_store import SupabaseStore
    with pytest.raises(RuntimeError):
        SupabaseStore('', '')


def test_supabase_migration_locks_down_every_table():
    """RLS with no policies is what keeps the browser out of the database."""
    from engine.config import ROOT
    sql = (ROOT / 'supabase/migrations/001_init.sql').read_text().casefold()
    for table in ('profiles', 'councils', 'stories', 'agents', 'turns', 'evidence',
                  'questions', 'answers', 'verdicts', 'worker_heartbeat'):
        assert f'create table if not exists {table} ' in sql
        assert f'alter table {table}' in sql and 'enable row level security' in sql
    assert 'create policy' not in sql, 'a public policy would expose council data'
    assert 'create extension if not exists vector' in sql
    assert 'match_recent_council' in sql
    assert 'extensions.vector(384)' in sql
