"""Find the mathematical crux, then check pooled accounts or a live source."""

from copy import deepcopy
from itertools import combinations
from urllib.parse import urlsplit

from engine.apify_tools import run_actor
from engine.config import LIMITS, STANCE_TEMPERATURE
from engine.math_core import stance, swap_test
from engine.schemas import Finding, ModeratorNote, VerificationQuery
from engine.stages.mine import grounded_quote


def top_pair(agents: list[dict]) -> tuple[dict, dict, str]:
    leader = max(agents[0]['stance'], key=lambda o: sum(a['stance'][o] * a['story_count'] for a in agents))
    a, b = max(combinations(agents, 2), key=lambda pair: abs(pair[0]['stance'][leader] - pair[1]['stance'][leader]))
    return a, b, leader


def crux(plan: dict, a: dict, b: dict, leader: str) -> tuple[str, str]:
    """Choose the single belief swap that closes the largest symmetric gap."""
    def gap(option, consequence):
        values = []
        for left, right in ((a, b), (b, a)):
            changed = deepcopy(left['beliefs'])
            changed[option][consequence] = right['beliefs'][option][consequence]
            values.append(abs(stance(changed, left['weights'], plan['consequences'], STANCE_TEMPERATURE)[leader]
                              - right['stance'][leader]))
        return sum(values)
    return min(((o['id'], c['id']) for o in plan['options'] for c in plan['consequences']),
               key=lambda target: gap(*target))


def verify(ctx, plan: dict, option: dict, consequence: dict) -> dict | None:
    ctx.progress(verifications=ctx.metrics['verifications'] + 1, force=True)
    query = ctx.llm.structured('moderator', VerificationQuery,
        'Write a concise web search query to check this consequence for the specified option. Prefer primary sources.',
        {'decision': ctx.council['question'], 'option': option, 'consequence': consequence},
        fallback={'query': f"{option['label']} {consequence['label']}"}, max_tokens=160, lane='verify')
    try:
        pages = run_actor('apify/rag-web-browser', {'query': query['query'], 'maxResults': 3,
            'outputFormats': ['markdown'], 'requestTimeoutSecs': 35}, LIMITS['verification_timeout'], partial=True)
    except RuntimeError:
        ctx.notice('The live fact check did not finish. Continuing with the stored accounts.')
        return None
    sources = []
    for page in pages:
        metadata = page.get('metadata') or {}
        url = metadata.get('url') or (page.get('searchResult') or {}).get('url') or ''
        parts = urlsplit(url)
        text = page.get('markdown') or page.get('text') or ''
        if (parts.scheme in {'http', 'https'} and parts.hostname and not parts.username and not parts.password
                and text and page.get('crawl', {}).get('requestStatus') != 'failed'):
            sources.append({'url': url, 'text': text[:3000]})
    if not sources:
        ctx.notice('The fact-check pages were empty or blocked. No web evidence was added.')
        return None
    finding = ctx.llm.structured('moderator', Finding,
        'Check whether these pages directly support a finding about the target consequence and option. '
        'If not, supported=false. Quote an exact supporting passage. suggested_p is a tentative belief estimate, '
        'not an observed event rate. Never infer a statistical rate from unrelated numbers.',
        {'option': option, 'consequence': consequence, 'sources': sources[:3]},
        fallback={'supported': False}, max_tokens=700, lane='verify')
    index = finding.get('source_index', 0)
    if not finding['supported'] or index >= len(sources) or not grounded_quote(finding.get('evidence_quote', ''), sources[index]['text']):
        ctx.notice('The web response lacked a supported finding. No web evidence was added.')
        return None
    return ctx.store.insert_evidence(ctx.cid, kind='web', title=consequence['label'],
        body=f"{finding['finding']} Suggested belief: {finding['suggested_p']:.0%} (model interpretation, not a measured rate).",
        url=sources[index]['url'], option_id=option['id'], consequence_id=consequence['id'],
        value=finding['suggested_p'], n=None)


def run(ctx, plan: dict, agents: list[dict], round_number: int) -> bool:
    a, b, leader = top_pair(agents)
    split = swap_test(a['beliefs'], a['weights'], b['beliefs'], b['weights'], plan['consequences'], leader, STANCE_TEMPERATURE)
    plan['disagreement'] = split
    stop = (split['total'] <= 0.1 or split['values'] >= 0.7 * split['total']
            or round_number >= LIMITS['max_rounds'])
    note = 'The remaining disagreement is mainly about priorities.' if split['values'] >= 0.7 * split['total'] else 'The council is close enough to weigh the final tradeoff.'
    if not stop and split['factual'] >= split['values']:
        oid, cid = crux(plan, a, b, leader)
        option = next(o for o in plan['options'] if o['id'] == oid)
        consequence = next(c for c in plan['consequences'] if c['id'] == cid)
        cell = plan['fact_table'][oid][cid]
        evidence = None
        if (consequence['checkable_online'] or cell['n'] < 15) and ctx.metrics['verifications'] < LIMITS['max_verifications']:
            evidence = verify(ctx, plan, option, consequence)
        if evidence is None:
            evidence = ctx.store.insert_evidence(ctx.cid, kind='datacheck', title=consequence['label'],
                body=f"Among {cell['n']} stored accounts choosing {option['label']}, {cell['mentions']} mentioned {consequence['label']}. "
                     f"Similarity-weighted, smoothed estimate: {cell['p']:.0%}. These are reports, not population odds.",
                url=None, option_id=oid, consequence_id=cid, value=cell['p'], n=cell['n'])
        ctx.store.insert_turn(ctx.cid, kind='verification' if evidence['kind'] == 'web' else 'factcheck', round=round_number,
            message=f"{evidence['body']} [{evidence['label']}]",
            claims=[{'text': evidence['title'], 'cites': [evidence['label']]}])
        note = f"Use [{evidence['label']}] to align beliefs about {consequence['label']} for {option['label']}; explain the remaining tradeoff."
        plan['settled_crux'] = evidence['body']
    if round_number >= LIMITS['max_rounds']:
        note = 'Three rounds are complete. Any remaining uncertainty carries into the verdict.'
    elif not stop:
        note = ctx.llm.structured('moderator', ModeratorNote,
            'Write one sentence naming the remaining disagreement, grounded only in the supplied note.',
            {'note': note, 'agents': [a['name'], b['name']]}, fallback={'message': note}, max_tokens=160, lane='verify')['message']
    plan['moderator_note'] = note
    ctx.store.update_council(ctx.cid, plan=plan)
    ctx.store.insert_turn(ctx.cid, round=round_number, kind='moderator', message=note)
    return stop
