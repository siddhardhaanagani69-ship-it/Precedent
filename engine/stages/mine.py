"""Rank candidates, extract firsthand outcomes, and store only paraphrases."""

import json
import os
import re
import threading
import unicodedata
from concurrent.futures import as_completed

import numpy as np

from engine.config import LIMITS, ROOT
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


class Audit:
    """Record why each candidate was or was not kept, for calibration runs.

    Extraction is the narrowest point in the pipeline and its rejections are
    otherwise invisible: a run reports 98 candidates and 1 story with no way to
    tell which gate discarded the rest. Enabled with PRECEDENT_AUDIT=1.
    """

    def __init__(self):
        self.on = os.getenv("PRECEDENT_AUDIT") == "1"
        self.rows: list[dict] = []
        self.lock = threading.Lock()

    def record(self, candidate: dict, verdict: str, detail: str = "") -> None:
        if not self.on:
            return
        with self.lock:
            self.rows.append({"verdict": verdict, "detail": detail, "url": candidate.get("url", ""),
                              "kind": candidate.get("kind", ""), "text": candidate["text"][:400]})

    def save(self, question: str) -> None:
        if not self.on:
            return
        from collections import Counter
        path = ROOT / "data/mine_audit.json"
        path.write_text(json.dumps({"question": question,
                                    "totals": Counter(r["verdict"] for r in self.rows),
                                    "rows": self.rows}, indent=1))


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

    audit = Audit()

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
        kept, seen, judged = [], set(), set()
        for row in output["stories"]:
            index = row["idx"]
            if index in judged or index >= len(batch):
                continue
            judged.add(index)
            if not row["relevant"]:
                audit.record(batch[index], "not_relevant")
                continue
            if row["option_id"] not in options:
                audit.record(batch[index], "no_option", str(row["option_id"]))
                continue
            if row["outcome"] not in {"glad", "regret"}:
                audit.record(batch[index], "no_outcome", row["outcome"])
                continue
            if not row["summary"].strip():
                audit.record(batch[index], "no_summary")
                continue
            if not grounded_quote(row.get("evidence_quote", ""), batch[index]["text"][:1800]):
                audit.record(batch[index], "quote_not_found", row.get("evidence_quote", "")[:200])
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
                audit.record(batch[index], "no_valid_reasons",
                             str([(r["consequence_id"], r["attribute"]) for r in row["reasons"]])[:200])
                continue
            audit.record(batch[index], "kept", row["summary"][:120])
            source = batch[index]
            kept.append({"source": source["source"], "url": source["url"], "option_id": row["option_id"],
                "outcome": row["outcome"], "context": {k: v for k, v in row["context"].items() if k in situation_keys},
                "reasons": reasons, "summary": clean_text(row["summary"], 30), "months_after": row["months_after"]})
        for position, candidate in enumerate(batch):
            if position not in judged:
                audit.record(candidate, "omitted_by_model")
        return kept

    size = LIMITS["extraction_batch"]
    futures = [ctx.pool.submit(extract, candidates[i:i + size]) for i in range(0, len(candidates), size)]
    stories = []
    for future in as_completed(futures):
        stories.extend(future.result())
        ctx.progress(stories_kept=len(stories))
    audit.save(ctx.council["question"])
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
