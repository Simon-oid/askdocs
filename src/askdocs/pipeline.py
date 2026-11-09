"""question -> retrieval -> grounded generation, as a stream of events.

Events (name, payload), in order:
  sources  the passages the answer may cite, numbered from 1
  token    a piece of answer text (zero or more)
  done     the full answer, citations, refusal flag, usage, timings
  error    instead of `done` when retrieval or the model fails
The HTTP layer maps them 1:1 onto SSE events; evals consume them directly.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import asdict

from .llm import LLM, Delta, Finish, LLMError, Usage
from .prompt import REFUSAL, build_messages, citations, is_refusal
from .retrieval import RetrievalError, Retriever

PipelineEvent = tuple[str, dict]


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


async def answer(question: str, retriever: Retriever, llm: LLM, *, k: int,
                 max_tokens: int) -> AsyncIterator[PipelineEvent]:
    t0 = time.perf_counter()
    try:
        passages = await retriever.search(question, k)
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
                       "usage": None,
                       "timings": {**timings, "total_ms": _ms(t0)}}
        return

    parts: list[str] = []
    usage: Usage | None = None
    finish = None
    t_gen = time.perf_counter()
    try:
        # aclosing: closing this generator closes the model stream now, not
        # whenever the garbage collector finalises it.
        async with aclosing(llm.stream(build_messages(question, passages), max_tokens)) as evs:
            async for ev in evs:
                if isinstance(ev, Delta):
                    if not parts:
                        timings["ttft_ms"] = _ms(t_gen)
                    parts.append(ev.text)
                    yield "token", {"text": ev.text}
                elif isinstance(ev, Usage):
                    usage = ev
                elif isinstance(ev, Finish):
                    finish = ev.reason
    except LLMError as e:
        yield "error", {"stage": "generation", "message": str(e), "partial": "".join(parts)}
        return

    text = "".join(parts).strip()
    if not text:
        # Not a refusal: the model said nothing, which is a failure to report.
        yield "error", {"stage": "generation", "message": "empty answer", "partial": ""}
        return
    refused = is_refusal(text)
    valid, invalid = citations(text, len(passages))
    yield "done", {
        "answer": text, "refused": refused, "refusal_reason": "model" if refused else None,
        "citations": valid, "invalid_citations": invalid,
        "truncated": finish == "length",
        "usage": asdict(usage) if usage else None,
        "timings": {**timings, "generation_ms": _ms(t_gen), "total_ms": _ms(t0)},
    }
