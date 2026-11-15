#!/usr/bin/env python3
"""Applies askdocs.attribution to the answers of a saved run, without the models.

    python evals/reattribute.py evals/results /tmp/reattributed
    python evals/run.py --phase judge --out /tmp/reattributed

The model's answers stay exactly as they were, so comparing the output with
the input (evals/gate.py compare IN OUT) measures the check alone, free of
the run-to-run noise a fresh generation adds. The verdicts are dropped: the
answers changed, so they must be judged again.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from askdocs.attribution import attribute
from askdocs.evaluation import Case, fact_recall, load_cases, refusal_ok
from askdocs.prompt import REFUSAL, citations

ROOT = Path(__file__).resolve().parent.parent


def reattribute(row: dict, passages: list[str], case: Case) -> dict:
    if "error" in row or row["refused"]:
        return row
    model = row.get("model_answer") or row["answer"]
    checked = attribute(model, passages)
    text, refused = checked.answer, False
    if not text:
        text, refused = REFUSAL, True
    _, invalid = citations(text, len(passages))
    out = {k: v for k, v in row.items() if k != "claims"}
    out.update(answer=text, model_answer=model, refused=refused, invalid_citations=invalid,
               refusal_ok=refusal_ok(case, refused),
               attribution={"recited": list(checked.recited), "dropped": list(checked.dropped)})
    if case.answerable:
        out["fact_recall"] = 0.0 if refused else fact_recall(text, case.facts)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--cases", default=str(ROOT / "evals" / "cases.jsonl"))
    args = ap.parse_args()
    src, dst = Path(args.src), Path(args.dst)
    if src.resolve() == dst.resolve():
        print("refusing to overwrite the source run", file=sys.stderr)
        return 2
    cases = {c.id: c for c in load_cases(Path(args.cases))}
    rows = [json.loads(x) for x in (src / "results.jsonl").read_text().splitlines()]
    texts = {(p := json.loads(x))["id"]: p["passages"]
             for x in (src / "passages.jsonl").read_text().splitlines()}
    out = [reattribute(r, texts[r["id"]], cases[r["id"]]) for r in rows]
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
    (dst / "passages.jsonl").write_text((src / "passages.jsonl").read_text())
    (dst / "report.md").write_text((src / "report.md").read_text())
    changed = sum(a.get("answer") != b.get("answer") for a, b in zip(rows, out, strict=True))
    print(f"{changed} of {len(rows)} answers changed; judge them with "
          f"evals/run.py --phase judge --out {dst}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
