# askdocs eval report

askdocs `v0.1.0-6-gd176ec7`; corpus: cMCP cdb8852, cRAG a3d4815, embedder mxbai-embed-large; answer model: `local` at http://localhost:8081/v1; judge: `qwen2.5-7b-instruct-q4_k_m`; top_k 5; run 2026-11-15 11:39.

Means with bootstrap 95% intervals (10,000 resamples of the cases, seed 20261109).

| Metric | Mean [95% CI] | n |
|---|---|---|
| Retrieval recall@1 (answerable) | 0.61 [0.46, 0.76] | 35 |
| Retrieval recall@3 (answerable) | 0.94 [0.87, 1.00] | 35 |
| Retrieval recall@5 (answerable) | 0.99 [0.96, 1.00] | 35 |
| Retrieval recall@10 (answerable) | 0.99 [0.96, 1.00] | 35 |
| Context recall: right file among the passages the model got | 0.94 [0.87, 1.00] | 35 |
| Key-fact recall (answerable; refusal = 0) | 0.90 [0.80, 0.99] | 35 |
| Wrong refusal (answerable) | 0.03 [0.00, 0.09] | 35 |
| Correct refusal (unanswerable + off-topic) | 1.00 [1.00, 1.00] | 10 |
| Faithfulness: judged sentences supported (per answer) | 0.87 [0.75, 0.96] | 34 |
| Answers with an out-of-range citation | 0.00 [0.00, 0.00] | 45 |
| Key-fact recall, factual only | 0.92 [0.82, 1.00] | 30 |
| Key-fact recall, multi only | 0.80 [0.40, 1.00] | 5 |

## Latency (informational: one client, sequential)

Nearest-rank percentiles. ttft and generation exist only for answers that
called the model (not for refusals with no passages).

| Stage | P50 ms | P95 ms | n |
|---|---|---|---|
| retrieval_ms | 17 | 18 | 45 |
| ttft_ms | 29864 | 30189 | 43 |
| generation_ms | 29967 | 30287 | 43 |
| total_ms | 29922 | 30303 | 45 |

## Tokens and cost per answer

| Per answer | P50 | P95 | n |
|---|---|---|---|
| prompt_tokens | 1999 | 2458 | 43 |
| completion_tokens | 87 | 196 | 43 |

No token prices set (ASKDOCS_LLM_PRICE_*_PER_MTOK): a local model's cost per answer is GPU time, the generation_ms above.

## Cases that missed something

- `f09` (factual): faithfulness 0.00
- `f13` (factual): key facts 0.00
- `f15` (factual): key facts 0.67; faithfulness 0.00
- `f16` (factual): faithfulness 0.00
- `f20` (factual): faithfulness 0.50
- `f30` (factual): key facts 0.00
- `m01` (multi): faithfulness 0.50
- `m02` (multi): faithfulness 0.50
- `m04` (multi): refused
- `m05` (multi): recall@5 0.50
