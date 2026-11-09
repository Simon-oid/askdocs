"""Test doubles for the retriever and the LLM."""

from __future__ import annotations

from collections.abc import AsyncIterator

import anyio

from askdocs.llm import Delta, Finish, LLMError, Usage
from askdocs.retrieval import Passage, RetrievalError

ALPHA = Passage("cMCP/docs/a.md", "requestState is sealed with HMAC-SHA256.", 0.71, 9.9, 0.033)
BETA = Passage("cRAG/README.md", "BM25 uses k1 = 1.2 and b = 0.75.", 0.60, 0.0, 0.016)


class FakeRetriever:
    def __init__(self, passages=(ALPHA, BETA), error: str | None = None):
        self.passages, self.error, self.queries = list(passages), error, []

    async def search(self, query: str, k: int) -> list[Passage]:
        self.queries.append((query, k))
        if self.error:
            raise RetrievalError(self.error)
        return self.passages[:k]


class FakeLLM:
    """Streams `pieces`, then usage. `delay` between pieces lets a test
    disconnect mid-answer; `cancelled` records whether that stopped it."""

    def __init__(self, pieces=("The seal is ", "HMAC-SHA256 [1]."), *, delay: float = 0.0,
                 fail_after: int | None = None, finish: str = "stop"):
        self.pieces, self.delay, self.fail_after = list(pieces), delay, fail_after
        self.finish = finish
        self.calls: list[list[dict]] = []
        self.cancelled = False
        self.finished = False

    async def stream(self, messages: list[dict], max_tokens: int) -> AsyncIterator:
        self.calls.append(messages)
        try:
            for i, p in enumerate(self.pieces):
                if self.fail_after is not None and i == self.fail_after:
                    raise LLMError("upstream died")
                if self.delay:
                    await anyio.sleep(self.delay)
                yield Delta(p)
            yield Finish(self.finish)
            yield Usage(prompt_tokens=100, completion_tokens=len(self.pieces))
            self.finished = True
        except (anyio.get_cancelled_exc_class(), GeneratorExit):
            self.cancelled = True
            raise
