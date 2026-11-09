"""The eval gate: per-case metrics, absolute floors, and a paired
regression test that knows how noisy a single run is.

Why not "fail if the mean drops": at temperature 0 four runs of the same
code spread 0.77-0.87 in key-fact recall (random prompt fence, batched
slots). A gate on the raw mean would fail on noise. A drop counts as a
regression only when

  1. the paired bootstrap interval of (new - base) over cases excludes
     zero, so it is not the luck of which cases moved, and
  2. the mean drop is larger than the measured run-to-run noise, so it
     is not the wobble two runs of identical code already show.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from .evaluation import BOOTSTRAP_RESAMPLES, Case, bootstrap_ci, mean

METRICS = ("recall@1", "recall@5", "context_recall", "key_facts", "wrong_refusal",
           "correct_refusal", "faithfulness", "bad_citation", "error")
LOWER_IS_BETTER = frozenset({"wrong_refusal", "bad_citation", "error"})


class GateError(ValueError):
    pass


def faithfulness(row: dict) -> float | None:
    known = [c["supported"] for c in row.get("claims", []) if c["supported"] is not None]
    return sum(known) / len(known) if known else None


def case_metrics(row: dict) -> dict[str, float | None]:
    """The value of each metric for one result row; None where it does not
    apply (no recall for an unanswerable case, no faithfulness for a
    refusal). A failed case still has its retrieval scores."""
    answerable = row["type"] in ("factual", "multi")
    rec = row.get("recall", {})
    out: dict[str, float | None] = dict.fromkeys(METRICS)
    out["recall@1"], out["recall@5"] = rec.get("1"), rec.get("5")
    out["context_recall"] = row.get("context_recall")
    out["error"] = float("error" in row)
    if "error" in row:
        return out
    if answerable:
        out["key_facts"] = row["fact_recall"]
        out["wrong_refusal"] = float(row["refused"])
        out["faithfulness"] = faithfulness(row)
    else:
        out["correct_refusal"] = float(row["refusal_ok"])
    out["bad_citation"] = float(bool(row["invalid_citations"]))
    return out


def values(rows: list[dict], metric: str) -> dict[str, float]:
    """case id -> value, for the cases where the metric applies."""
    return {r["id"]: v for r in rows if (v := case_metrics(r)[metric]) is not None}


def check_coverage(rows: list[dict], cases: list[Case]) -> list[str]:
    """Problems that make committed results stale for the current case set."""
    want = {c.id: c for c in cases}
    got = {r["id"]: r for r in rows}
    problems = [f"no result for case {i}" for i in sorted(want.keys() - got.keys())]
    problems += [f"result for unknown case {i}" for i in sorted(got.keys() - want.keys())]
    problems += [f"{i}: question changed since the run"
                 for i in sorted(want.keys() & got.keys())
                 if got[i]["question"] != want[i].question or got[i]["type"] != want[i].type]
    return problems


def check_floors(rows: list[dict], floors: dict[str, dict]) -> list[str]:
    problems = []
    for metric, bound in floors.items():
        if metric not in METRICS:
            raise GateError(f"unknown metric in floors: {metric}")
        xs = list(values(rows, metric).values())
        if not xs:
            problems.append(f"{metric}: no cases to score")
            continue
        m = mean(xs)
        if "min" in bound and m < bound["min"]:
            problems.append(f"{metric} {m:.3f} below the floor {bound['min']}")
        if "max" in bound and m > bound["max"]:
            problems.append(f"{metric} {m:.3f} above the ceiling {bound['max']}")
    return problems


@dataclass(frozen=True)
class Diff:
    metric: str
    n: int
    mean: float       # new - base, signed so that negative is worse
    lo: float
    hi: float
    noise: float
    regressed: bool


def paired_diffs(base: list[dict], new: list[dict], metric: str) -> list[float]:
    """Per-case new - base, oriented so that a negative value is worse."""
    b, n = values(base, metric), values(new, metric)
    sign = -1.0 if metric in LOWER_IS_BETTER else 1.0
    return [sign * (n[i] - b[i]) for i in sorted(b.keys() & n.keys())]


def compare(base: list[dict], new: list[dict], noise: dict[str, float], *,
            resamples: int = BOOTSTRAP_RESAMPLES) -> list[Diff]:
    out = []
    for metric in METRICS:
        ds = paired_diffs(base, new, metric)
        if not ds:
            continue
        m, lo, hi = bootstrap_ci(ds, resamples=resamples)
        margin = noise.get(metric, 0.0)
        out.append(Diff(metric, len(ds), m, lo, hi, margin, hi < 0 and -m > margin))
    return out


def measure_noise(runs: list[list[dict]]) -> dict[str, float]:
    """Run-to-run noise per metric: the largest |mean paired difference|
    between any two runs of the same code."""
    if len(runs) < 2:
        raise GateError("need at least two runs of the same code")
    out = {}
    for metric in METRICS:
        gaps = [abs(mean(ds)) for a, b in itertools.combinations(runs, 2)
                if (ds := paired_diffs(a, b, metric))]
        if gaps:
            out[metric] = round(max(gaps), 4)
    return out
