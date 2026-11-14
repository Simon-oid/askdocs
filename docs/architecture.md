# Architecture

Why askdocs is built the way it is: the main decisions, each with what
it buys, what it costs, and the measurement or test behind it. The
mechanics are in the code and the README; this is the reasoning.

## The system

```
client ──HTTP/SSE──► askdocs (FastAPI, Python)
                       │  one long-lived MCP session, stdio
                       ├──► crag-mcp (C: cMCP server + cRAG engine) ──► docs.db (SQLite)
                       │                    └──HTTP──► Ollama (embedder)
                       └──HTTP (OpenAI chat format)──► llama.cpp (or a hosted model)
```

There are three processes besides the client, each in the language where it
is strongest:

- **The service:** HTTP, streaming, timeouts, evaluation glue, in Python.
- **Retrieval:** in C, the same engine as cRAG's CLI.
- **The model server:** llama.cpp.

## 1. Retrieval goes through MCP, not a library call

askdocs never links cRAG. It speaks MCP over stdio to `crag-mcp`, the cMCP
reference server that wraps cRAG, and calls its `crag_search` tool.

### What it buys

- **One retriever, several consumers.**
  - `crag-mcp` is an MCP server for any agent host. While this was being
    measured, another instance of the same binary was serving retrieval to
    an agent session on the same machine.
  - askdocs is one more client of the same tool, so retrieval was not
    integrated a second time.
  - The relevance gate, calibrated per embedder, lives in the server.
    Every consumer gets the same gate, and none can forget it.
- **Failure isolation.**
  - A crash in the C code kills `crag-mcp`, not the service with every
    stream it has open.
  - A supervisor task owns the session and reopens it, and a search that
    hits a dead session retries once on the new one.
  - `tests/test_retrieval.py::test_reconnects_after_server_crash` kills the
    server mid-session over real stdio and checks that the next search
    succeeds.
  - With an in-process binding (cffi), the same fault would be a segfault
    of the Python process.
- **A protocol boundary instead of an ABI.**
  - The service depends on a tool name, an input schema and a result
    format, not on a C struct layout or a compiled extension per Python
    version.
  - The retriever can be rebuilt, upgraded or replaced on its own.
- **A boundary you can see.** The MCP SDK traces the call, so every answer's
  trace shows `MCP send tools/call crag_search` under `retrieval`
  ([observability](observability.md)).

### What it costs, measured

[`bench/mcp_overhead.py`](../bench/mcp_overhead.py) times the 45 eval
questions × 4 rounds on the same index, 2026-11-09:
- **MCP path:** `McpRetriever.search`, what the service does.
- **Direct path:** the same work in-process. The question is embedded with
  Ollama, then searched with cRAG's hybrid search through `crag-index` (the
  same C engine via cffi), with the same relevance gate.

| | P50 ms | P95 ms |
|---|---|---|
| MCP path (all calls) | 13.11 | 15.96 |
| direct path (all calls) | 13.66 | 16.62 |
| MCP path, when it ran first | 12.22 | |
| direct path, when it ran first | 12.78 | |
| of which: embedding (Ollama) | 10.50 | 13.38 |
| of which: hybrid search | 2.75 | 3.52 |
| MCP round trip alone (`crag_stats`) | 0.37 | 0.46 |

Both paths returned the same passages for all 180 retrievals.

- **The protocol costs about 0.4 ms per question.** That is about 0.05% of
  a P50 answer (683 ms), and below the noise of the end-to-end comparison:
  the MCP path was not slower than the in-process one.
- **Retrieval time is the embedder.** About 80% of it is Ollama computing
  the query vector. The search over 524 chunks takes under 3 ms.
- **A trap the benchmark had to avoid.** The second call on the same text
  is about 3 ms slower than the first, whichever path makes it. The first
  version of the benchmark always ran MCP first, and so "showed" MCP 3.7 ms
  *faster*. The paths now alternate, and the table also compares each path
  only when it ran first.

The other costs are not latency:
- **One more process,** about 12 MB of RSS and 6 threads (main, reader, 4
  workers).
- **Supervision code.** anyio requires a task group to be exited by the
  task that entered it, so a request can never open or close the session.
  One task owns the session, and requests only borrow it.
