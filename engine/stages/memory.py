"""Reuse public story records only when their extraction meanings still match."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import numpy as np


def find(ctx) -> dict | None:
    vector = ctx.embedder.embed([ctx.council['question']])[0]
    ctx.store.update_council(ctx.cid, question_embedding=vector.tolist())
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    matches = []
    for council in ctx.store.find_recent_councils(since):
        previous = np.asarray(council['question_embedding'])
        if previous.shape == vector.shape and (similarity := float(previous @ vector)) >= .92:
            matches.append((similarity, council))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def taxonomy(plan: dict) -> dict:
    return {key: plan[key] for key in ('options', 'attributes', 'consequences')}


def reuse(ctx, plan: dict, previous: dict | None) -> list[dict] | None:
    if not previous or taxonomy(plan) != taxonomy(previous['plan']):
        return None
    # A cosine match alone cannot guarantee matching choices or consequence IDs.
    # ponytail: exact taxonomy compatibility sacrifices cache hits to avoid mislabeling evidence.
    rows = ctx.store.get_stories(previous['id'])
    if not rows:
        return None
    stories = [{k: deepcopy(v) for k, v in row.items() if k not in {'id', 'council_id', 'created_at'}} for row in rows]
    known = {s['key']: s['user_value'] for s in plan['situational'] if s.get('user_value') is not None}
    user = ctx.embedder.embed([plan['user_summary'] + json.dumps(known)])[0]
    vectors = ctx.embedder.embed([s['summary'] + json.dumps(s['context']) for s in stories])
    for story, vector in zip(stories, vectors):
        story['similarity'] = float(np.clip(.2 + .8 * ((vector @ user + 1) / 2), .2, 1))
        story['embedding'] = vector.tolist()
    stories.sort(key=lambda s: s['similarity'], reverse=True)
    for i, story in enumerate(stories, 1):
        story['label'] = f'S{i}'
    ctx.store.insert_stories(ctx.cid, stories)
    ctx.progress(stories_found=len(stories), stories_kept=len(stories), force=True)
    ctx.notice(f'Reused {len(stories)} stories from a similar council completed within the last 24 hours. Recomputed similarity for your situation.')
    return ctx.store.get_stories(ctx.cid)
