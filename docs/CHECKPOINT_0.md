# Checkpoint 0 — Precedent

Phase 0 foundation is implemented. Phase 1 has not started.

## Local validation

- Python 3.12.14 virtual environment with the nine permitted dependencies.
- `pytest -q`: 11 passed; one upstream apify-shared deprecation warning.
- `pip check`: no broken requirements.
- Source compilation passed; docs/SPEC.md matches SPEC_final.md byte for byte.
- SQLite tables initialized; WAL, atomic claims, answer ownership and content storage tested.
- Required Featherless, Apify and Flask secret variables are set; Supabase service-role key is missing and is not needed for SQLite.
- `.env`, `precedent.env`, the database, caches and downloaded weights are ignored by Git.

## Featherless checks

| Model | Result | Elapsed |
| --- | --- | --- |
| Qwen/Qwen2.5-7B-Instruct | Pass | 1.89s |
| meta-llama/Meta-Llama-3.1-8B-Instruct | HTTP 403 | 0.49s |
| Qwen/Qwen2.5-14B-Instruct | Pass | 2.23s |
| google/gemma-3-12b-it | HTTP 403 | 0.24s |
| mistralai/Mistral-Nemo-Instruct-2407 | Pass | 3.3s |
| Qwen/Qwen2.5-32B-Instruct | HTTP 500 | 4.09s |

The Qwen 32B check includes a retry; two consecutive HTTP 500 responses stopped further completion probes. Gemma 27B was not probed.

Resolved extractor: Qwen 7B. Resolved moderator fallback: Qwen 14B. Verified agent candidates: Qwen 14B, Mistral Nemo, Qwen 7B (three distinct models, two families). Four-family readiness is **not** satisfied.

Read-only catalog checks confirm Llama 8B and Gemma 12B are on the account plan but gated. Their HTTP 403 responses are consistent with missing gated-model authorization. Enable their access through the connected Hugging Face account, then rerun the model check. See [Featherless access documentation](https://featherless.ai/docs/api-reference-models). No key values or raw error responses are stored.

## Apify checks

### reddit

- Actor run succeeded in 111.26s; 20 items returned.
- Normalized: 18 usable candidates (6 posts, 12 comments).
- Post field names: `body`, `communityName`, `createdAt`, `dataType`, `html`, `id`, `parsedCommunityName`, `parsedId`, `scrapedAt`, `title`, `url`, `username`.
- Comment field names: `body`, `category`, `communityName`, `createdAt`, `dataType`, `html`, `id`, `parsedCommunityName`, `parsedId`, `postId`, `scrapedAt`, `title`, `url`, `username`.

### rag

- Actor run succeeded in 19.67s; 1 items returned.
- Field names: `crawl`, `metadata`, `searchResult`, `text`.

The first client reads timed out after 15s while both actors continued successfully. The SDK uses a blocking long-poll: its HTTP timeout must exceed `wait_secs`. This has been fixed and regression-tested. Outputs were recovered with `python scripts/smoke_apify.py --read-last`; no repeat actor runs were launched. Raw Reddit bodies and usernames were not persisted. Seeing `username` in field names does not mean its values are stored.

## Embedding check — unresolved

`fastembed` and its ONNX runtime installed successfully. The public
`BAAI/bge-small-en-v1.5` tokenizer/config files downloaded, but the model-weight
transfer stalled at zero bytes using both the default Xet transfer and standard
HTTP. Both task-owned processes were stopped; no background download remains.
The model has **not** loaded or produced verified embeddings. The check script
now bounds future download/load attempts to 120 seconds.

Per section 10, no replacement dependency was installed. Restore access to the
public model-weight download or approve the specified sentence-transformers
fallback before relying on embeddings. The fallback also needs a model download,
so fixing download access is preferable.

## Next phase

Phase 1 builds the Flask UI and worker pipeline: planning, scouting, mining, cohorts, evidence-gated debate and a basic verdict. The spec requires review at this checkpoint before continuing.
