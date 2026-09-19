"""A basic Phase 1 verdict; recommendations and confidence remain pure math."""

from collections import Counter
from datetime import datetime, timezone
from engine.config import STANCE_TEMPERATURE
from engine.math_core import stance
from engine.schemas import VerdictText


def run(ctx, plan: dict, stories: list[dict], agents: list[dict]) -> dict:
    if not agents:
        verdict = {"recommendation": "Not enough precedent", "confidence": None,
            "summary": f"We found {len(stories)} relevant firsthand {'outcome' if len(stories) == 1 else 'outcomes'}, but fewer than two cohorts had at least five stories. There is not enough evidence for a responsible comparison.",
            "crux": "More accounts with clear choices and outcomes are needed.", "dissent": {},
            "cheap_test": "Ask one person who chose each option what happened and what they would do differently.",
            "crowd_vs_you": plan.get("crowd_vs_you", {}), "receipts": [
                {"label": s["label"], "url": s["url"], "summary": s["summary"]} for s in stories[:5]]}
    else:
        masses = {a["id"]: sum(s["similarity"] for s in stories if f"{s['option_id']}:{s['outcome']}" == a["cohort_key"]) for a in agents}
        total = sum(masses.values())
        weights = {attr: sum(a["weights"][attr] * masses[a["id"]] for a in agents) / total for attr in plan["attributes"]}
        beliefs = {o["id"]: {c["id"]: sum(a["beliefs"][o["id"]][c["id"]] * masses[a["id"]] for a in agents) / total
                             for c in plan["consequences"]} for o in plan["options"]}
        final = stance(beliefs, weights, plan["consequences"], STANCE_TEMPERATURE)
        winner = max(final, key=final.get)
        label = next(o["label"] for o in plan["options"] if o["id"] == winner)
        turns = ctx.store.get_turns(ctx.cid)
        counts = Counter(cite for turn in turns for claim in turn["claims"] for cite in claim["cites"])
        sources = {s["label"]: {"label": s["label"], "url": s["url"], "summary": s["summary"]} for s in stories}
        receipts = [sources[key] for key, _ in counts.most_common(8) if key in sources]
        opposition = min(agents, key=lambda a: a["stance"][winner])
        dissent_turns = [t for t in turns if t["agent_id"] == opposition["id"] and t["claims"]]
        dissent = {"agent": opposition["name"], "message": dissent_turns[-1]["message"] if dissent_turns else "No cited dissent was supplied."}
        fallback = {"summary": f"The council currently leans toward {label}, based on {len(stories)} stored outcomes. These self-selected online accounts are not a representative survey.",
                    "crux": "The recommendation depends on the consequences people reported and the priorities revealed by their stories.",
                    "cheap_test": "Talk to one person who recently chose each option. Compare their actual outcome with your biggest concern before deciding."}
        text = ctx.llm.structured("moderator", VerdictText,
            "Explain this computed recommendation in 2–3 plain sentences, identify the unresolved tradeoff, "
            "and propose one cheap concrete test this week. Do not invent numbers. Cite supplied receipts "
            "when describing outcomes. Never call model confidence a real-world probability of success. "
            "No user values question or current-fact verification has occurred in this Phase 1 council.",
            {"decision": ctx.council["question"], "recommendation": label, "confidence": final[winner],
             "stories_kept": len(stories), "receipts": receipts[:5], "dissent": dissent},
            fallback=fallback, max_tokens=650, lane="verify")
        verdict = {"recommendation": label, "confidence": final[winner], **text, "dissent": dissent,
                   "crowd_vs_you": plan["crowd_vs_you"], "receipts": receipts}
    result = ctx.store.insert_verdict(ctx.cid, **verdict)
    ctx.store.update_council(ctx.cid, status="done", finished_at=datetime.now(timezone.utc).isoformat())
    return result
