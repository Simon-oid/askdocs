#!/usr/bin/env python3
"""The eval gate.

    python evals/gate.py check                 # CI: committed results fresh and above the floors
    python evals/gate.py compare BASE NEW      # locally: did NEW regress against BASE?
    python evals/gate.py noise RUN RUN [...]   # measure run-to-run noise (--write to save it)

BASE, NEW and RUN are output directories of evals/run.py. CI has no GPU or
models, so it cannot rerun the eval: it checks that the committed results
cover the current case set and clear the floors in evals/gate.json. The
regression test against a baseline runs where the models are.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from askdocs.evaluation import load_cases
from askdocs.gate import check_coverage, check_floors, compare, measure_noise

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "evals" / "gate.json"


def rows(run_dir: str) -> list[dict]:
    path = Path(run_dir) / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cmd_check(args, cfg: dict) -> int:
    results = rows(args.results)
    problems = check_coverage(results, load_cases(Path(args.cases)))
    problems += check_floors(results, cfg["floors"])
    for p in problems:
        print(f"FAIL {p}")
    if not problems:
        print(f"ok: {len(results)} results match the case set and clear "
              f"{len(cfg['floors'])} floors")
    return 1 if problems else 0


def cmd_compare(args, cfg: dict) -> int:
    diffs = compare(rows(args.base), rows(args.new), cfg["noise"])
    print("| Metric | n | new - base (negative = worse) [95% CI] | noise | verdict |")
    print("|---|---|---|---|---|")
    for d in diffs:
        verdict = "REGRESSION" if d.regressed else "ok"
        print(f"| {d.metric} | {d.n} | {d.mean:+.3f} [{d.lo:+.3f}, {d.hi:+.3f}] "
              f"| {d.noise:.3f} | {verdict} |")
    return 1 if any(d.regressed for d in diffs) else 0


def cmd_noise(args, cfg: dict) -> int:
    noise = measure_noise([rows(r) for r in args.runs])
    print(json.dumps(noise, indent=2))
    if args.write:
        CONFIG.write_text(json.dumps({**cfg, "noise": noise, "noise_runs": len(args.runs)},
                                     indent=2) + "\n")
        print(f"written to {CONFIG}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="The eval gate.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--results", default=str(ROOT / "evals" / "results"))
    c.add_argument("--cases", default=str(ROOT / "evals" / "cases.jsonl"))
    p = sub.add_parser("compare")
    p.add_argument("base")
    p.add_argument("new")
    n = sub.add_parser("noise")
    n.add_argument("runs", nargs="+")
    n.add_argument("--write", action="store_true")
    args = ap.parse_args()
    cfg = json.loads(CONFIG.read_text())
    return {"check": cmd_check, "compare": cmd_compare, "noise": cmd_noise}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
