"""LLM providers behind one interface.

llama.cpp's server and OpenAI-compatible APIs share the chat-completions
wire format, so one client covers both; `make_llm` picks defaults per
provider. The interface is a stream of events, not of strings, because
the end of the stream carries token usage (needed for cost and the
tokens-per-answer metrics) and the interface must not lose it.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import anyio
import httpx

from .config import Settings

# Statuses worth retrying: overloaded or briefly unavailable upstream.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
MAX_ERROR_BODY = 2048        # bytes of an upstream error body kept for the log
MAX_RETRY_AFTER_S = 10.0     # a longer Retry-After is the caller's problem, not a wait


class LLMError(RuntimeError):
    """The model could not produce a (complete) answer."""


@dataclass(frozen=True)
class Delta:
    text: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class Finish:
    reason: str              # "stop", "length" (truncated by max_tokens), ...


Event = Delta | Usage | Finish
DONE = object()              # the terminal `data: [DONE]`


class LLM(Protocol):
    def stream(self, messages: list[dict], max_tokens: int) -> AsyncIterator[Event]: ...


def _int(v: object) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise LLMError(f"malformed usage value: {v!r}")
    return v


def parse_sse_line(line: str) -> list[Event] | object:
    """One line of an OpenAI-style stream -> the events it carries (a chunk
    can carry text, a finish reason and usage at once), or DONE. Anything
    malformed is an LLMError: never an AttributeError that would reset the
    client's connection instead of producing an `error` event."""
    if not line.startswith("data:"):
        return []                            # comments (": keep-alive"), event: lines
    data = line[5:].strip()
    if data == "[DONE]":
        return DONE
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError as e:
        raise LLMError(f"malformed stream chunk: {data[:80]!r}") from e
    if not isinstance(chunk, dict):
        raise LLMError(f"stream chunk is not an object: {data[:80]!r}")
    if chunk.get("error"):
        # Some servers report a mid-stream failure as a 200 + error chunk.
        raise LLMError(f"upstream error in stream: {str(chunk['error'])[:200]}")
    events: list[Event] = []
    choices = chunk.get("choices") or []
    if choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        text = delta.get("content") if isinstance(delta, dict) else None
        if isinstance(text, str) and text:
            events.append(Delta(text))
        if isinstance(choice.get("finish_reason"), str):
            events.append(Finish(choice["finish_reason"]))
    u = chunk.get("usage")
    if isinstance(u, dict):
        events.append(Usage(_int(u.get("prompt_tokens", 0)), _int(u.get("completion_tokens", 0))))
    return events


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return min(float(r.headers["retry-after"]), MAX_RETRY_AFTER_S)
    except (KeyError, ValueError):
        return None


class _Retry(Exception):
    def __init__(self, err: Exception, wait: float | None = None):
        self.err, self.wait = err, wait


class ChatCompletionsLLM:
    def __init__(self, base_url: str, model: str, api_key: str | None, *,
                 temperature: float, connect_timeout_s: float, read_timeout_s: float,
                 max_retries: int, retry_backoff_s: float, total_timeout_s: float = 180.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, transport=transport,
            timeout=httpx.Timeout(read_timeout_s, connect=connect_timeout_s))
        self._model, self._temperature = model, temperature
        self._max_retries, self._backoff = max_retries, retry_backoff_s
        self._total_s = total_timeout_s

    async def aclose(self) -> None:
        await self._client.aclose()

    def _body(self, messages: list[dict], max_tokens: int) -> dict:
        return {"model": self._model, "messages": messages, "stream": True,
                "max_tokens": max_tokens, "temperature": self._temperature,
                "stream_options": {"include_usage": True}}

    async def _error(self, r: httpx.Response, attempt: int) -> Exception:
        body = b""
        async for part in r.aiter_bytes():
            body += part
            if len(body) >= MAX_ERROR_BODY:
                break
        err = LLMError(f"LLM HTTP {r.status_code}: {body[:200].decode(errors='replace')}")
        if r.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
            return _Retry(err, _retry_after(r))
        return err

    async def stream(self, messages: list[dict], max_tokens: int) -> AsyncIterator[Event]:
        """Events until the model finishes. Retries transport errors and
        RETRYABLE_STATUS, but only until the first event is yielded: past
        that point the caller has shown text. A stream that ends without
        [DONE] or a finish reason is an error, not a short answer."""
        body = self._body(messages, max_tokens)
        deadline = time.monotonic() + self._total_s
        for attempt in range(self._max_retries + 1):
            yielded = False
            try:
                async with self._client.stream("POST", "/chat/completions", json=body) as r:
                    if r.status_code != 200:
                        raise await self._error(r, attempt)
                    finished = False
                    async for line in r.aiter_lines():
                        if time.monotonic() > deadline:   # keep-alives reset the read timeout
                            raise LLMError(f"LLM exceeded {self._total_s:.0f} s for one answer")
                        evs = parse_sse_line(line)
                        if evs is DONE:
                            return
                        for ev in evs:
                            finished = finished or isinstance(ev, Finish)
                            yielded = True
                            yield ev
                    if not finished:
                        raise LLMError("LLM stream ended before the answer was complete")
                    return
            except _Retry as e:
                wait = e.wait if e.wait is not None else self._backoff * 2 ** attempt
            except httpx.ReadTimeout as e:
                # Before any event, a stall may be one hung connection: retry.
                # After one, retrying would duplicate text the client has.
                if yielded or attempt == self._max_retries:
                    raise LLMError("LLM stalled: no data within the read timeout") from e
                wait = self._backoff * 2 ** attempt
            except (httpx.TransportError, httpx.DecodingError) as e:
                if yielded or attempt == self._max_retries:
                    raise LLMError(f"LLM unreachable: {type(e).__name__}") from e
                wait = self._backoff * 2 ** attempt
            await anyio.sleep(wait)


def make_llm(s: Settings, transport: httpx.AsyncBaseTransport | None = None) -> ChatCompletionsLLM:
    return ChatCompletionsLLM(
        s.base_url(), s.llm_model, s.llm_key(), temperature=s.llm_temperature,
        connect_timeout_s=s.llm_connect_timeout_s, read_timeout_s=s.llm_read_timeout_s,
        max_retries=s.llm_max_retries, retry_backoff_s=s.llm_retry_backoff_s,
        total_timeout_s=s.llm_total_timeout_s, transport=transport)
