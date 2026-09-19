"""Turn a decision into options and outcome-story search queries."""

from engine.schemas import Plan


def run(ctx, cached: dict | None = None) -> dict:
    profile = ctx.store.get_profile(ctx.council['visitor_id']) or {'weights': {}, 'facts': {}}
    prior = ({key: cached['plan'][key] for key in ('options', 'attributes', 'consequences')}
             if cached else None)
    plan = ctx.llm.structured("moderator", Plan,
        "Plan an evidence-backed decision council. Identify 2 options (3 only if essential), "
        "4–6 attributes, 6–10 option-agnostic consequences with signed impacts -3..3, and "
        "2–4 situational facts. Do not assume facts absent from the decision. "
        "Write 6 SHORT Reddit search queries (2–4 terms each) seeking first-person OUTCOMES for both options. "
        "Use the distinctive topic keyword in every query; join essential terms with AND (e.g. startup AND regret). "
        "Use varied words such as regret, glad, update, one year later. Consequences describe events, not advice. "
        "Use profile facts to prefill situations only when applicable; explicit current text takes precedence. "
        "When a previous extraction taxonomy is supplied, keep its IDs and definitions exactly if appropriate "
        "for this decision. Change them when meaning differs; never force a cache match.",
        {"decision": ctx.council["question"], "profile": profile, "previous_taxonomy": prior}, fallback=None, max_tokens=2200)
    if plan is None:
        raise RuntimeError("The planner could not identify reliable options. Please try a clearer decision or run again later.")
    remembered = []
    for situation in plan['situational']:
        previous = profile['facts'].get(situation['key'])
        if previous is not None and situation.get('user_value') is None and previous in situation['values']:
            situation['user_value'] = previous
        if previous is not None and situation.get('user_value') == previous:
            remembered.append(f"{situation['label']}: {previous}")
    remembered.extend(f"{key} priority: {value:g}/5" for key, value in profile['weights'].items() if key in plan['attributes'])
    if remembered:
        ctx.notice('The council remembers: ' + '; '.join(remembered))
    ctx.store.update_council(ctx.cid, plan=plan)
    ctx.notice("The council will compare: " + " · ".join(o["label"] for o in plan["options"]))
    return plan
