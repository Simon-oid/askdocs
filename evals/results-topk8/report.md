# askdocs eval report

askdocs `v0.1.0-19-g5c3aa17-dirty`; corpus: cMCP cdb8852, cRAG a3d4815, embedder mxbai-embed-large; answer model: `local` at http://localhost:8081/v1; judge: `qwen2.5-7b-instruct-q4_k_m`; top_k 8; run 2026-11-17 03:42.

Means with bootstrap 95% intervals (10,000 resamples of the cases, seed 20261109).

| Metric | Mean [95% CI] | n |
|---|---|---|
| Retrieval recall@1 (answerable) | 0.57 [0.43, 0.70] | 43 |
| Retrieval recall@3 (answerable) | 0.85 [0.76, 0.93] | 43 |
| Retrieval recall@5 (answerable) | 0.92 [0.85, 0.98] | 43 |
| Retrieval recall@10 (answerable) | 0.94 [0.90, 0.99] | 43 |
| Context recall: right file among the passages the model got | 0.93 [0.87, 0.98] | 43 |
| Key-fact recall (answerable; refusal = 0) | 0.81 [0.70, 0.92] | 43 |
| Key facts present in the passages given (answerable) | 0.94 [0.90, 0.99] | 43 |
| Wrong refusal (answerable) | 0.14 [0.05, 0.26] | 43 |
| Correct refusal (unanswerable + off-topic) | 1.00 [1.00, 1.00] | 10 |
| Faithfulness: judged sentences supported (per answer) | 0.83 [0.73, 0.92] | 37 |
| Answers with an out-of-range citation | 0.02 [0.00, 0.06] | 53 |
| Answers citing nothing (answered, not refused) | 0.00 [0.00, 0.00] | 37 |
| Key-fact recall, factual only | 0.93 [0.83, 1.00] | 30 |
| Key-fact recall, multi only | 0.54 [0.31, 0.77] | 13 |

## Latency (informational: one client, sequential)

Nearest-rank percentiles. ttft and generation exist only for answers that
called the model (not for refusals with no passages).

| Stage | P50 ms | P95 ms | n |
|---|---|---|---|
| retrieval_ms | 31 | 57 | 53 |
| ttft_ms | 1980 | 2668 | 51 |
| generation_ms | 2782 | 4864 | 51 |
| total_ms | 2813 | 4893 | 53 |

## Tokens and cost per answer

| Per answer | P50 | P95 | n |
|---|---|---|---|
| prompt_tokens | 3081 | 3464 | 51 |
| completion_tokens | 39 | 82 | 51 |

No token prices set (ASKDOCS_LLM_PRICE_*_PER_MTOK): a local model's cost per answer is GPU time, the generation_ms above.

## Cases that missed something

- `f07` (factual): faithfulness 0.00
- `f09` (factual): faithfulness 0.00
- `f11` (factual): faithfulness 0.50
- `f13` (factual): faithfulness 0.70
- `f19` (factual): faithfulness 0.62
- `f20` (factual): refused
- `f22` (factual): refused
- `f30` (factual): faithfulness 0.50
- `m02` (multi): faithfulness 0.50
- `m03` (multi): refused
- `m04` (multi): faithfulness 0.50
- `m05` (multi): key facts 0.50 (all in the passages: the model missed them); recall@5 0.50
- `m06` (multi): key facts 0.50 (all in the passages: the model missed them)
- `m07` (multi): faithfulness 0.00; recall@5 0.50
- `m09` (multi): refused; recall@5 0.50
- `m10` (multi): key facts 0.50 (passages held 0.50); recall@5 0.50
- `m11` (multi): refused
- `m12` (multi): refused; recall@5 0.50
- `m13` (multi): key facts 0.50 (passages held 0.50); faithfulness 0.50; recall@5 0.00
