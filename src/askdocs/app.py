"""HTTP layer: POST /ask (SSE stream, or JSON with ?stream=false), GET /healthz."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing, asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException, Query
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
    async def ask(req: AskRequest, stream_: bool = Query(True, alias="stream")):
        release = slots.try_take()
        if release is None:
            raise HTTPException(503, "too many answers in flight", headers={"Retry-After": "1"})
        k = req.k or s.top_k
        try:
            if stream_:
                return SlotStreamingResponse(
                    stream(req.question, k), release, media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            # JSON mode runs to completion even if the client leaves (Starlette
            # does not cancel plain handlers); evals and scripts use it, browsers
            # use the stream.
            return await collect(events(req.question, k))
        except BaseException:
            release()
            raise
        finally:
            if not stream_:
                release()

    return app


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
