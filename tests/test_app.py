import json

import anyio
import httpx
import pytest

from askdocs.app import Slots, create_app
from askdocs.config import Settings
from askdocs.prompt import REFUSAL

from .fakes import FakeLLM, FakeRetriever


def parse_sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def make_client():
    async def make(retriever=None, llm=None, **settings):
        app = create_app(Settings(**settings), retriever=retriever or FakeRetriever(),
                         llm=llm or FakeLLM())
        return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                      base_url="http://test")
    return make


async def test_ask_streams_sse(make_client):
    app, client = await make_client()
    async with app.router.lifespan_context(app), client:
        r = await client.post("/ask", json={"question": "how is it sealed?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert [e for e, _ in events] == ["sources", "token", "token", "done"]
    assert events[-1][1]["citations"] == [1]


async def test_ask_json_envelope(make_client):
    app, client = await make_client()
    async with app.router.lifespan_context(app), client:
        r = await client.post("/ask?stream=false", json={"question": "q", "k": 1})
    body = r.json()
    assert r.status_code == 200 and body["success"] and body["error"] is None
    assert len(body["data"]["sources"]) == 1 and body["data"]["answer"].endswith("[1].")


async def test_no_sources_json_refusal(make_client):
    app, client = await make_client(retriever=FakeRetriever(passages=[]))
    async with app.router.lifespan_context(app), client:
        body = (await client.post("/ask?stream=false", json={"question": "q"})).json()
    assert body["data"]["answer"] == REFUSAL and body["data"]["refused"]


async def test_upstream_error_hides_details(make_client):
    app, client = await make_client(retriever=FakeRetriever(error="/secret/path: boom"))
    async with app.router.lifespan_context(app), client:
        r = await client.post("/ask?stream=false", json={"question": "q"})
        s = await client.post("/ask", json={"question": "q"})
    assert r.status_code == 502 and r.json()["error"]["message"] == "retrieval is unavailable"
    assert "/secret" not in r.text and "/secret" not in s.text
    assert parse_sse(s.text) == [("error", {"stage": "retrieval",
                                            "message": "retrieval is unavailable"})]


@pytest.mark.parametrize("payload", [{"question": ""}, {"question": "   "},
                                     {"question": "x" * 2001}, {"question": "q", "k": 0},
                                     {"question": "q", "k": 21}, {}])
async def test_rejects_bad_input(make_client, payload):
    app, client = await make_client()
    async with app.router.lifespan_context(app), client:
        r = await client.post("/ask", json=payload)
        health = (await client.get("/healthz")).json()
    assert r.status_code == 422 and "detail" in r.json()
    assert health["in_flight"] == 0       # a rejected request holds no slot


async def test_busy_returns_503_and_slot_is_released(make_client):
    app, client = await make_client(max_concurrent=1, llm=FakeLLM(delay=0.05))
    async with app.router.lifespan_context(app), client:
        async with anyio.create_task_group() as tg:
            results = []

            async def one():
                results.append((await client.post("/ask", json={"question": "q"})).status_code)
            tg.start_soon(one)
            await anyio.sleep(0.02)
            busy = await client.post("/ask", json={"question": "q"})
        after = await client.post("/ask", json={"question": "q"})
        health = (await client.get("/healthz")).json()
    assert busy.status_code == 503 and busy.headers["retry-after"] == "1"
    assert results == [200] and after.status_code == 200
    assert health["in_flight"] == 0


def scope_for(query: bytes = b"", headers=()) -> dict:
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/ask",
            "raw_path": b"/ask", "root_path": "", "query_string": query,
            "headers": [(b"content-type", b"application/json"), (b"host", b"test"),
                        *headers],
            "client": ("127.0.0.1", 1), "server": ("test", 80)}


def receiver(chunks: list[bytes], disconnect: anyio.Event):
    """ASGI receive: the body in `chunks`, then http.disconnect once set."""
    pending = list(chunks)

    async def receive():
        if pending:
            body = pending.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(pending)}
        # Like uvicorn: once disconnected, answer without awaiting, so a
        # poll from an already-cancelled scope (is_disconnected) still sees it.
        if not disconnect.is_set():
            await disconnect.wait()
        return {"type": "http.disconnect"}
    return receive


async def test_client_disconnect_stops_generation(make_client):
    llm = FakeLLM(pieces=["tok "] * 50, delay=0.01)
    app, _ = await make_client(llm=llm)
    sent: list[dict] = []
    disconnect = anyio.Event()

    async def send(msg):
        sent.append(msg)
        tokens = sum(1 for m in sent if b"event: token" in m.get("body", b""))
        if tokens == 3:
            disconnect.set()

    async with app.router.lifespan_context(app):
        with anyio.fail_after(5):
            await app(scope_for(), receiver([json.dumps({"question": "q"}).encode()],
                                            disconnect), send)
    assert llm.cancelled and not llm.finished
    tokens = sum(1 for m in sent if b"event: token" in m.get("body", b""))
    assert tokens < 10


def test_slots_release_is_idempotent():
    s = Slots(1)
    release = s.try_take()
    assert release and s.try_take() is None
    release()
    release()
    assert s.used == 0


async def test_json_mode_client_disconnect_stops_generation(make_client):
    llm = FakeLLM(pieces=["tok "] * 50, delay=0.02)
    app, _ = await make_client(llm=llm)
    disconnect = anyio.Event()
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    async def leave_soon():
        await anyio.sleep(0.15)
        disconnect.set()

    async with app.router.lifespan_context(app):
        async with anyio.create_task_group() as tg:
            tg.start_soon(leave_soon)
            with anyio.fail_after(5):
                await app(scope_for(b"stream=false"),
                          receiver([json.dumps({"question": "q"}).encode()], disconnect), send)
    assert llm.cancelled and not llm.finished
    assert sent[0]["status"] == 499


async def body_status(app, chunks: list[bytes], headers=()) -> int:
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    async with app.router.lifespan_context(app):
        with anyio.fail_after(5):
            await app(scope_for(headers=headers), receiver(chunks, anyio.Event()), send)
    return sent[0]["status"]


async def test_oversized_body_is_413_by_content_length(make_client):
    app, _ = await make_client()
    assert await body_status(app, [b"{}"], headers=[(b"content-length", b"999999")]) == 413


async def test_oversized_chunked_body_is_413_without_reading_it_all(make_client):
    app, _ = await make_client()
    piece = b"x" * 8192
    assert await body_status(app, [b'{"question": "', piece, piece, piece, b'"}']) == 413


async def test_body_under_the_limit_in_chunks_still_works(make_client):
    app, _ = await make_client()
    body = json.dumps({"question": "how is it sealed?"}).encode()
    assert await body_status(app, [body[:5], body[5:]]) == 200
