# askdocs

A question-answering service over a document corpus that **cites its sources**:
FastAPI with streamed answers (SSE), retrieval through an MCP server
([cMCP](https://github.com/Simon-oid/cMCP)'s `crag-mcp`, backed by
[cRAG](https://github.com/Simon-oid/cRAG)), a local LLM served by llama.cpp,
and an evaluation that blocks regressions in CI.

Status: the service works end to end (phase 1 of P3 in a four-project
portfolio); evaluation, CI gate, tracing and deployment come next. Every
number in this README will come from a script, with hardware and date.

## How an answer is made

```
POST /ask ──► crag_search over MCP (stdio) ──► passages [1..k]
                 │ nothing clears the relevance gate?
                 └──► "I don't know" — the model is never called
          ──► prompt: numbered sources + rules ──► LLM (streamed)
          ──► SSE: sources, token…, done {citations, refused, usage, timings}
```

- **Citations are checked, not trusted.** `done.citations` lists the source
  numbers the answer uses; `done.invalid_citations` lists numbers that point
  at no source (a fabrication the eval counts).
- **Refusing is a first-class answer.** Off-topic questions are refused in
  the retrieval step, without paying for a generation; when passages exist
  but do not contain the answer, the model is told to reply with one fixed
  sentence, and `done.refused` reports it.
- **Failures are bounded.** Per-chunk read timeout on the model stream;
  retries (exponential backoff) only before the first token reaches the
  client; one reconnect if the MCP server dies; at most
  `ASKDOCS_MAX_CONCURRENT` answers in flight, then `503` with `Retry-After`.
- **Incomplete is not short.** A model stream that ends without `[DONE]` or
  a finish reason, or carries an error chunk, is an `error`, never a
  truncated "answer"; an answer cut by `max_tokens` has `done.truncated`.
- **Documents are data.** Sources and the question sit between per-request
  random fences that their text cannot close, so a document cannot forge a
  source header or an instruction.
- **A client that disconnects stops the generation**, in both modes: the
  SSE stream is closed at once; a JSON request (`?stream=false`) polls for
  the disconnect and cancels. Either way the upstream stream closes and
  llama.cpp cancels the task (tested with raw ASGI disconnects, and checked
  against a real llama.cpp server).
- **Bounded input.** Request bodies over 16 KB get `413` before they are
  read into memory, on Content-Length and on the bytes actually received
  (chunked uploads); questions over 2000 characters get `422`.

## Quick start

```sh
# 1. The models: an embedder in Ollama, an LLM in llama.cpp.
ollama pull mxbai-embed-large
llama-server -hf Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M --port 8081 -ngl 99 -c 8192

# 2. The demo corpus: cMCP + cRAG documentation at pinned commits, indexed by cRAG.
CRAG=/path/to/cRAG/crag scripts/build_corpus.sh

# 3. The service.
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env            # set ASKDOCS_MCP_COMMAND to your crag-mcp build
.venv/bin/askdocs --port 8000
```

```sh
curl -N localhost:8000/ask -H 'content-type: application/json' \
     -d '{"question": "How is the requestState sealed in cMCP?"}'
```

```
event: sources
data: {"sources": [{"n": 1, "source": "cMCP/CHANGELOG.md", "text": "...", "cosine": 0.683, ...}, ...]}

event: token
data: {"text": "The requestState is sealed with HMAC-SHA256 "}
...
event: done
data: {"answer": "...", "citations": [2, 3], "invalid_citations": [], "refused": false,
       "usage": {"prompt_tokens": 1594, "completion_tokens": 101},
       "timings": {"retrieval_ms": ..., "ttft_ms": ..., "generation_ms": ..., "total_ms": ...}}
```

`POST /ask?stream=false` returns the same as one JSON envelope
(`{"success", "data", "error"}`); `GET /healthz` reports load.

## API

| | |
|---|---|
| `POST /ask` | body `{"question": str (1–2000 chars), "k": int 1–20 (optional)}`; SSE by default, JSON with `?stream=false` |
| SSE events | `sources` → `token`* → `done`, or `error` `{stage: retrieval\|generation, message, partial?}` |
| Errors | `413` body over 16 KB · `422` bad input · `503` too many in flight · `502` (JSON mode) when a stage fails; details stay in the server log |
| `GET /healthz` | `{"status", "in_flight", "max_concurrent"}` |

## Configuration

Environment variables (or `.env`), all prefixed `ASKDOCS_`; see
[`.env.example`](.env.example) and [`src/askdocs/config.py`](src/askdocs/config.py),
where every limit carries its reason.

| Variable | Default | |
|---|---|---|
| `LLM_PROVIDER` | `llamacpp` | `llamacpp` or `openai` (any OpenAI-compatible API; needs `LLM_MODEL` and `LLM_API_KEY`) |
| `LLM_BASE_URL` | per provider | `http://localhost:8081/v1` / `https://api.openai.com/v1` |
| `LLM_MAX_TOKENS`, `LLM_TEMPERATURE` | `512`, `0.0` | temperature 0 keeps evals reproducible |
| `LLM_READ_TIMEOUT_S`, `LLM_MAX_RETRIES` | `60`, `2` | timeout per streamed chunk, not per answer |
| `MCP_COMMAND`, `MCP_DB` | `crag-mcp`, `data/docs.db` | the MCP server and its index |
| `EMBED_MODEL`, `EMBED_URL` | `mxbai-embed-large`, Ollama | must match the model the index was built with |
| `TOP_K`, `MAX_CONCURRENT` | `5`, `4` | |

## Tests

```sh
.venv/bin/pytest -q                      # hermetic: fake LLM, fake MCP server over real stdio
ASKDOCS_INTEGRATION=1 .venv/bin/pytest -m integration   # the real stack, running
```

The retriever is tested against a stand-in MCP server
([`tests/fake_crag_mcp.py`](tests/fake_crag_mcp.py)) spoken to over real
stdio, including a crash mid-session and the reconnect after it.

## Planned

- `evals/cases.jsonl`: 30–50 labelled cases; retrieval (Recall@k) measured
  separately from generation (faithfulness, local LLM judge checked against a
  hand-graded sample).
- A CI gate on the eval scores that accounts for their confidence interval.
- Tracing (OpenTelemetry), P50/P95 latency, tokens per answer.
- One-command start with Docker Compose; an AWS deploy guide.
- `docs/architecture.md`: why retrieval goes through MCP, and what it costs
  in latency, measured.

## License

MIT
