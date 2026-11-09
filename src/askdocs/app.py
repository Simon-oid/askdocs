"""HTTP layer: POST /ask (SSE stream, or JSON with ?stream=false), GET /healthz."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing, asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .llm import LLM, make_llm
from .pipeline import PipelineEvent, answer
from .retrieval import McpRetriever, Retriever

log = logging.getLogger("askdocs")

# What the client sees when a stage fails; details stay in the server log.
CLIENT_ERROR = {"retrieval": "retrieval is unavailable", "generation": "the model failed to answer"}


MAX_QUESTION_CHARS = 2000
# A valid body is a 2000-character question plus `k`: 4 bytes per char in
# the worst UTF-8 case, plus JSON overhead. Anything bigger is refused before
# it is read into memory, whatever its Content-Length claims.
MAX_BODY_BYTES = 16 * 1024
DISCONNECT_POLL_S = 0.2


class BodyLimit:
    """Pure ASGI middleware: 413 for request bodies over `limit` bytes.

    It reads the body itself (at most `limit` bytes are ever buffered) and
    replays it to the app: raising from inside the app's own body read
    would not work, since FastAPI turns errors there into a 400. Checked on
    the bytes received, not only on Content-Length, which a chunked upload
    does not send and a client can lie about."""

    def __init__(self, app, limit: int = MAX_BODY_BYTES):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.limit):
            return await _too_large(send)
        chunks, size = [], 0
        while True:
            msg = await receive()
            if msg["type"] != "http.request":         # disconnected while uploading
                return
            size += len(msg.get("body", b""))
            if size > self.limit:
                return await _too_large(send)
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body", False):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()                    # later calls: disconnect detection

        await self.app(scope, replay, send)


async def _too_large(send) -> None:
    body = b'{"detail":"request body too large"}'
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    k: int | None = Field(None, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question is blank")
        return v


class Slots:
    """At most `n` answers in flight; past that, refuse instead of queueing."""

    def __init__(self, n: int):
        self.n, self.used = n, 0

    def try_take(self) -> Callable[[], None] | None:
        if self.used >= self.n:
            return None
        self.used += 1
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self.used -= 1
        return release


class SlotStreamingResponse(StreamingResponse):
    """Releases the slot however the response ends. StreamingResponse's own
    `background` is skipped when the client disconnects mid-stream."""

    def __init__(self, content, release: Callable[[], None], **kw):
        super().__init__(content, **kw)
        self._release = release

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def public(event: str, data: dict) -> dict:
    if event == "error":
        log.warning("ask failed at %s: %s", data["stage"], data["message"])
        return {**data, "message": CLIENT_ERROR[data["stage"]]}
    return data


def create_app(settings: Settings | None = None, *, retriever: Retriever | None = None,
               llm: LLM | None = None) -> FastAPI:
    """Production wiring by default; tests inject a retriever and an LLM."""
    s = settings or Settings()
    s.check()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        own_llm = llm is None
        app.state.llm = llm or make_llm(s)
        app.state.retriever = retriever or McpRetriever(s)
        try:
            async with anyio.create_task_group() as tg:
                if hasattr(app.state.retriever, "run"):
                    await tg.start(app.state.retriever.run)
                yield
                tg.cancel_scope.cancel()
        finally:
            if own_llm:
                await app.state.llm.aclose()

    app = FastAPI(title="askdocs", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodyLimit)
    slots = Slots(s.max_concurrent)

    def events(question: str, k: int) -> AsyncIterator[PipelineEvent]:
        return answer(question, app.state.retriever, app.state.llm, k=k,
                      max_tokens=s.llm_max_tokens)

    async def stream(question: str, k: int) -> AsyncIterator[str]:
        # aclosing: on client disconnect the pipeline is closed right away,
        # which closes the upstream HTTP stream and stops the generation.
        async with aclosing(events(question, k)) as evs:
            async for name, data in evs:
                yield sse(name, public(name, data))

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "in_flight": slots.used, "max_concurrent": slots.n}

    @app.post("/ask")
    async def ask(req: AskRequest, request: Request,
                  stream_: bool = Query(True, alias="stream")):
        release = slots.try_take()
        if release is None:
            raise HTTPException(503, "too many answers in flight", headers={"Retry-After": "1"})
        k = req.k or s.top_k
        try:
            if stream_:
                return SlotStreamingResponse(
                    stream(req.question, k), release, media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            return await collect_unless_gone(events(req.question, k), request)
        except BaseException:
            release()
            raise
        finally:
            if not stream_:
                release()

    return app


async def collect_unless_gone(evs: AsyncIterator[PipelineEvent], request: Request) -> JSONResponse:
    """collect(), cancelled if the client leaves. Starlette cancels a
    streaming response on disconnect but not a plain handler, so without
    this a JSON request would keep generating (and hold its slot) for
    nobody."""
    result: list[JSONResponse] = []
    async with anyio.create_task_group() as tg:
        async def run() -> None:
            result.append(await collect(evs))
            tg.cancel_scope.cancel()

        tg.start_soon(run)
        while True:
            if await request.is_disconnected():
                log.info("client left before the JSON answer was ready; cancelling")
                tg.cancel_scope.cancel()
                break
            await anyio.sleep(DISCONNECT_POLL_S)
    # 499 (client closed request): nobody reads it, but logs and tests do.
    return result[0] if result else JSONResponse({"success": False, "data": None, "error": {
        "stage": "client", "message": "client disconnected"}}, status_code=499)


async def collect(evs: AsyncIterator[PipelineEvent]) -> JSONResponse:
    """The whole answer as one JSON envelope (evals, scripts, curl)."""
    sources: list = []
    async with aclosing(evs) as it:
        async for name, data in it:
            if name == "sources":
                sources = data["sources"]
            elif name == "done":
                return JSONResponse({"success": True, "data": {**data, "sources": sources},
                                     "error": None})
            elif name == "error":
                body = public(name, data)
                return JSONResponse({"success": False, "data": None, "error": body},
                                    status_code=502)
    return JSONResponse({"success": False, "data": None,
                         "error": {"stage": "pipeline", "message": "no result"}}, status_code=500)
