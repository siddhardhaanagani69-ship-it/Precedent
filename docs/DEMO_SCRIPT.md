# Precedent: 3-minute demo script

Fill in the bracketed names. Roughly 150 words per minute; the times below add up to 3:00.

**Before you hit record**
- Two terminals visible: web (`flask --app app --debug run --port 5050`) and worker (`MIN_COHORT=1 python -m engine.worker`). Keep the worker terminal on screen; its logs are the "console logs" the judges want.
- Browser on `http://localhost:5050/`. Housing council open in a second tab: `/c/61e1079f-7f3d-457b-a5e6-e3decaf58039`.
- Apify console runs page open in a third tab as proof of live scraping.
- Start the live run in Beat 1 at the top of the demo so the scout has time to work while you talk. Cut to the worker log when it finishes.

---

## 0:00–0:30 · Team intros (about 7 seconds each)

**[P1]:** "I'm [P1]. I built the scraping and the pipeline: Apify, planning and the worker."
**[P2]:** "I'm [P2]. I built extraction and the LLM layer on Featherless."
**[P3]:** "I'm [P3]. I built the debate engine and the stance math."
**[P4]:** "I'm [P4]. I built the web app, the room UI, and the tests."

*(Swap the roles to match who did what. Speaker labels below use P1–P4.)*

**Who speaks when:** P1 pitch, P4 demo beats 1 and 3, P2 demo beat 2, P3 demo beat 4, P1 close. Everyone speaks at least once; P1 speaks twice.

---

## 0:30–1:00 · The pitch (the trailer), spoken by P1

> "Every big decision has been made by thousands of people before you. They wrote about it, and almost nobody reads it. Advice online is opinion. What you actually want is outcomes: who chose this, and how did it go?
>
> Precedent finds real people who made the choice you're facing. It turns their stories into a council of agents, one per group, like 'took the offer and regrets it' or 'stayed and is glad'. Then it makes them debate, with every claim tied to a real source.
>
> It's for anyone stuck on a decision with no easy answer, and it never invents a story to fill a gap."

---

## 1:00–2:30 · Live demo (talk through it as it runs)

**Beat 1, 1:00–1:20, the input (P4).** On the home page:
> "One box. I type a real dilemma and hit Gather my council. Flask and Jinja serve this page, with vanilla JavaScript and no build step. The web app only writes to a database. A separate worker process picks the job up."

Type a question, submit. Switch to the worker terminal.

**Beat 2, 1:20–1:50, the pipeline, with console logs (P2 talks, P1 can point at Apify).** Show lines like `[council 3f2a] planning done in 12s`, `scouting done in 120s`, `mining done in 16s`.
> "The worker plans the decision with Featherless models. Then Apify scrapes Reddit for first-hand outcomes, with a RAG browser fallback. We embed candidates with fastembed and extract each story into a structured record: what they chose, how it went, and why. Every summary must copy a real quote from the source, or it's thrown away."

**Beat 3, 1:50–2:10, the room (P4).** Cut back to the browser to show the pipeline strip and the live counts (sources found, stories kept).
> "The strip is driven by the worker's progress writes, polled every second. Here's the honest part. Reddit only gave us a handful of usable stories for this question."

**Beat 4, 2:10–2:30, the verdict (P3).** Open the housing council tab and show the dark verdict panel: "Not enough precedent".
> "Precedent won't invent a debate. Below five stories per group, it says so and shows what's missing. It's a designed outcome, not a crash. In the full path, each cohort is an agent on a different model family. Their stances are computed from beliefs times weights, so the models never set a number. If an agent tries to change its mind without new evidence, the code blocks it."

Optionally flash the terminal with `pytest -q` showing 44 passed:
> "The full debate, moderator swap tests, and the one values question run end to end in our test suite."

---

## 2:30–3:00 · Close: so what? (P1, last line all four together)

> "Precedent is for anyone facing a hard decision: a career move, a big purchase, a switch of study. It matters because it replaces confident advice with real outcomes and visible reasoning.
>
> The hard problem we hit was source breadth. We fixed a token-budget bug that was silently emptying the extractor, and added a check that catches stories tagged with the wrong choice. Next is more sources beyond one Reddit scraper, and the Supabase backend we've written for shared, persistent memory.
>
> **All four:** "Precedent. Someone has been here before."

---

## Facts you can safely claim
- Stack: Python, Flask/Jinja, vanilla JS/CSS, SQLite, a separate worker process talking through the store.
- LLMs: Featherless only, several model families (Qwen, Mistral and others) in different roles.
- Scraping: Apify only (`trudax/reddit-scraper-lite`, `apify/rag-web-browser`).
- Embeddings: fastembed (BAAI/bge-small-en-v1.5).
- Stances are computed from beliefs times weights. The LLM never sets them.
- Blocked-update turns, moderator swap tests, and a fact table that counts both glad and regret stories are all implemented and tested. 44 tests pass.

## Do not claim
- Do not say the live demo produces a full multi-agent debate. It currently ends in the low-precedent verdict for most questions.
- Do not say Supabase is running. The files are written and untested, and the app runs on SQLite.
- Do not show or cite the old startup council (`b739531b`). Its stories were mislabeled.
