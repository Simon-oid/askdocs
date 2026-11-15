# Changelog

All notable changes to askdocs. Dates are ISO 8601.

## Unreleased

- `askdocs.access`: optional API keys (`ASKDOCS_API_KEYS`, Bearer, constant-
  time compare; `/healthz` stays open) and a per-client token bucket
  (`ASKDOCS_RATE_PER_MINUTE`, `ASKDOCS_RATE_BURST`; per key, or per address
  without keys; at most 10,000 clients tracked). New responses: `401`,
  `429` with `Retry-After`. Both off by default.
- `askdocs.attribution`: after generation, each sentence is checked against
  the passages by content-word overlap. A sentence whose citation points at
  the wrong passage is re-cited to the one that holds it; an uncited copy
  of a passage gets its citation; a sentence tied to no passage is dropped
  (an answer with nothing left becomes a refusal, `refusal_reason:
  "unsupported"`). `done` carries `model_answer` and `attribution`
  (`recited`, `dropped`); `ASKDOCS_ATTRIBUTION=false` turns it off.
  `evals/reattribute.py` applies it to a saved run, and `evals/injection.py`
  reports each attack served and unchecked, on the same answers.
  Measured with the 3B: faithfulness 0.60 → 0.83 on the same saved answers
  (+0.23 [+0.11, +0.35], key facts unchanged); the "append a line"
  injection 4–5 of 6 → 0 of 6 in three runs.
- New baseline in `evals/results/` (the first of four runs with the
  check): key facts 0.77, faithfulness 0.82. Gate: faithfulness floor
  0.35 → 0.60, noise from two sets of four runs (larger kept), the
  key-fact and wrong-refusal floors not loosened (`gate.json`).
- Citations in the `【n】` / `【n†L1-L3】` form are read as `[n]`
  (`prompt.normalize_citations`), in `done.answer`, `done.citations` and
  the eval's claim split. The streamed tokens stay the model's own.
- Gate metric `uncited`: the share of answered (not refused) cases whose
  answer has no readable citation, with a ceiling of 0.10. `bad_citation`
  counted only out-of-range numbers, so an answer citing nothing scored as
  clean. The 3B is at 1 of 34; gpt-oss-120b was at 9 of 34 before the
  parser read its form.
- Fact and refusal checks fold typography (`prompt.fold_typography`: NFKC,
  Unicode hyphens, quotes, no-break spaces). gpt-oss writes `bge‑m3` with
  U+2011 and lost 3 key-fact cases to it (0.83 → 0.90). The 3B's committed
  results are unchanged.
- Hosted baseline: gpt-oss-120b (Groq free tier, reasoning low) on the 45
  cases, judged by the local 7B, in `evals/results-gptoss120b/`. Level with
  the 3B on key facts (0.90 vs 0.87); faithfulness 0.87 vs 0.60 once its
  `【n】` citations (9 of 34 answers, unread by the citation parser, which
  made the judge mark those sentences unsupported: 0.63 as served) are
  normalised and re-judged (`evals/results-gptoss120b-normcites/`).
  `evals/hosted_proxy.py` paces requests under the free tier's token limit.

- `evals/injection.py`: a prompt-injection run. One poisoned passage
  among the real ones, six attacks at first and last rank, success checked
  deterministically (canary, link, refusal, format), no judge. With the 3B,
  6–7 of 72 attacks succeed, nearly all by appending a line; see
  `KNOWN_LIMITATIONS.md`. The attacks and detectors (`askdocs.injection`)
  are unit-tested in CI.
- Prompt: runs of three or more angle brackets inside a fence are shortened
  to two, so a passage cannot even look like a fence end. Measured effect is
  within run-to-run noise (fence_forge last: 5/6 before, 4/6 and 6/6 after). No corpus passage or eval question
  contains one, so the main eval's prompts are unchanged.

- Retrieval reads crag-mcp's `structuredContent` (cMCP `17599fb`) instead
  of regex-parsing the text items; the regex stays as the fallback for an
  older crag-mcp. An empty index fails with a message saying so (it was
  an "unexpected crag_search item").
- Docker image builds crag-mcp at cMCP `17599fb`. The `CPATH` workaround
  for the SQLite header is gone (fixed upstream in `3c0e5d4`). The corpus
  pins, and so the eval baseline, are unchanged.

## 0.1.0 — 2026-11-10

First release: the service, its evaluation, and what it takes to run it.

### Service
- `POST /ask`: retrieval through an MCP server (`crag-mcp` over stdio, one
  supervised long-lived session), a grounded prompt, and a streamed, cited
  answer over SSE; `?stream=false` for one JSON envelope.
- Refusal in two places: no passage clears the relevance gate, or the model
  answers with the fixed refusal sentence.
- Citations checked: `citations` and `invalid_citations` in the final event.
- Bounded failure: per-chunk and total timeouts, retries only before the
  first token, one reconnect of the MCP session, `503` past
  `MAX_CONCURRENT` answers in flight, `413` for bodies over 16 KB.
- A client that disconnects stops the generation, in both modes.
- A stream always ends with `done` or `error`; an unexpected failure is
  `error {stage: internal}` and `500` in JSON mode.
- One background retrieval at startup, so the embedder is warm.
- Two LLM providers behind one interface: llama.cpp and any
  OpenAI-compatible API.

### Evaluation
- 45 labelled cases; retrieval recall@k, context recall, key-fact recall,
  refusals, and faithfulness by an LLM judge, each with a bootstrap interval.
- Judge agreement measured on a sample (κ 0.80).
- CI gate: committed results must cover the case set and clear the floors;
  a paired, noise-aware comparison flags regressions locally.

### Operations
- OpenTelemetry tracing (one trace per answer), `GET /stats` with P50/P95
  per stage, tokens and cost per answer.
- Docker Compose stack (CPU, ROCm, CUDA), an image that builds cRAG and
  crag-mcp from source, and an AWS guide.
- Locust load test; `docs/architecture.md`, `docs/observability.md`,
  `docs/load-test.md`, `docs/deploy-aws.md`, `KNOWN_LIMITATIONS.md`.
