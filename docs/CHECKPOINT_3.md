# Checkpoint 3

## What is built

Phases 0–3 are implemented on SQLite. The Flask room and the separate worker run
planning → scouting → extraction → cohorts → three debate rounds → moderator swap
tests and evidence checks → at most one scored question → a full verdict.

Phase 3 adds visitor profiles that prefill situational facts and show "The council
remembers", a 24-hour story cache, the metrics footer, low-precedent mode, and the
Supabase drop-in backend (`001_init.sql`, `supabase_store.py`, `check_supabase.py`,
`copy_to_supabase.py`). The Supabase files are written but not live-tested and are
never on the demo's critical path; `DB_BACKEND=sqlite` stays the default.

41 tests pass, including a full offline pipeline, low-precedent mode, answer/resume
through the real worker and storage path, memory isolation, cache taxonomy matching,
and retrieval calibration against the real embedding model.

## The honest limitation: live retrieval

Runs still end with too few stories to seat two cohorts, so the live demo reaches
the low-precedent verdict rather than a debate. This is a source-breadth problem,
not a pipeline failure — every stage after extraction is exercised by tests, and
the low-precedent path is itself a designed outcome rather than a crash.

Measured across audited live runs of the startup dilemma:

| run | candidates | stories kept | note |
|---|---|---|---|
| 59931336 | 98 | 1 | question-only ranking, AND-joined queries |
| e8ff3daa | 69 | 2 | 48% of candidates were off-site (a vendor, a charity) |
| bd6a1e42 | 60 | 4 | Reddit-only after the hostname filter |
| ada4a411 | 93 | 2 | extractor truncation fixed |
| 30bdf7b8 | 70 | 0 | one Reddit run per query pair (reverted): no more candidates than before, and every extraction batch then failed validation |
| ecd4cef5 | n/a | 2 | extractor fix applied: 0 validation fallbacks; retrieval is still the limit |
| f008a4b4 | n/a | 3 | subreddit-targeted search added; still under the 5-story cohort floor |

What the audits established, in order:

1. Ranking candidates by similarity to the **question** promoted restatements of the
   dilemma and generic advice over short first-person outcomes. Measured on labelled
   examples, it kept 2 of 4 real outcomes at the 0.55 floor; ranking against the
   plan's outcome queries keeps 4 of 4.
2. The planner was told to join terms with `AND`, producing queries Reddit answers
   with unrelated threads.
3. The RAG browser treats `site:` as advisory. One run drew 22 candidates from a
   vendor named Worth and 11 from a cancer charity, both matched on "worth it".
4. The extractor's reply budget could not hold six records containing quotes of up
   to 1200 characters, so 45% of candidates came back with no record at all. After
   capping the quote and raising the budget, that fell to 18%.
5. Reddit ANDs every search term, so the long phrases the web fallback needs match
   almost no threads. Reddit now receives the distinctive content words instead.

**What remains.** Candidates still come from only five or six distinct threads, and
`not_relevant` is now the dominant rejection. Subreddit targeting is now in (commit 890b91f) and lifted the count only from 2 to 3.
The housing demo council was not re-run, since it would hit the same ceiling.
Proposed fallback: keep live runs as the low-precedent showcase and demo from
featured replays once curated stories exist; the alternative is a larger scrape budget. Nothing was lowered to
manufacture a debate: `min_cohort` stays at 5 and no synthetic stories exist in demo
data, per the spec's rule that every number traces to stored data.

## Two-minute demo checklist

**Before the room** — two terminals, virtual environment active in both:

```sh
flask --app app --debug run --port 5050
python -m engine.worker
```

Confirm the header does not say "Worker offline", and that the home page lists the
featured councils.

**The run, in order:**

1. **Home page (15s).** One input, three example dilemmas, featured councils for
   instant replay. Say what Precedent does: it finds people who already made this
   choice and wrote about how it went.
2. **Open a featured council (30s).** This replays instantly and is the safe path —
   it does not depend on live scraping. Point at the pipeline strip: stories found →
   kept → cohorts → round.
3. **The voices (20s).** One card per cohort, each on a different model family, each
   showing how many real stories it speaks for. The stance meters move as beliefs
   change, with a faint marker at the previous position.
4. **The transcript (30s).** Open a `[S3]` chip to show the paraphrase and its source
   link — every claim traces to a stored story. Show a `rejected_update` turn if the
   run has one: an agent tried to change its mind without new evidence and the code,
   not the model, blocked it.
5. **The question (15s).** One question, asked only when an answer would change the
   recommendation. The "why we're asking" line names the agents who agree on the
   facts but weigh them differently.
6. **The verdict (20s).** Recommendation and confidence, most people online versus
   people like you, the crux, the strongest surviving dissent, one cheap test to run
   this week, and receipts.

**If asked about live runs.** Start one, then say plainly that source breadth is the
open problem and show the low-precedent verdict: the council says it does not have
enough evidence rather than inventing a debate. That is the designed behaviour and
the audit trail above shows the work.

**Do not** start a live council as the primary demo path. Use a featured replay.
