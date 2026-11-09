#!/usr/bin/env python3
"""Locust CSVs of a step run -> one markdown table, one row per step.

    python loadtest/summarize.py out/      # reads out/u*_stats.csv

Locust's percentiles are approximate: above 100 ms it rounds response
times to two significant digits before bucketing them.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path


def rows(path: Path) -> dict[str, dict]:
    with path.open() as f:
        return {r["Name"]: r for r in csv.DictReader(f)}


def ms(r: dict | None, col: str) -> str:
    return r[col] if r and r["Request Count"] != "0" else "–"


def users(p: Path) -> int:
    return int(re.findall(r"\d+", p.stem)[0])


def main() -> int:
    out = Path(sys.argv[1])
    steps = sorted(out.glob("u*_stats.csv"), key=users)
    print("| users | answers/s | ttft P50 / P95 ms | answer P50 / P95 ms "
          "| answers | rejected (503) | failed |")
    print("|---|---|---|---|---|---|---|")
    for p in steps:
        r = rows(p)
        a, t, rej, post = r.get("answer"), r.get("ttft"), r.get("rejected"), r.get("POST /ask")
        n = int(a["Request Count"]) if a else 0
        rejected = int(rej["Request Count"]) if rej else 0
        failed = int(post["Failure Count"]) if post else 0
        offered = n + rejected + failed
        print(f"| {users(p)} | {float(a['Requests/s']) if a else 0:.2f} "
              f"| {ms(t, '50%')} / {ms(t, '95%')} | {ms(a, '50%')} / {ms(a, '95%')} "
              f"| {n} | {rejected} ({rejected / offered:.0%}) | {failed} |"
              if offered else f"| {p.stem} | no requests |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
