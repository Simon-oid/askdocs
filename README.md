# askdocs

A question-answering service over a document corpus that **cites its sources**:
FastAPI with streamed answers (SSE), retrieval through an MCP server
([cMCP](https://github.com/Simon-oid/cMCP)'s `crag-mcp`, backed by
[cRAG](https://github.com/Simon-oid/cRAG)), a local LLM served by llama.cpp,
and an evaluation that blocks regressions in CI.

Status: under construction (P3 of a four-project portfolio). Every number in
this README will come from a script, with hardware and date.

## Planned

- `POST /ask` streams an answer with numbered citations of the retrieved
  passages; when the sources do not support an answer, it says so.
- LLM providers behind one thin interface: llama.cpp (local, default) and an
  OpenAI-compatible API, switched by configuration.
- Retrieval over MCP (why, and what it costs in latency, in
  `docs/architecture.md`).
- `evals/cases.jsonl`: 30–50 labelled cases; retrieval (Recall@k) measured
  separately from generation (faithfulness, local LLM judge checked against a
  hand-graded sample).
- Tracing (OpenTelemetry), P50/P95 latency, tokens per answer.
- One-command start with Docker Compose; an AWS deploy guide.
