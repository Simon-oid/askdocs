#!/usr/bin/env python3
"""What does going through MCP cost? Retrieval for the eval questions,
two ways, on the same index:

  mcp      McpRetriever.search: JSON-RPC over stdio to crag-mcp, which
           embeds the query (its own HTTP call to Ollama), runs cRAG's
           hybrid search, applies the relevance gate and formats text
           that askdocs parses back. What the service does.
  direct   the same work in-process: embed via Ollama (one kept-alive
           connection), cRAG's hybrid search through crag-index (the same
           C engine via cffi), the same cosine gate.

and the pieces, timed apart:

  embed        the Ollama call alone, kept-alive connection
  search       crag-index hybrid search alone
  protocol     a crag_stats call over the same MCP session: the JSON-RPC
               round trip with next to no work behind it

    pip install /path/to/cRAG        # crag-index
    python bench/mcp_overhead.py [--rounds 3] [--db data/docs.db]

Needs Ollama with the embedder and the crag-mcp settings from .env.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import json
import sys
import time
from pathlib import Path

import anyio
import crag
import httpx

from askdocs.config import Settings
from askdocs.retrieval import McpRetriever
from askdocs.telemetry import percentile

ROOT = Path(__file__).resolve().parent.parent
MIN_COSINE = 0.50        # crag-mcp's calibrated gate for mxbai (tools/crag-mcp/crag_cutoff.c)
K = 5


def ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


async def embed(client: httpx.AsyncClient, s: Settings, text: str) -> array.array:
    r = await client.post(s.embed_url, json={"model": s.embed_model, "prompt": text})
    r.raise_for_status()
    return array.array("f", r.json()["embedding"])


def short(source: str, marker: str) -> str:
    i = source.find(marker)
    return source[i + len(marker):] if i >= 0 else source


async def measure(questions: list[str], s: Settings, db: str, rounds: int) -> dict:
    t: dict[str, list] = {k: [] for k in ("mcp", "direct", "embed", "search", "protocol",
                                          "mcp_first")}
    same = 0
    retriever = McpRetriever(s)
    async with anyio.create_task_group() as tg, httpx.AsyncClient(timeout=30) as http:
        await tg.start(retriever.run)
        with crag.Index(db) as idx:
            for q in questions[:3]:                                  # warm both paths
                await retriever.search(q, K)
                idx.search(vector=await embed(http, s, q), text=q, k=K)
            async def via_mcp(q: str) -> list[str]:
                t0 = time.perf_counter()
                got = await retriever.search(q, K)
                t["mcp"].append(ms(t0))
                return [p.source for p in got]

            async def direct(q: str) -> list[str]:
                t0 = time.perf_counter()
                vec = await embed(http, s, q)
                t["embed"].append(ms(t0))
                t1 = time.perf_counter()
                hits = [h for h in idx.search(vector=vec, text=q, k=K)
                        if h.cosine >= MIN_COSINE]
                t["search"].append(ms(t1))
                t["direct"].append(ms(t0))
                return [short(h.source, s.source_root_marker) for h in hits]

            for r in range(rounds):
                for i, q in enumerate(questions):
                    # Alternate which path goes first: the second call on the
                    # same text may find the embedder in a different state.
                    first_mcp = (i + r) % 2 == 0
                    t["mcp_first"].append(first_mcp)
                    if first_mcp:
                        a_src, b_src = await via_mcp(q), await direct(q)
                    else:
                        b_src, a_src = await direct(q), await via_mcp(q)
                    same += a_src == b_src

                    session = await retriever._borrow()
                    t0 = time.perf_counter()
                    await session.call_tool("crag_stats", {})
                    t["protocol"].append(ms(t0))
        tg.cancel_scope.cancel()
    return {"timings": t, "same_results": same, "n": len(questions) * rounds}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--db", default=None, help="default: ASKDOCS_MCP_DB")
    ap.add_argument("--json", help="also write the raw timings here")
    args = ap.parse_args()
    s = Settings()
    cases = (ROOT / "evals" / "cases.jsonl").read_text().splitlines()
    questions = [json.loads(line)["question"] for line in cases if line.strip()]
    out = asyncio.run(measure(questions, s, args.db or s.mcp_db, args.rounds))
    t = out["timings"]
    print(f"{out['n']} retrievals ({len(questions)} questions x {args.rounds}); "
          f"direct and MCP returned the same passages in {out['same_results']}")
    first = t.pop("mcp_first")
    print("| path | P50 ms | P95 ms |\n|---|---|---|")
    for k, xs in t.items():
        print(f"| {k} | {percentile(xs, 50):.2f} | {percentile(xs, 95):.2f} |")
    over = [m - d for m, d in zip(t["mcp"], t["direct"], strict=True)]
    print(f"| mcp - direct (per question) | {percentile(over, 50):.2f} | "
          f"{percentile(over, 95):.2f} |")
    for label, want in (("MCP ran first", True), ("direct ran first", False)):
        sel = [(m, d) for m, d, f in zip(t["mcp"], t["direct"], first, strict=True) if f == want]
        print(f"{label}: mcp P50 {percentile([m for m, _ in sel], 50):.2f}, "
              f"direct P50 {percentile([d for _, d in sel], 50):.2f} (n={len(sel)})")
    if args.json:
        Path(args.json).write_text(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
