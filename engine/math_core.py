"""Pure decision math. No model, network, or storage dependencies."""

import math

Beliefs = dict[str, dict[str, float]]
Weights = dict[str, float]


def stance(beliefs: Beliefs, weights: Weights, consequences: list[dict],
           temperature: float = 4.0) -> dict[str, float]:
    """Return a numerically stable softmax of expected weighted utility."""
    if not beliefs or not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("Beliefs and a positive finite temperature are required")
    utilities = {}
    for option, probabilities in beliefs.items():
        utility = 0.0
        for consequence in consequences:
            p = probabilities[consequence["id"]]
            weight = weights[consequence["attribute"]]
            impact = consequence["impact"]
            if not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError("Belief probabilities must be between zero and one")
            if not math.isfinite(weight) or not 1 <= weight <= 5:
                raise ValueError("Attribute weights must be between one and five")
            if not math.isfinite(impact) or not -3 <= impact <= 3:
                raise ValueError("Consequence impacts must be between minus three and three")
            utility += p * impact * weight
        utilities[option] = utility
    maximum = max(utilities.values())
    exps = {o: math.exp((u - maximum) / temperature) for o, u in utilities.items()}
    total = sum(exps.values())
    return {o: value / total for o, value in exps.items()}


def swap_test(beliefs_a: Beliefs, weights_a: Weights, beliefs_b: Beliefs,
              weights_b: Weights, consequences: list[dict], option: str,
              temperature: float = 4.0) -> dict[str, float]:
    """Average the brief's belief/weight swap effects in both directions.

    Signed effects are intentional: a swap can widen a gap. They are not
    additive causal percentages when beliefs and priorities interact.
    """
    aa = stance(beliefs_a, weights_a, consequences, temperature)[option]
    bb = stance(beliefs_b, weights_b, consequences, temperature)[option]
    ba = stance(beliefs_b, weights_a, consequences, temperature)[option]
    ab = stance(beliefs_a, weights_b, consequences, temperature)[option]
    total = abs(aa - bb)
    factual = total - (abs(ba - bb) + abs(ab - aa)) / 2
    values = total - (abs(ab - bb) + abs(ba - aa)) / 2
    return {"total": total, "factual": factual, "values": values}


def score_question(current: dict[str, float], answers: list[dict[str, float]]) -> dict:
    """Score movement in the current leader's probability across answers."""
    if not current or not answers:
        raise ValueError("Current stance and at least one answer are required")
    for probabilities in [current, *answers]:
        if probabilities.keys() != current.keys() or any(
            not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()
        ) or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-6):
            raise ValueError("Answers must be distributions over the same options")
    leader = max(current, key=current.get)
    score = sum(abs(a[leader] - current[leader]) for a in answers) / len(answers)
    # A tie is not a reversal; only a strictly better alternative flips it.
    flips = any(max(a.values()) > a[leader] for a in answers)
    return {"score": score, "flips": flips, "ask": flips or score >= 0.10}
