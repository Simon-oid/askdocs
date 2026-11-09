"""Retrieval through crag-mcp, cMCP's MCP server over a cRAG index.

One MCP session over stdio lives as long as the service: spawning the
server per request would add process start-up and the initialize
handshake to every question. If the server dies, the next search
reconnects once before giving up.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Protocol

import anyio
from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CONNECTION_CLOSED

from .config import Settings

log = logging.getLogger("askdocs.retrieval")
MAX_RECONNECT_BACKOFF_S = 10.0
# Exceptions that mean the stdio pipe or the process is gone.
TRANSPORT_ERRORS = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream,
                    OSError)

# crag_search item header: "[cos 0.683  bm25 9.88  fusion 0.033] path\n<body>"
_HEADER = re.compile(
    r"^\[cos (?P<cos>-?[\d.]+)\s+bm25 (?P<bm25>-?[\d.]+)\s+fusion (?P<fusion>-?[\d.]+)\] "
    r"(?P<source>[^\n]+)\n?(?P<text>.*)\Z",
    re.S,
)
# The no-match sentinel. Matched by prefix: crag-mcp appends an
# explanation after it ("— the index is non-empty but nothing matched").
NO_MATCH = "(no chunk cleared the relevance threshold"


class RetrievalError(RuntimeError):
    """The retriever could not answer (server down, timeout, tool error)."""


@dataclass(frozen=True)
class Passage:
    source: str
    text: str
    cosine: float
    bm25: float
    fusion: float


class Retriever(Protocol):
    async def search(self, query: str, k: int) -> list[Passage]: ...


def short_source(path: str, marker: str) -> str:
    """Path relative to the corpus root, so absolute paths never leave the host."""
    i = path.find(marker)
    return path[i + len(marker):] if i >= 0 else os.path.basename(path)


def parse_items(texts: list[str], marker: str) -> list[Passage]:
    """crag_search text items -> passages. Unparseable items are an error:
    silently dropping them would hide a server/client format drift."""
    if len(texts) == 1 and texts[0].startswith(NO_MATCH):
        return []
    out = []
    for t in texts:
        m = _HEADER.match(t)
        if not m:
            raise RetrievalError(f"unexpected crag_search item: {t[:80]!r}")
        out.append(Passage(short_source(m["source"].strip(), marker), m["text"].strip(),
                           float(m["cos"]), float(m["bm25"]), float(m["fusion"])))
    return out


class McpRetriever:
    """crag_search over one long-lived MCP session.

    anyio requires a task group (stdio_client and ClientSession each own
    one) to be exited by the task that entered it, so a request task must
    never open or close the session. A supervisor task (`run`, started in
    the app lifespan) owns it: opens it, and when a request reports it
    broken, closes it and opens a new one. Requests only borrow it.
    """

    def __init__(self, settings: Settings):
        self._s = settings
        self._session: ClientSession | None = None
        self._ready = anyio.Event()        # set while _session is usable
        self._broken = anyio.Event()       # set by a request: reconnect
        self.reconnects = 0

    def _params(self) -> StdioServerParameters:
        s = self._s
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "CRAG_EMBED_BACKEND": s.embed_backend, "CRAG_EMBED_MODEL": s.embed_model,
               "CRAG_EMBED_URL": s.embed_url}
        return StdioServerParameters(command=s.mcp_command, args=["--db", s.mcp_db], env=env)

    async def run(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        """Supervisor. The first connection must succeed (startup fails
        loudly on a wrong command or DB); later ones are retried forever."""
        started = False
        backoff = self._s.mcp_reconnect_backoff_s
        while True:
            try:
                async with stdio_client(self._params()) as (read, write), \
                        ClientSession(read, write) as session:
                    with anyio.fail_after(self._s.mcp_timeout_s):
                        await session.initialize()
                    self._session, self._broken = session, anyio.Event()
                    self._ready.set()
                    backoff = self._s.mcp_reconnect_backoff_s
                    if not started:
                        started = True
                        task_status.started()
                    await self._broken.wait()
            except Exception:
                if not started:
                    raise
                log.warning("crag-mcp session lost; reconnecting in %.1f s", backoff,
                            exc_info=True)
            finally:
                self._unpublish()
            self.reconnects += 1
            await anyio.sleep(backoff)
            backoff = min(backoff * 2, MAX_RECONNECT_BACKOFF_S)

    def _unpublish(self) -> None:
        """No session to borrow until the supervisor opens the next one.
        The event is replaced only if set: an unset one may have waiters,
        and it is the one the supervisor sets on reconnect."""
        self._session = None
        if self._ready.is_set():
            self._ready = anyio.Event()

    def _mark_broken(self, session: ClientSession) -> None:
        if self._session is session:      # not already replaced
            self._unpublish()
            self._broken.set()

    async def _borrow(self) -> ClientSession:
        with anyio.move_on_after(self._s.mcp_timeout_s):
            while self._session is None:
                await self._ready.wait()
            return self._session
        raise RetrievalError("crag-mcp unavailable: no session within timeout")

    async def _call(self, query: str, k: int) -> list[Passage]:
        """RetrievalError for answers the server gave (it is alive); the
        raw exception, with the session marked broken, only when the
        transport is gone. One slow call must not kill the shared session."""
        session = await self._borrow()
        try:
            with anyio.fail_after(self._s.mcp_timeout_s):
                res = await session.call_tool("crag_search", {"query": query, "k": k})
        except MCPError as e:
            if e.error.code != CONNECTION_CLOSED:
                raise RetrievalError(f"crag_search rejected: {e.error.message}") from e
            self._mark_broken(session)
            raise
        except TimeoutError as e:
            raise RetrievalError(f"crag_search timed out after {self._s.mcp_timeout_s} s") from e
        except TRANSPORT_ERRORS:
            self._mark_broken(session)     # the supervisor opens a new one
            raise
        except Exception as e:
            raise RetrievalError(f"crag_search failed: {type(e).__name__}") from e
        texts = [c.text for c in getattr(res, "content", []) if getattr(c, "type", "") == "text"]
        if getattr(res, "is_error", False):
            raise RetrievalError("crag_search failed: " + " ".join(texts)[:200])
        return parse_items(texts, self._s.source_root_marker)

    async def search(self, query: str, k: int) -> list[Passage]:
        """One retry, on a fresh session, for transport failures only."""
        try:
            return await self._call(query, k)
        except RetrievalError:
            raise
        except Exception:
            try:
                return await self._call(query, k)
            except RetrievalError:
                raise
            except Exception as e:
                raise RetrievalError(f"crag-mcp unavailable: {type(e).__name__}") from e
