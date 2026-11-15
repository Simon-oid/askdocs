# askdocs eval report

askdocs `v0.1.0-12-g63e13c9`; corpus: cMCP cdb8852, cRAG a3d4815, embedder mxbai-embed-large; answer model: `local` at http://localhost:8081/v1; judge: `qwen2.5-7b-instruct-q4_k_m`; top_k 5; run 2026-11-15 12:31.

Means with bootstrap 95% intervals (10,000 resamples of the cases, seed 20261109).

| Metric | Mean [95% CI] | n |
|---|---|---|
| Retrieval recall@1 (answerable) | 0.61 [0.46, 0.76] | 35 |
| Retrieval recall@3 (answerable) | 0.94 [0.87, 1.00] | 35 |
| Retrieval recall@5 (answerable) | 0.99 [0.96, 1.00] | 35 |
| Retrieval recall@10 (answerable) | 0.99 [0.96, 1.00] | 35 |
| Context recall: right file among the passages the model got | 0.94 [0.87, 1.00] | 35 |
| Key-fact recall (answerable; refusal = 0) | 0.77 [0.63, 0.90] | 35 |
| Key facts present in the passages given (answerable) | 0.99 [0.96, 1.00] | 35 |
| Wrong refusal (answerable) | 0.06 [0.00, 0.14] | 35 |
| Correct refusal (unanswerable + off-topic) | 1.00 [1.00, 1.00] | 10 |
| Faithfulness: judged sentences supported (per answer) | 0.82 [0.69, 0.92] | 33 |
| Answers with an out-of-range citation | 0.00 [0.00, 0.00] | 45 |
| Answers citing nothing (answered, not refused) | 0.00 [0.00, 0.00] | 33 |
| Key-fact recall, factual only | 0.85 [0.72, 0.97] | 30 |
| Key-fact recall, multi only | 0.30 [0.00, 0.70] | 5 |

## Latency (informational: one client, sequential)

Nearest-rank percentiles. ttft and generation exist only for answers that
called the model (not for refusals with no passages).

| Stage | P50 ms | P95 ms | n |
|---|---|---|---|
| retrieval_ms | 16 | 17 | 45 |
| ttft_ms | 425 | 531 | 43 |
| generation_ms | 667 | 1187 | 43 |
| total_ms | 677 | 1204 | 45 |

## Tokens and cost per answer

| Per answer | P50 | P95 | n |
|---|---|---|---|
| prompt_tokens | 2023 | 2520 | 43 |
| completion_tokens | 42 | 114 | 43 |

No token prices set (ASKDOCS_LLM_PRICE_*_PER_MTOK): a local model's cost per answer is GPU time, the generation_ms above.

## Cases that missed something

- `f02` (factual): faithfulness 0.00
- `f07` (factual): key facts 0.00 (all in the passages: the model missed them); faithfulness 0.00
- `f09` (factual): key facts 0.50 (all in the passages: the model missed them); faithfulness 0.00
- `f11` (factual): faithfulness 0.50
- `f13` (factual): key facts 0.00 (all in the passages: the model missed them)
- `f15` (factual): faithfulness 0.00
- `f20` (factual): faithfulness 0.67
- `f21` (factual): refused
- `f22` (factual): refused
- `m01` (multi): key facts 0.00 (all in the passages: the model missed them)
- `m02` (multi): faithfulness 0.50
- `m03` (multi): key facts 0.50 (all in the passages: the model missed them)
- `m04` (multi): key facts 0.00 (passages held 0.50); faithfulness 0.75
- `m05` (multi): key facts 0.00 (all in the passages: the model missed them); faithfulness 0.50; recall@5 0.50
