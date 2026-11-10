"""question -> retrieval -> grounded generation, as a stream of events.

Events (name, payload), in order:
  sources  the passages the answer may cite, numbered from 1
  token    a piece of answer text (zero or more)
  done     the full answer, citations, refusal flag, usage, cost, timings,
           and the trace id (null when tracing is off)
  error    instead of `done` when retrieval or the model fails (stage
           retrieval | generation), or on an unexpected failure (internal)
The HTTP layer maps them 1:1 onto SSE events; evals consume them directly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import asdict

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, StatusCode

from .llm import LLM, Delta, Finish, LLMError, Usage
from .prompt import REFUSAL, build_messages, citations, is_refusal
from .retrieval import RetrievalError, Retriever
from .telemetry import FREE, TRACER, Prices, cost_usd

PipelineEvent = tuple[str, dict]
log = logging.getLogger("askdocs.pipeline")


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _trace_id(span: Span) -> str | None:
    ctx = span.get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


async def answer(question: str, retriever: Retriever, llm: LLM, *, k: int,
                 max_tokens: int, prices: Prices = FREE,
                 parent: Context | None = None) -> AsyncGenerator[PipelineEvent, None]:
    """One trace per answer: `askdocs.answer` with children `retrieval` and
    `generation`. Spans are started with an explicit parent, never made
    "current": this generator yields with the generation span open, and a
    context attached across a yield would leak into the consumer's code
    and be detached in the wrong context. The question text is not
    recorded (it may be personal data), only its length."""
    root = TRACER.start_span("askdocs.answer", context=parent, attributes={
        "askdocs.k": k, "askdocs.question_chars": len(question)})
    ctx = trace.set_span_in_context(root)
    outcome = "cancelled"            # unless the stream reaches done or error
    try:
        # aclosing: closing this generator must close the model stream now.
        async with aclosing(_answer(question, retriever, llm, k, max_tokens, prices, ctx)) as evs:
            async for name, data in evs:
                if name in ("done", "error"):
                    outcome = _close_root(root, name, data)
                    data = {**data, "trace_id": _trace_id(root)}
                yield name, data
    except Exception as e:
        # A bug, not a stage failure: the stream must still end with an
        # event the client can act on. The traceback goes to the log and
        # the span; the client gets the exception type only, since the
        # message may hold internals. Cancellation is not an Exception
        # and passes through.
        log.exception("unexpected failure while answering")
        outcome = "error"
        root.record_exception(e)
        root.set_status(StatusCode.ERROR, f"internal: {type(e).__name__}")
        yield "error", {"stage": "internal", "message": type(e).__name__,
                        "trace_id": _trace_id(root)}
    finally:
        root.set_attribute("askdocs.outcome", outcome)
        root.end()


def _close_root(root: Span, event: str, data: dict) -> str:
    """Annotate the answer span from the final event; returns the outcome."""
    if event == "error":
        root.set_status(StatusCode.ERROR, f"{data['stage']}: {data['message']}")
        return "error"
    root.set_attributes({"askdocs.cost_usd": data["cost_usd"] or 0.0,
                         "askdocs.citations": len(data["citations"]),
                         "askdocs.invalid_citations": len(data["invalid_citations"])})
    return "refused" if data["refused"] else "answered"


async def _answer(question: str, retriever: Retriever, llm: LLM, k: int, max_tokens: int,
                  prices: Prices, ctx: Context) -> AsyncGenerator[PipelineEvent, None]:
    t0 = time.perf_counter()
    try:
        # No yield inside: safe to make this span current, which also
        # records the exception and sets the error status.
        with TRACER.start_as_current_span("retrieval", context=ctx,
                                          attributes={"askdocs.k": k}) as span:
            passages = await retriever.search(question, k)
            span.set_attribute("askdocs.passages", len(passages))
    except RetrievalError as e:
        yield "error", {"stage": "retrieval", "message": str(e)}
        return
    retrieval_ms = _ms(t0)
    yield "sources", {"sources": [{"n": i, **asdict(p)} for i, p in enumerate(passages, 1)]}

    timings = {"retrieval_ms": retrieval_ms}
    if not passages:
        # Nothing cleared the relevance gate: refuse without paying for a
        # generation that could only be ungrounded.
        yield "token", {"text": REFUSAL}
        yield "done", {"answer": REFUSAL, "refused": True, "refusal_reason": "no_sources",
                       "citations": [], "invalid_citations": [], "truncated": False,
                       "usage": None, "cost_usd": None,
                       "timings": {**timings, "total_ms": _ms(t0)}}
        return

    parts: list[str] = []
    usage: Usage | None = None
    finish = None
    t_gen = time.perf_counter()
    gen = TRACER.start_span("generation", context=ctx, attributes={
        "gen_ai.operation.name": "chat", "gen_ai.request.max_tokens": max_tokens,
        **({"gen_ai.request.model": m} if (m := getattr(llm, "model", None)) else {})})
    try:
        # aclosing: closing this generator closes the model stream now, not
        # whenever the garbage collector finalises it.
        async with aclosing(llm.stream(build_messages(question, passages), max_tokens)) as evs:
            async for ev in evs:
                if isinstance(ev, Delta):
                    if not parts:
                        timings["ttft_ms"] = _ms(t_gen)
                        gen.add_event("first_token")
                    parts.append(ev.text)
                    yield "token", {"text": ev.text}
                elif isinstance(ev, Usage):
                    usage = ev
                elif isinstance(ev, Finish):
                    finish = ev.reason
    except LLMError as e:
        gen.record_exception(e)
        gen.set_status(StatusCode.ERROR, str(e))
        yield "error", {"stage": "generation", "message": str(e), "partial": "".join(parts)}
        return
    finally:
        _end_generation(gen, usage, finish)

    text = "".join(parts).strip()
    if not text:
        # Not a refusal: the model said nothing, which is a failure to report.
        yield "error", {"stage": "generation", "message": "empty answer", "partial": ""}
        return
    refused = is_refusal(text)
    valid, invalid = citations(text, len(passages))
    used = asdict(usage) if usage else None
    yield "done", {
        "answer": text, "refused": refused, "refusal_reason": "model" if refused else None,
        "citations": valid, "invalid_citations": invalid,
        "truncated": finish == "length",
        "usage": used, "cost_usd": cost_usd(used, prices),
        "timings": {**timings, "generation_ms": _ms(t_gen), "total_ms": _ms(t0)},
    }


def _end_generation(span: Span, usage: Usage | None, finish: str | None) -> None:
    if usage:
        span.set_attributes({"gen_ai.usage.input_tokens": usage.prompt_tokens,
                             "gen_ai.usage.output_tokens": usage.completion_tokens})
    if finish:
        span.set_attribute("gen_ai.response.finish_reasons", (finish,))
    span.end()
