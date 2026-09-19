"""Turn a decision into options and outcome-story search queries."""

from engine.schemas import Plan


def run(ctx) -> dict:
    plan = ctx.llm.structured("moderator", Plan,
        "Plan an evidence-backed decision council. Identify 2 options (3 only if essential), "
        "4–6 attributes, 6–10 option-agnostic consequences with signed impacts -3..3, and "
        "2–4 situational facts. Do not assume facts absent from the decision. "
        "Write 6 SHORT Reddit search queries (2–4 terms each) seeking first-person OUTCOMES for both options. "
        "Use the distinctive topic keyword in every query; join essential terms with AND (e.g. startup AND regret). "
        "Use varied words such as regret, glad, update, one year later. Consequences describe events, not advice.",
        {"decision": ctx.council["question"]}, fallback=None, max_tokens=2200)
    if plan is None:
        raise RuntimeError("The planner could not identify reliable options. Please try a clearer decision or run again later.")
    ctx.store.update_council(ctx.cid, plan=plan)
    ctx.notice("The council will compare: " + " · ".join(o["label"] for o in plan["options"]))
    return plan
