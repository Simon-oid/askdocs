# Observability

What askdocs records about each answer, where it goes, and what it costs.
Measured 2026-11-09 on one machine: Ryzen 7 5800X, RX 7800 XT, Qwen2.5-3B-Instruct
Q4_K_M on llama.cpp, `mxbai-embed-large` on Ollama, the 45 eval questions sent
one at a time over HTTP.

## Three views of the same answer

| View | Where | Answers |
|---|---|---|
| Trace per answer | OTLP → any collector (Jaeger here) | why was *this* request slow, where did it fail |
| Rolling percentiles | `GET /stats`, last 1000 answers | how slow are we *now*, with no metrics backend |
| Eval report | `evals/results/report.md` | latency, tokens and cost of the committed baseline |

All three read the same timings: the pipeline measures each stage once and
puts it in the `done` event, the span, and the stats window.

## The trace

Tracing is off unless `ASKDOCS_OTEL_ENDPOINT` is set, and then it needs the
`otel` extra (`pip install 'askdocs[otel]'`). Without it the OpenTelemetry
API is a no-op and the SDK is never imported.

```sh
docker run --rm -p 127.0.0.1:4318:4318 -p 127.0.0.1:16686:16686 jaegertracing/jaeger:2.11.0
ASKDOCS_OTEL_ENDPOINT=http://127.0.0.1:4318 askdocs     # UI on http://127.0.0.1:16686
```

One answer, as Jaeger received it (JSON mode):

```
POST /ask                                 +0.0 ms   664.2 ms   FastAPI, SERVER span
  fastapi.dependencies                    +0.4 ms     0.1 ms
  fastapi.endpoint                        +0.6 ms   663.3 ms
    askdocs.answer                        +0.8 ms   663.0 ms   outcome, citations, cost
      retrieval                           +0.8 ms    19.4 ms   k, passages returned
        MCP send tools/call crag_search   +1.4 ms    18.2 ms   the MCP SDK's span
        MCP send tools/list              +19.8 ms     0.2 ms   once per session
      generation                         +20.3 ms   643.3 ms   tokens, finish reason, first_token event
```

`askdocs.answer`, `retrieval` and `generation` are ours. `POST /ask` and the
`fastapi.*` spans come from FastAPI's built-in telemetry, and the `MCP send`
spans from the MCP Python SDK. Both appear because they use the same global
tracer provider. A `traceparent` header from the caller makes all of it part
of the caller's trace.

Attributes on `generation` follow the OpenTelemetry GenAI conventions
(`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
`gen_ai.response.finish_reasons`), which are still marked as in development.
The question text is never recorded, only its length, since a question may
contain personal data. `done` and `error` events carry the `trace_id`, so a
client can quote it in a bug report.

### Why the spans are not "current" across the stream

`answer()` is an async generator, and it yields tokens while the generation
span is open. Making a span current means attaching it to a contextvar. An
attach that stays in place across a `yield` leaks into the consumer's code
between tokens, and the detach then happens in a different context.
OpenTelemetry logs "Failed to detach context" when that happens. So
`askdocs.answer` and `generation` are started with an explicit parent and
never attached. `retrieval` contains no `yield`, so it is made current, and
that is what lets the MCP SDK's span nest under it. A test checks that no span
leaks into the consumer.

### Cancelled answers still end their spans

When a client disconnects mid-stream, the pipeline is closed. Every span ends
in a `finally` block, and the answer span records `askdocs.outcome=cancelled`.
A trace exporter only sends finished spans, so an unended span would silently
disappear from the trace.

## Per-stage latency (sequential, 45 questions)

From the spans in Jaeger, and from `GET /stats` for the same run:

| Stage | P50 ms (spans) | P95 ms (spans) | P50 / P95 ms (`/stats`) | n |
|---|---|---|---|---|
| retrieval (whole MCP round trip) | 13.9 | 18.4 | 14.0 / 18.4 | 45 |
| of which the `crag_search` call | 13.7 | 18.1 | | 45 |
| time to first token | | | 449.3 / 566.5 | 43 |
| generation | 684.3 | 1137.3 | 684.4 / 1137.4 | 43 |
| answer, end to end | 683.4 | 1151.8 | 683.3 / 1151.7 | 45 |

The two sources agree to within 0.1 ms, so the in-process window measures
what the trace shows. Generation takes about 97% of an answer that calls the
model (643 of 663 ms in the trace above). Retrieval, including embedding the
question in Ollama, takes the rest. Two of the 45 questions were refused with
no passage, so they have no generation (n=43).

Two operational findings:

- **Cold start.** In the first run after starting the service, the first
  retrieval took 738 ms instead of 14 ms, because Ollama was loading the
  embedding model. The retrieval P95 in that run was 122 ms. The service now
  runs one retrieval in the background at startup (`ASKDOCS_WARMUP`), so the
  first user does not pay for it.
- **`MCP send tools/list`**: the MCP SDK lists the tools once per session,
  after the first call. It costs 0.2 ms, once.

Percentiles are nearest-rank: always a value that was actually observed.
With 45 values, P95 is the 43rd-smallest. These numbers come from one
client sending sequentially. Latency under concurrent load is the planned
load test's job.

## Tokens and cost per answer

From the eval baseline (`evals/results/report.md`):

| Per answer | P50 | P95 |
|---|---|---|
| prompt tokens | 2009 | 2492 |
| completion tokens | 33 | 89 |

The prompt is about 60 times the answer: five passages of context against a
short, cited answer. With a hosted model, cost would follow the input side,
so `top_k` and the passage cap are the cost levers, not `max_tokens`.

Cost is `prompt_tokens × input price + completion_tokens × output price`,
with prices in USD per million tokens set in `ASKDOCS_LLM_PRICE_INPUT_PER_MTOK`
and `ASKDOCS_LLM_PRICE_OUTPUT_PER_MTOK`. It appears in `done` as `cost_usd`,
on the answer span, in `/stats`, and in the eval report. The defaults are 0,
because a local model's cost is GPU time (the generation row above), not
money. No provider's prices are built in: they change, so set the ones you
actually pay.

## What tracing costs

The pipeline on in-memory fakes (no I/O), 20,000 answers, best of 5:

| | µs per answer |
|---|---|
| API only (tracing off) | 58.1 |
| SDK + batch processor, 3 spans | 152.1 |

That is about 94 µs per answer, or 0.014% of the P50 answer. In that tight
loop the SDK logged `Queue full, dropping Span`: when spans arrive faster than
the exporter drains its queue (2048), the batch processor drops spans instead
of blocking requests. That is the right trade-off for a service, but it means
a trace backend can miss spans during a burst. `/stats` does not drop
anything.

## Not measured here

- The embedding and search split inside crag-mcp: the server does not
  trace, so `crag_search` is one span. What the MCP hop itself costs is for
  the planned `docs/architecture.md`.
- Inside llama.cpp: prompt processing against decoding shows up only as time
  to first token against the rest of the generation.
