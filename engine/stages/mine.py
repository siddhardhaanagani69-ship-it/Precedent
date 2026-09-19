"""Rank candidates, extract firsthand outcomes, and store only paraphrases."""

import json
import re
import unicodedata
from concurrent.futures import as_completed

import numpy as np

from engine.config import LIMITS
from engine.schemas import Extraction
from engine.stages.scout import intent_scores


def clean_text(text: str, words: int) -> str:
    text = re.sub(r"(?:https?://(?:www\.)?reddit\.com/(?:u|user)/|/?u/)[\w-]+", "[person]", text)
    return " ".join(text.split()[:words])


# Models re-typeset quotes they copy faithfully: curly quotes, en/em dashes and
# ellipsis characters routinely replace their ASCII originals. Folding these keeps
# the provenance check strict about content while tolerating punctuation.
TYPOGRAPHY = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
                            "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
                            "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                            "\u2014": "-", "\u2015": "-", "\u2026": "...", "\u00a0": " "})


def normalize_quote(text: str) -> str:
    """Fold case, unicode form, punctuation and whitespace for substring matching."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(TYPOGRAPHY).casefold().split())


def grounded_quote(quote: str, source: str) -> bool:
    """Reject invented evidence before a model-generated story can be retained."""
    quote, source = normalize_quote(quote), normalize_quote(source)
    return len(quote) >= 25 and len(quote.split()) >= 5 and quote in source


def run(ctx, plan: dict, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    ctx.store.update_council(ctx.cid, question_embedding=ctx.embedder.embed([ctx.council["question"]])[0].tolist())
    # Rank by the same outcome intents scouting used, so the extractor sees the
    # candidates most likely to describe a lived result, not the closest restatements.
    order = np.argsort(intent_scores(ctx, plan, candidates))[::-1][:LIMITS["prefilter"]]
    candidates = [candidates[int(i)] for i in order]
    options = {o["id"] for o in plan["options"]}
    consequence_ids = {c["id"] for c in plan["consequences"]} | {"other"}
    consequence_attributes = {c["id"]: c["attribute"] for c in plan["consequences"]}
    situation_keys = {s["key"] for s in plan["situational"]}

    def extract(batch):
        output = ctx.llm.structured("extractor", Extraction,
            "Extract firsthand decision outcomes only. Return one record per candidate idx. "
            "Ignore advice, predictions, hypothetical choices, secondhand stories and advertisements. "
            "Only keep a clearly chosen option with a glad or regret outcome; otherwise mark relevant false. "
            "Reasons use consequence IDs and attributes from the plan (or consequence_id other). "
            "Context uses listed situational keys. Never infer missing context. "
            "For every relevant record, evidence_quote MUST copy an exact contiguous passage from that candidate "
            "showing what the author chose and how it went. If that passage does not exist, mark relevant false. "
            "Never turn unrelated text into a story about the listed options. "
            "Write third-person paraphrases: summaries <=30 words; reasons <=15 words. No names or usernames.",
            {"options": plan["options"], "consequences": plan["consequences"], "situational": plan["situational"],
             "candidates": [{"idx": i, "text": c["text"][:1800]} for i, c in enumerate(batch)]},
            fallback={"stories": []}, max_tokens=2400)
        kept, seen = [], set()
        for row in output["stories"]:
            index = row["idx"]
            if (index in seen or index >= len(batch) or not row["relevant"] or row["option_id"] not in options
                    or row["outcome"] not in {"glad", "regret"} or not row["summary"].strip()):
                continue
            if not grounded_quote(row.get("evidence_quote", ""), batch[index]["text"][:1800]):
                continue
            seen.add(index)
            reasons = []
            for reason in row["reasons"]:
                # A listed consequence defines its own attribute, so the model's
                # attribute wording only has to be valid for the "other" bucket.
                attribute = consequence_attributes.get(reason["consequence_id"], reason["attribute"])
                if reason["consequence_id"] in consequence_ids and attribute in plan["attributes"]:
                    reason["attribute"] = attribute
                    reason["text"] = clean_text(reason["text"], 15)
                    reasons.append(reason)
            if not reasons:
                continue
            source = batch[index]
            kept.append({"source": source["source"], "url": source["url"], "option_id": row["option_id"],
                "outcome": row["outcome"], "context": {k: v for k, v in row["context"].items() if k in situation_keys},
                "reasons": reasons, "summary": clean_text(row["summary"], 30), "months_after": row["months_after"]})
        return kept

    size = LIMITS["extraction_batch"]
    futures = [ctx.pool.submit(extract, candidates[i:i + size]) for i in range(0, len(candidates), size)]
    stories = []
    for future in as_completed(futures):
        stories.extend(future.result())
        ctx.progress(stories_kept=len(stories))
    if not stories:
        return []
    user_text = plan["user_summary"] + json.dumps({s["key"]: s["user_value"] for s in plan["situational"] if s["user_value"]})
    user = ctx.embedder.embed([user_text])[0]
    vectors = ctx.embedder.embed([s["summary"] + json.dumps(s["context"]) for s in stories])
    for story, vector in zip(stories, vectors):
        story["similarity"] = float(np.clip(0.2 + 0.8 * ((vector @ user + 1) / 2), 0.2, 1))
        story["embedding"] = vector.tolist()
    stories.sort(key=lambda s: s["similarity"], reverse=True)
    for i, story in enumerate(stories, 1):
        story["label"] = f"S{i}"
    ctx.store.insert_stories(ctx.cid, stories)
    ctx.progress(stories_kept=len(stories), force=True)
    ctx.notice(f"Kept {len(stories)} relevant outcome stories with source links.")
    return ctx.store.get_stories(ctx.cid)
