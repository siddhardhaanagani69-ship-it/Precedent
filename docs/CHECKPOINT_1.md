# Checkpoint 1

The runnable Flask/SQLite spine, separate worker, embeddings, three-round debate,
computed stances, citation gates, receipts and low-precedent verdict are implemented.
20 tests pass, including an offline full pipeline and low-precedent run. Browser
checks covered desktop/mobile layout, examples, polling, and source dialogs.

Latest live startup dilemma: 133.63s total; planning 10.92s, scouting 112.14s,
mining 10.56s. 22 candidates, zero supported outcome stories, no debate. Result:
“Not enough precedent”. This verifies degradation, not a successful live debate.
Read-only inspection confirmed the RAG actor succeeded but its Reddit page fetches
were blocked (403) and returned empty text. The fallback now stops after two
consecutive failures. No synthetic stories have been added to demo data.

Embeddings now pass locally. Qwen and Mistral work; the other requested model
families remain unavailable. The user's subsequent instruction to complete the
project authorizes proceeding with the remaining SQLite phases.
