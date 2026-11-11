#!/usr/bin/env python3
"""Pretty-printer for scripts/demo.sh.

    curl -sN .../ask ... | demo_render.py stream     # an SSE answer, as it arrives
    curl -s  .../stats   | demo_render.py stats
"""

from __future__ import annotations

import json
import sys


def stream() -> int:
    event = None
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if line.startswith("event: "):
            event = line[7:]
            continue
        if not line.startswith("data: "):
            continue
        d = json.loads(line[6:])
        if event == "sources":
            for s in d["sources"]:
                print(f"  [{s['n']}] {s['source']}  (cosine {s['cosine']:.2f})")
            print()
        elif event == "token":
            print(d["text"], end="", flush=True)
        elif event == "done":
            t = d["timings"]
            what = f"refused ({d['refusal_reason']})" if d["refused"] else f"cites {d['citations']}"
            first = f", first token {t['ttft_ms']:.0f} ms" if "ttft_ms" in t else ""
            print(f"\n\n  -> {what}; invalid citations {d['invalid_citations']}; "
                  f"{t['total_ms']:.0f} ms total{first}")
        elif event == "error":
            print(f"\n  -> error at {d['stage']}: {d['message']}")
            return 1
    return 0


def stats() -> int:
    s = json.load(sys.stdin)
    print(f"  answers {s['answers']}, refusals {s['refusals']}, errors {s['errors']}")
    for k in ("retrieval_ms", "ttft_ms", "total_ms", "prompt_tokens", "completion_tokens"):
        v = s[k]
        if v["n"]:
            print(f"  {k:18s} p50 {v['p50']:>8.1f}   p95 {v['p95']:>8.1f}   (n={v['n']})")
    return 0


if __name__ == "__main__":
    sys.exit({"stream": stream, "stats": stats}[sys.argv[1]]())
