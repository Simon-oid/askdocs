# askdocs eval report

askdocs `74f8589`; corpus: cMCP cdb8852, cRAG a3d4815, embedder mxbai-embed-large; answer model: `local` at http://localhost:8081/v1; judge: `qwen2.5-7b-instruct-q4_k_m`; top_k 5; run 2026-11-09 19:21.

Means with bootstrap 95% intervals (10,000 resamples of the cases, seed 20261109).

| Metric | Mean [95% CI] | n |
|---|---|---|
| Retrieval recall@1 (answerable) | 0.61 [0.46, 0.76] | 35 |
| Retrieval recall@3 (answerable) | 0.94 [0.87, 1.00] | 35 |
| Retrieval recall@5 (answerable) | 0.99 [0.96, 1.00] | 35 |
| Retrieval recall@10 (answerable) | 0.99 [0.96, 1.00] | 35 |
| Context recall: right file among the passages the model got | 0.94 [0.87, 1.00] | 35 |
| Key-fact recall (answerable; refusal = 0) | 0.87 [0.76, 0.96] | 35 |
| Wrong refusal (answerable) | 0.03 [0.00, 0.09] | 35 |
| Correct refusal (unanswerable + off-topic) | 1.00 [1.00, 1.00] | 10 |
| Faithfulness: judged sentences supported (per answer) | 0.60 [0.45, 0.75] | 34 |
| Answers with an out-of-range citation | 0.00 [0.00, 0.00] | 45 |
| Key-fact recall, factual only | 0.92 [0.82, 1.00] | 30 |
| Key-fact recall, multi only | 0.60 [0.30, 0.90] | 5 |

## Latency (informational: one client, sequential)

Nearest-rank percentiles. ttft and generation exist only for answers that
called the model (not for refusals with no passages).

| Stage | P50 ms | P95 ms | n |
|---|---|---|---|
| retrieval_ms | 16 | 18 | 45 |
| ttft_ms | 450 | 562 | 43 |
| generation_ms | 676 | 1187 | 43 |
| total_ms | 686 | 1203 | 45 |

## Tokens and cost per answer

| Per answer | P50 | P95 | n |
|---|---|---|---|
| prompt_tokens | 2009 | 2492 | 43 |
| completion_tokens | 33 | 89 | 43 |

No token prices set (ASKDOCS_LLM_PRICE_*_PER_MTOK): a local model's cost per answer is GPU time, the generation_ms above.

## Cases that missed something

- `f02` (factual): faithfulness 0.00
- `f03` (factual): faithfulness 0.00
- `f05` (factual): faithfulness 0.50
- `f06` (factual): faithfulness 0.00
- `f07` (factual): faithfulness 0.00
- `f09` (factual): key facts 0.50; faithfulness 0.00
- `f11` (factual): faithfulness 0.50
- `f13` (factual): key facts 0.00; faithfulness 0.25
- `f15` (factual): faithfulness 0.00
- `f16` (factual): faithfulness 0.00
- `f19` (factual): faithfulness 0.00
- `f20` (factual): faithfulness 0.25
- `f22` (factual): refused
- `f30` (factual): faithfulness 0.00
- `m01` (multi): key facts 0.50; faithfulness 0.50
- `m03` (multi): key facts 0.50; faithfulness 0.00
- `m04` (multi): key facts 0.00; faithfulness 0.50
- `m05` (multi): recall@5 0.50
