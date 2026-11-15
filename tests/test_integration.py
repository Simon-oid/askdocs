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


async def test_running_retriever_sees_an_index_updated_by_another_process(tmp_path):
    """build_corpus.sh updates the index in place for a running service:
    the same crag-mcp process must find a document indexed after it started.
    Works on a copy of the index; needs the `crag` CLI ($CRAG or on PATH)."""
    import shutil

    import anyio

    from askdocs.retrieval import McpRetriever

    base = Settings()
    db = tmp_path / "docs.db"
    shutil.copy(base.mcp_db, db)
    term = "zanzibarquokka"
    retriever = McpRetriever(base.model_copy(update={"mcp_db": str(db)}))
    async with anyio.create_task_group() as tg:
        await tg.start(retriever.run)
        assert not any(term in p.text for p in await retriever.search(f"what is {term}", 5))
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / f"{term}.md").write_text(f"# {term}\n\nThe {term} is a test codename.\n")
        await anyio.run_process(
            [os.environ.get("CRAG", "crag"), "index", str(corpus), "--db", str(db),
             "--ext", "md", "--backend", base.embed_backend, "--url", base.embed_url],
            env={**os.environ, "CRAG_EMBED_MODEL": base.embed_model})
        assert any(term in p.text for p in await retriever.search(f"what is {term}", 5))
        tg.cancel_scope.cancel()
