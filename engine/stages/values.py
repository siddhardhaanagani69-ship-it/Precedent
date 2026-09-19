"""Score one useful question and apply its answer without another debate."""

from collections import defaultdict
from copy import deepcopy

from engine.config import STANCE_TEMPERATURE
from engine.math_core import consensus, score_question, stance
from engine.schemas import QuestionText
from engine.stages.cohorts import fact_table
from engine.stages.moderator import top_pair


def baseline(ctx, plan: dict, stories: list[dict], agents: list[dict]) -> tuple[dict, dict]:
    beliefs, weights = consensus(plan, stories, agents)
    profile = ctx.store.get_profile(ctx.council['visitor_id']) or {}
    weights.update({k: v for k, v in profile.get('weights', {}).items() if k in weights})
    return beliefs, weights


def candidates(plan: dict, stories: list[dict], agents: list[dict], beliefs: dict,
               weights: dict, profile: dict) -> list[dict]:
    current = stance(beliefs, weights, plan['consequences'], STANCE_TEMPERATURE)
    a, b, leader = top_pair(agents)
    unknown = [attr for attr in plan['attributes'] if attr not in profile.get('weights', {})]
    result = []
    # Test each one-attribute swap before selecting opposing priorities.
    def closes(attr, left, right):
        changed = {**left['weights'], attr: right['weights'][attr]}
        return abs(left['stance'][leader] - right['stance'][leader]) - abs(
            stance(left['beliefs'], changed, plan['consequences'], STANCE_TEMPERATURE)[leader] - right['stance'][leader])
    xs = [attr for attr in unknown if a['weights'][attr] > b['weights'][attr]]
    ys = [attr for attr in unknown if b['weights'][attr] > a['weights'][attr]]
    if unknown:
        x = max(xs, key=lambda attr: closes(attr, a, b)) if xs else max(unknown, key=lambda attr: abs(a['weights'][attr] - b['weights'][attr]))
        y = max(ys, key=lambda attr: closes(attr, b, a)) if ys else None
        if y == x:
            y = None
        choices = [('x', f'Prioritize {x}', {x: 5, y: 2}), ('even', 'Balance both', {x: 3.5, y: 3.5}),
                   ('y', f'Prioritize {y}', {x: 2, y: 5})] if y else [
                   ('x', 'A lot', {x: 5}), ('even', 'Somewhat', {x: 3.5}), ('y', 'A little', {x: 2})]
        result.append({'kind': 'values', 'attributes': [x, y] if y else [x],
            'text': f'Which matters more for this decision: {x} or {y}?' if y else f'How much does {x} matter for this decision?',
            'answers': [{'id': key, 'label': label} for key, label, _ in choices],
            'outcomes': {key: {'beliefs': beliefs, 'weights': {**weights, **changes}, 'facts': {}} for key, _, changes in choices}})
    leading = max(current, key=current.get)
    for situation in plan['situational']:
        key = situation['key']
        if situation.get('user_value') is not None or key in profile.get('facts', {}):
            continue
        groups = defaultdict(list)
        for story in stories:
            value = story['context'].get(key)
            if story['option_id'] == leading and value in situation['values']:
                groups[value].append(story)
        rates = [sum(s['outcome'] == 'regret' for s in rows) / len(rows) for rows in groups.values() if len(rows) >= 5]
        if len(rates) < 2 or max(rates) - min(rates) < 0.2:
            continue
        outcomes, answers = {}, []
        for i, value in enumerate(situation['values']):
            subset = [s for s in stories if s['context'].get(key) == value]
            table = fact_table(plan, subset) if len(subset) >= 8 else plan['fact_table']
            scoped = {o: {c: cell['p'] for c, cell in cells.items()} for o, cells in table.items()}
            aid = str(i)
            answers.append({'id': aid, 'label': value})
            outcomes[aid] = {'beliefs': scoped, 'weights': weights, 'facts': {key: value}}
        result.append({'kind': 'situational', 'key': key, 'text': f"What best describes your {situation['label'].lower()}?",
                       'answers': answers, 'outcomes': outcomes})
    for candidate in result:
        distributions = [stance(o['beliefs'], o['weights'], plan['consequences'], STANCE_TEMPERATURE)
                         for o in candidate['outcomes'].values()]
        candidate.update(score_question(current, distributions))
    return sorted((c for c in result if c['ask']), key=lambda c: (c['score'], c['kind'] == 'situational'), reverse=True)


def run(ctx, plan: dict, stories: list[dict], agents: list[dict]) -> bool:
    beliefs, weights = baseline(ctx, plan, stories, agents)
    profile = ctx.store.get_profile(ctx.council['visitor_id']) or {}
    choices = candidates(plan, stories, agents, beliefs, weights, profile)
    if not choices:
        ctx.notice('None of the tested questions would change the recommendation enough to ask. Other unknowns may still matter.')
        return False
    selected = choices[0]
    a, b, _ = top_pair(agents)
    why = (f"{a['name']} and {b['name']} emphasize different tradeoffs. Your answer could change the recommendation."
           if selected['kind'] == 'values' else 'Outcomes differ across this situation in the stored accounts; your answer could change the recommendation.')
    text = ctx.llm.structured('moderator', QuestionText,
        'Phrase exactly this question in plain language. Preserve the meaning of the listed answer choices. '
        'Use only supplied numbers, describing reported accounts rather than predicted odds. Do not add another question.',
        {'question': selected['text'], 'answers': selected['answers'], 'why': why, 'fact_table': plan['fact_table']},
        fallback={'text': selected['text'], 'why': why}, max_tokens=350, lane='verify')
    ctx.store.insert_question(ctx.cid, text=text['text'], why=text['why'], candidate=selected, answers=selected['answers'])
    ctx.store.update_council(ctx.cid, status='awaiting_user')
    return True


def apply_answer(ctx, beliefs: dict, weights: dict) -> tuple[dict, dict]:
    answer = ctx.store.get_answer(ctx.cid)
    if not answer:
        return beliefs, weights
    question = ctx.store.get_question(ctx.cid)
    candidate = question['candidate']
    chosen = candidate['outcomes'][answer['answer_id']]
    if candidate['kind'] == 'values':
        remembered = {attr: chosen['weights'][attr] for attr in candidate['attributes']}
        ctx.store.save_profile(ctx.council['visitor_id'], remembered, {})
    else:
        ctx.store.save_profile(ctx.council['visitor_id'], {}, chosen['facts'])
    return deepcopy(chosen['beliefs']), dict(chosen['weights'])
