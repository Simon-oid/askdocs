# Changelog

All notable changes to askdocs. Dates are ISO 8601.

## Unreleased

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
