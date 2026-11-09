#!/usr/bin/env python3
"""How far can the LLM judge be trusted? Compares its verdicts in
judge_sample.jsonl with a second grader's labels.

    python evals/agreement.py                 # judge vs the `reference` labels
    python evals/agreement.py --label FIELD   # vs any other true/false field

`reference` holds independent labels (true/false per claim, graded on the
cited passages as the judge saw them); `reference_by` records who graded.
A second grader can add their own field and compare it the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from askdocs.evaluation import agreement

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sample", default=str(ROOT / "evals" / "results" / "judge_sample.jsonl"))
    ap.add_argument("--label", default="reference")
    args = ap.parse_args()
    rows = [json.loads(line) for line in Path(args.sample).read_text().splitlines()]
    label = args.label
    pairs = [(r["judge"], r.get(label)) for r in rows
             if r["judge"] is not None and r.get(label) is not None]
    if not pairs:
        print(f"no claims graded in `{label}`", file=sys.stderr)
        return 1
    agree, kappa = agreement(pairs)
    too_strict = sum(1 for j, g in pairs if not j and g)
    too_lenient = sum(1 for j, g in pairs if j and not g)
    print(f"judge vs {label}: {len(pairs)} claims, agreement {agree:.2f}, Cohen's kappa "
          f"{kappa:.2f}; judge rejected {too_strict} supported claim(s), accepted "
          f"{too_lenient} unsupported one(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
