import sys
from pathlib import Path

import anyio
import pytest

from askdocs.config import Settings
from askdocs.retrieval import (
    McpRetriever,
    RetrievalError,
    parse_items,
    parse_structured,
    short_source,
)

FAKE = str(Path(__file__).with_name("fake_crag_mcp.py"))


def test_parse_items_reads_scores_and_relative_source():
    [p] = parse_items(["[cos 0.683  bm25 9.88  fusion 0.033] /h/x/corpus/cMCP/a.md\nbody\ntwo"],
                      "corpus/")
    assert (p.source, p.text, p.cosine, p.bm25, p.fusion) == (
        "cMCP/a.md", "body\ntwo", 0.683, 9.88, 0.033)


def test_parse_items_no_match_sentinel_is_empty():
    assert parse_items(["(no chunk cleared the relevance threshold)"], "corpus/") == []
    real = ("(no chunk cleared the relevance threshold — the index is non-empty "
            "but nothing matched this query)")          # crag-mcp 0.11 wording
    assert parse_items([real], "corpus/") == []


def test_parse_items_rejects_unknown_format():
    with pytest.raises(RetrievalError):
        parse_items(["something else"], "corpus/")


def test_parse_structured_reads_fields_and_relative_source():
    sc = {"status": "ok", "results": [{"source": "/h/x/corpus/cRAG/b.md", "cosine": 0.71,
                                       "bm25": 0.0, "fusion": 0.016, "text": " body "}]}
    [p] = parse_structured(sc, "corpus/")
    assert (p.source, p.text, p.cosine, p.bm25, p.fusion) == ("cRAG/b.md", "body", 0.71, 0.0, 0.016)


@pytest.mark.parametrize("status", ["below_threshold", "no_match"])
def test_parse_structured_no_hit_statuses_are_empty(status):
    assert parse_structured({"status": status, "results": []}, "corpus/") == []


@pytest.mark.parametrize("sc,match", [
    ({"status": "empty_index", "results": []}, "index is empty"),
    ({"status": "weird", "results": []}, "unexpected crag_search status"),
    ({"status": "ok", "results": [{"source": "a.md"}]}, "unexpected crag_search result"),
    ({"status": "ok", "results": [{"source": "a", "cosine": "x", "bm25": 0, "fusion": 0,
                                   "text": ""}]}, "unexpected crag_search result"),
    (["not", "a", "dict"], "structuredContent"),
])
def test_parse_structured_rejects_broken_payloads(sc, match):
    with pytest.raises(RetrievalError, match=match):
        parse_structured(sc, "corpus/")


def test_short_source_without_marker_keeps_only_basename():
    assert short_source("/secret/home/notes.md", "corpus/") == "notes.md"


def settings(monkeypatch, tmp_path, **env) -> Settings:
    monkeypatch.setenv("FAKE_STATE_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # The fake reads FAKE_* from its environment; McpRetriever passes only a
    # fixed set of variables, so run it through `env` with them inlined.
    return Settings(mcp_command=sys.executable, mcp_db="unused", mcp_timeout_s=5,
                    mcp_reconnect_backoff_s=0.01)


async def run_with(retriever: McpRetriever, fn):
    """Run fn with the supervisor up; fn's exception is re-raised bare,
    not wrapped in the task group's ExceptionGroup."""
    outcome: dict = {}
    async with anyio.create_task_group() as tg:
        await tg.start(retriever.run)
        try:
            outcome["value"] = await fn()
        except Exception as e:
            outcome["error"] = e
        tg.cancel_scope.cancel()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


class FakeServerRetriever(McpRetriever):
    """Launches the fake with its FAKE_* variables (the real class passes
    only PATH and the embedder variables, by design)."""

    def __init__(self, s: Settings, env: dict):
        super().__init__(s)
        self._env = env

    def _params(self):
        p = super()._params()
        return p.model_copy(update={"args": [FAKE], "env": {**p.env, **self._env}})


@pytest.mark.parametrize("mode,expected", [("ok", 2), ("nomatch", 0)])
async def test_search_over_stdio(monkeypatch, tmp_path, mode, expected):
    r = FakeServerRetriever(settings(monkeypatch, tmp_path), {"FAKE_MODE": mode})
    hits = await run_with(r, lambda: r.search("seal", 5))
    assert len(hits) == expected
    if expected:
        assert hits[0].source == "cMCP/docs/a.md"     # absolute path cut
        assert "seal" in hits[0].text


@pytest.mark.parametrize("mode", ["toolerror", "garbage"])
async def test_tool_errors_are_retrieval_errors(monkeypatch, tmp_path, mode):
    r = FakeServerRetriever(settings(monkeypatch, tmp_path), {"FAKE_MODE": mode})
    with pytest.raises(RetrievalError):
        await run_with(r, lambda: r.search("q", 5))


async def test_reconnects_after_server_crash(monkeypatch, tmp_path):
    env = {"FAKE_DIE_AFTER": "2", "FAKE_STATE_DIR": str(tmp_path)}
    r = FakeServerRetriever(settings(monkeypatch, tmp_path), env)

    async def three_searches():
        return [len(await r.search(f"q{i}", 5)) for i in range(3)]

    assert await run_with(r, three_searches) == [2, 2, 2]   # the 2nd call hit the crash
    assert r.reconnects == 1


async def test_startup_fails_loudly_on_bad_command(tmp_path):
    r = McpRetriever(Settings(mcp_command=str(tmp_path / "missing"), mcp_timeout_s=2))
    with pytest.raises(ExceptionGroup) as ei:   # spawn fails; startup must not hang or pass
        await run_with(r, lambda: r.search("q", 1))
    assert ei.group_contains(FileNotFoundError)


async def test_slow_call_times_out_without_killing_the_session(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path).model_copy(update={"mcp_timeout_s": 0.3})
    r = FakeServerRetriever(s, {})

    async def slow_then_fast():
        with pytest.raises(RetrievalError, match="timed out"):
            await r.search("slow", 5)
        await anyio.sleep(1.0)            # let the fake finish the slow answer
        return len(await r.search("fast", 5))

    assert await run_with(r, slow_then_fast) == 2
    assert r.reconnects == 0


@pytest.mark.parametrize("mode,expected", [("ok", 1), ("nomatch", 0)])
async def test_search_prefers_structured_content(monkeypatch, tmp_path, mode, expected):
    env = {"FAKE_MODE": mode, "FAKE_STRUCTURED": "1"}
    r = FakeServerRetriever(settings(monkeypatch, tmp_path), env)
    hits = await run_with(r, lambda: r.search("seal", 5))
    assert len(hits) == expected                      # the text side has 2 hits
    if expected:
        assert (hits[0].source, hits[0].cosine) == ("cMCP/docs/a.md", 0.9)


async def test_structured_empty_index_is_a_retrieval_error(monkeypatch, tmp_path):
    env = {"FAKE_MODE": "empty", "FAKE_STRUCTURED": "1"}
    r = FakeServerRetriever(settings(monkeypatch, tmp_path), env)
    with pytest.raises(RetrievalError, match="index is empty"):
        await run_with(r, lambda: r.search("q", 5))
