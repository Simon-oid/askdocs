# askdocs eval report

askdocs `v0.1.0-19-g5c3aa17-dirty`; corpus: cMCP cdb8852, cRAG a3d4815, embedder mxbai-embed-large; answer model: `local` at http://localhost:8081/v1; judge: `qwen2.5-7b-instruct-q4_k_m`; top_k 10; run 2026-11-17 03:45.

Means with bootstrap 95% intervals (10,000 resamples of the cases, seed 20261109).

| Metric | Mean [95% CI] | n |
|---|---|---|
| Retrieval recall@1 (answerable) | 0.57 [0.43, 0.70] | 43 |
| Retrieval recall@3 (answerable) | 0.85 [0.76, 0.93] | 43 |
| Retrieval recall@5 (answerable) | 0.92 [0.85, 0.98] | 43 |
| Retrieval recall@10 (answerable) | 0.94 [0.90, 0.99] | 43 |
| Context recall: right file among the passages the model got | 0.94 [0.90, 0.99] | 43 |
| Key-fact recall (answerable; refusal = 0) | 0.78 [0.66, 0.88] | 43 |
| Key facts present in the passages given (answerable) | 0.98 [0.94, 1.00] | 43 |
| Wrong refusal (answerable) | 0.14 [0.05, 0.26] | 43 |
| Correct refusal (unanswerable + off-topic) | 1.00 [1.00, 1.00] | 10 |
| Faithfulness: judged sentences supported (per answer) | 0.73 [0.61, 0.84] | 37 |
| Answers with an out-of-range citation | 0.00 [0.00, 0.00] | 53 |
| Answers citing nothing (answered, not refused) | 0.00 [0.00, 0.00] | 37 |
| Key-fact recall, factual only | 0.83 [0.70, 0.95] | 30 |
| Key-fact recall, multi only | 0.65 [0.42, 0.85] | 13 |

## Latency (informational: one client, sequential)

Nearest-rank percentiles. ttft and generation exist only for answers that
called the model (not for refusals with no passages).

| Stage | P50 ms | P95 ms | n |
|---|---|---|---|
| retrieval_ms | 30 | 57 | 53 |
| ttft_ms | 2382 | 3505 | 51 |
| generation_ms | 3220 | 4888 | 51 |
| total_ms | 3234 | 4937 | 53 |

## Tokens and cost per answer

| Per answer | P50 | P95 | n |
|---|---|---|---|
| prompt_tokens | 3766 | 4166 | 51 |
| completion_tokens | 36 | 92 | 51 |

No token prices set (ASKDOCS_LLM_PRICE_*_PER_MTOK): a local model's cost per answer is GPU time, the generation_ms above.

## Cases that missed something

- `f02` (factual): faithfulness 0.50
- `f03` (factual): faithfulness 0.50
- `f06` (factual): faithfulness 0.00
- `f07` (factual): refused
- `f09` (factual): key facts 0.50 (all in the passages: the model missed them); faithfulness 0.00
- `f10` (factual): key facts 0.50 (all in the passages: the model missed them); faithfulness 0.00
- `f11` (factual): faithfulness 0.50
- `f15` (factual): key facts 0.00 (all in the passages: the model missed them); faithfulness 0.00
- `f19` (factual): faithfulness 0.67
- `f20` (factual): refused
- `f25` (factual): refused
- `m01` (multi): key facts 0.50 (all in the passages: the model missed them); faithfulness 0.50
- `m02` (multi): faithfulness 0.50
- `m03` (multi): refused
- `m05` (multi): recall@5 0.50
- `m06` (multi): faithfulness 0.50
- `m07` (multi): faithfulness 0.00; recall@5 0.50
- `m09` (multi): refused; recall@5 0.50
- `m10` (multi): key facts 0.50 (passages held 0.50); faithfulness 0.50; recall@5 0.50
- `m11` (multi): refused
- `m12` (multi): faithfulness 0.50; recall@5 0.50
- `m13` (multi): key facts 0.50 (all in the passages: the model missed them); faithfulness 0.50; recall@5 0.00
