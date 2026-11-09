"""End-to-end against the real stack: crag-mcp + Ollama embedder +
llama.cpp, over the demo corpus (scripts/build_corpus.sh).

    ASKDOCS_INTEGRATION=1 ASKDOCS_MCP_COMMAND=/path/to/crag-mcp pytest -m integration

Asserts behaviour, not wording: a small local model phrases answers
differently run to run, but grounding, citations and refusals must hold.
"""

import os
from contextlib import asynccontextmanager

import httpx
import pytest

from askdocs.app import create_app
from askdocs.config import Settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("ASKDOCS_INTEGRATION") != "1",
                       reason="set ASKDOCS_INTEGRATION=1 with the real stack running"),
]


@asynccontextmanager
async def client():
    # Not a fixture: pytest-asyncio may run fixture setup and teardown in
    # different tasks, and the lifespan's task group must exit where it entered.
    app = create_app(Settings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test", timeout=120) as c:
            yield c


async def ask(q: str) -> dict:
    async with client() as c:
        r = await c.post("/ask?stream=false", json={"question": q})
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def test_answerable_question_is_grounded_and_cited():
    d = await ask("How is the requestState sealed in cMCP?")
    assert not d["refused"]
    assert "HMAC" in d["answer"]
    assert d["citations"] and not d["invalid_citations"]


async def test_off_topic_question_refuses_without_generation():
    d = await ask("How do I bake sourdough bread?")
    assert d["refused"] and d["refusal_reason"] == "no_sources" and d["usage"] is None