- **Structured results, text as fallback.**
  - `crag_search` returns `structuredContent` (`{status, results: [{source,
    cosine, bm25, fusion, text}]}`), declared by its `outputSchema`; the MCP
    SDK validates it, and askdocs reads the fields directly.
  - `status` also tells an empty index (`empty_index`, a broken deployment:
    a retrieval error) from a query nothing matched (an empty result).
  - A crag-mcp older than cMCP `17599fb` sends only text items (`[cos …
    bm25 … fusion …] path\n body`), which askdocs still parses with a regex.
    Either way a malformed result is an error, never silently dropped.
- **Deployment.** The image has to build the C code, and the index has to
  be mounted read-write, since SQLite's WAL mode writes `docs.db-shm` even
  for readers ([deploy](deploy-aws.md)).

### When this would be the wrong choice

- **Retrieval in a tight loop.** If something ran thousands of retrievals
  per answer (a reranker over many candidate queries, say), 0.4 ms each
  would add up, and a batch tool or an in-process call would be justified.
- **A latency budget where embedding is not the floor.** Here the embedder
  sets the floor: 10 ms against 0.4 ms.

## 2. The answer is a stream of events

The pipeline is an async generator of events: `sources`, then `token` many
times, then `done` or `error`. The HTTP layer maps them 1:1 to SSE, and the
evals consume them directly.

- **What it buys.**
  - The user sees text after the first token, P50 about 450 ms instead of
    about 690 ms for the full answer.
  - The citations, refusal, usage, cost and trace id arrive in one final
    event.
  - Evals and the service exercise the same code.
- **What it costs.**
  - A disconnect has to cancel generation explicitly. A JSON request also
    polls for it, since Starlette does not cancel a plain handler.
  - Retries are only allowed before the first token, since after that a
    retry would duplicate text the client already shows.
  - Tracing cannot make spans "current" across a `yield`
    ([observability](observability.md)).

## 3. Refuse instead of queueing, and the limit equals the model's slots

Past `ASKDOCS_MAX_CONCURRENT` answers in flight, the service answers `503` +
`Retry-After` at once. Measured ([load test](load-test.md)):

- **The limit equal to llama.cpp's 4 slots** gave 1.78 answers/s and a time
  to first token of about 0.5 s at any load. The excess was refused.
- **A limit of 8 on 4 slots** gave the same throughput and a time to first
  token of 2.6 s. The extra answers waited in llama.cpp's queue, where the
  client cannot see them.
- **The service is prefill-bound.** Each answer needs about 0.46 s of GPU
  to read its 2000-token prompt. More concurrency does not add throughput,
  so a fast, honest 503 that a load balancer can route elsewhere beats a
  hidden queue.

The same test found that llama.cpp's `-c` is the context of all slots
together, so it is now `-c 16384 -np 4`.

## 4. Refusing is decided twice

- **In retrieval.** No passage clears the relevance gate, so the model is
  never called. That costs nothing and cannot hallucinate.
- **In generation.** Passages exist but lack the answer, and the model is
  told to answer with one fixed sentence, which the pipeline detects.

The evals score both: correct refusals 10/10, wrong refusals 0.03
([report](../evals/results/report.md)).

## 5. Evaluation measures retrieval and generation apart

- **Retrieval** recall@k is measured with a separate search at k=10.
- **Context recall:** what actually reached the model.
- **Key facts and faithfulness** for the answer, the latter with an LLM
  judge whose agreement with independent grades is measured (κ 0.80).

A drop in the answer score can then be traced to the stage that caused it.
The CI gate compares runs case by case and requires a drop beyond the
measured run-to-run noise: without that margin, an identical rerun was
flagged as a regression.

## 6. Everything that can be pinned is pinned

These are fixed:
- the corpus commits;
- the index embedder;
- the llama.cpp build (`b11749`);
- the Ollama and Python images;
- the eval seed.

A model server or an index that changes underneath changes the numbers.
That happened once in a measurable way. The index rebuilt in the container
was embedded on CPU instead of GPU: cosine 0.99998 against the original,
which was still enough to flip 3 of 45 near-tied rankings. A baseline holds
only for the index it was measured on.

## Index of the measurements

| What | Where |
|---|---|
| eval baseline, judge agreement, gate | [`evals/results/report.md`](../evals/results/report.md), README "Evaluation" |
| per-stage latency, tokens, cost, tracing overhead | [observability](observability.md) |
| concurrency, throughput, the context bug | [load test](load-test.md) |
| containerized stack, AWS | [deploy-aws](deploy-aws.md) |
| what MCP costs | this page, [`bench/mcp_overhead.py`](../bench/mcp_overhead.py) |
