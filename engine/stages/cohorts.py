"""Build evidence pools and derive priorities from what each cohort discusses."""

from collections import Counter, defaultdict
from engine.config import LIMITS, STANCE_TEMPERATURE
from engine.math_core import stance
from engine.schemas import Personas

COLORS = ["#225c71", "#985239", "#576f3e", "#755b91"]


def fact_table(plan: dict, stories: list[dict]) -> dict:
    table = {}
    for option in plan["options"]:
        rows = [s for s in stories if s["option_id"] == option["id"]]
        total = sum(s["similarity"] for s in rows)
        table[option["id"]] = {}
        for consequence in plan["consequences"]:
            mentions = [s for s in rows if any(r["consequence_id"] == consequence["id"] for r in s["reasons"])]
            table[option["id"]][consequence["id"]] = {
                "p": (sum(s["similarity"] for s in mentions) + 0.5) / (total + 1),
                "n": len(rows), "mentions": len(mentions),
            }
    return table


def run(ctx, plan: dict, stories: list[dict]) -> list[dict]:
    plan["fact_table"] = fact_table(plan, stories)
    plan["crowd_vs_you"] = {}
    for option in plan["options"]:
        rows = [s for s in stories if s["option_id"] == option["id"]]
        total = sum(s["similarity"] for s in rows)
        plan["crowd_vs_you"][option["id"]] = {
            "n": len(rows), "crowd": sum(s["outcome"] == "glad" for s in rows) / len(rows) if rows else None,
            "similar": sum(s["similarity"] for s in rows if s["outcome"] == "glad") / total if total else None,
        }
    ctx.store.update_council(ctx.cid, plan=plan)
    buckets = defaultdict(list)
    for story in stories:
        buckets[f"{story['option_id']}:{story['outcome']}"].append(story)
    buckets = dict(sorted(((k, v) for k, v in buckets.items() if len(v) >= LIMITS["min_cohort"]),
                          key=lambda item: -len(item[1]))[:4])
    if len(buckets) < 2:
        ctx.notice("Too few substantial cohorts for a debate. The verdict will explain the evidence gap.")
        return []
    if LIMITS["min_cohort"] < 5:
        ctx.notice("Thin evidence: cohorts are small, so treat this debate as a preview built only from the real stories found.")
    personas = ctx.llm.structured("moderator", Personas,
        "Name each cohort plainly by its choice and outcome. Give each a two-sentence first-person voice "
        "grounded only in its sample summaries. Do not invent biographies or names.",
        {"options": plan["options"], "cohorts": [{"cohort_key": k, "summaries": [s["summary"] for s in v[:5]]}
                                                  for k, v in buckets.items()]},
        fallback={"agents": []}, max_tokens=900)
    voices = {p["cohort_key"]: p for p in personas["agents"]}
    models = ctx.llm.models["agents"]
    agents = []
    for i, (key, rows) in enumerate(buckets.items()):
        option, outcome = key.split(":")
        counts = Counter(r["attribute"] for s in rows for r in s["reasons"])
        maximum = max(counts.values(), default=1)
        weights = {a: 1 + 4 * counts[a] / maximum for a in plan["attributes"]}
        own = fact_table(plan, rows)
        beliefs = {o["id"]: {c["id"]: (own if o["id"] == option else plan["fact_table"])[o["id"]][c["id"]]["p"]
                             for c in plan["consequences"]} for o in plan["options"]}
        label = next(o["label"] for o in plan["options"] if o["id"] == option)
        voice = voices.get(key, {"name": f"{label} · {outcome}", "persona": f"I speak for people who chose {label} and felt {outcome}."})
        model = models[i % len(models)]
        agent = ctx.store.upsert_agent(ctx.cid, {
            "cohort_key": key, "name": voice["name"], "persona": voice["persona"],
            "model": model["id"], "family": model["family"], "color": COLORS[i], "story_count": len(rows),
            "weights": weights, "beliefs": beliefs, "stance": stance(beliefs, weights, plan["consequences"], STANCE_TEMPERATURE),
            "prev_stance": {},
        })
        agents.append(agent)
    ctx.progress(cohorts=len(agents), force=True)
    ctx.notice("Council formed: " + "; ".join(f"{a['name']} ({a['story_count']} stories, {a['family']})" for a in agents))
    if len({a["family"] for a in agents}) < len(agents):
        ctx.notice("Some seats share a model family because access to other families is unavailable. Each seat has separate stories and beliefs.")
    return agents
