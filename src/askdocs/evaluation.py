"""Evaluation metrics, kept pure so they are tested like any other code.

Retrieval and generation are scored separately: a wrong answer over the
right passages is a prompt/model problem, a wrong answer over the wrong
passages is a retrieval problem, and one number would hide which.

Every mean is reported with a bootstrap 95% interval (resampling cases),
because with ~45 cases one wrong answer moves a rate by two points.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

ANSWERABLE = frozenset({"factual", "multi"})
NOT_ANSWERABLE = frozenset({"unanswerable", "offtopic"})
TYPES = ANSWERABLE | NOT_ANSWERABLE

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261109

_CITE = re.compile(r"\[(\d{1,2})\]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_ONLY_CITES = re.compile(r"^(\s*\[\d{1,2}\])+[.!?]?\s*$")
_PUNCT_GAP = re.compile(r"\s+([.!?,;:])")     # "1.2 [1]." -> "1.2 ." -> "1.2."


class CaseError(ValueError):
    pass


@dataclass(frozen=True)
class Case:
    """One labelled question.

    For `factual`, `sources` are the acceptable homes of the answer (any
    one is enough); for `multi`, all of them are needed. `facts` are
    strings the answer must contain; `a|b` accepts either spelling.
    """
    id: str
    type: str
    question: str
    sources: tuple[str, ...]
    facts: tuple[str, ...]

    @property
    def answerable(self) -> bool:
        return self.type in ANSWERABLE


@dataclass(frozen=True)
class Claim:
    text: str            # the sentence without its [n] markers
    cites: tuple[int, ...]


def parse_case(row: dict) -> Case:
    try:
        case = Case(row["id"], row["type"], row["question"].strip(),
                    tuple(row["sources"]), tuple(row["facts"]))
    except (KeyError, AttributeError, TypeError) as e:
        raise CaseError(f"malformed case {row!r}: {e}") from e
    if case.type not in TYPES:
        raise CaseError(f"{case.id}: unknown type {case.type!r}")
    if not case.question:
        raise CaseError(f"{case.id}: empty question")
    if case.answerable and not (case.sources and case.facts):
        raise CaseError(f"{case.id}: an answerable case needs sources and facts")
    if not case.answerable and (case.sources or case.facts):
        raise CaseError(f"{case.id}: an unanswerable case must not list sources or facts")
    return case


def load_cases(path: Path) -> list[Case]:
    cases = [parse_case(json.loads(line))
             for line in path.read_text().splitlines() if line.strip()]
    ids = [c.id for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise CaseError(f"duplicate case ids: {dupes}")
    return cases


def ranked_sources(sources: list[str]) -> list[str]:
    """Distinct sources in rank order (several chunks can share a file)."""
    return list(dict.fromkeys(sources))


def source_recall(case: Case, ranked: list[str], k: int) -> float | None:
    """1/0 for factual (any acceptable source in the top k), the fraction
    of required sources for multi, None when there is nothing to find."""
    if not case.answerable:
        return None
    top = set(ranked[:k])
    if case.type == "factual":
        return float(any(s in top for s in case.sources))
    return sum(s in top for s in case.sources) / len(case.sources)


def _norm(text: str) -> str:
    return " ".join(text.lower().replace(" ", " ").split())


def fact_recall(answer: str, facts: tuple[str, ...]) -> float:
    """Fraction of key facts present in the answer (case-insensitive)."""
    if not facts:
        raise ValueError("no facts to check")
    text = _norm(answer)
    found = sum(any(_norm(alt) in text for alt in fact.split("|")) for fact in facts)
    return found / len(facts)


def refusal_ok(case: Case, refused: bool) -> bool:
    return refused != case.answerable


def claims(answer: str) -> list[Claim]:
    """Split an answer into sentences with the sources each one cites.

    A fragment made only of markers ("[2].") belongs to the sentence
    before it, which is how small models often place them.
    """
    out: list[Claim] = []
    for part in _SENTENCE_END.split(answer.strip()):
        cites = tuple(dict.fromkeys(int(n) for n in _CITE.findall(part)))
        if _ONLY_CITES.match(part) and out:
            prev = out[-1]
            out[-1] = Claim(prev.text, tuple(dict.fromkeys(prev.cites + cites)))
            continue
        text = _PUNCT_GAP.sub(r"\1", " ".join(_CITE.sub("", part).split()))
        if text:
            out.append(Claim(text, cites))
    return out


def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("mean of nothing")
    return sum(values) / len(values)


def bootstrap_ci(values: list[float], *, resamples: int = BOOTSTRAP_RESAMPLES,
                 seed: int = BOOTSTRAP_SEED, level: float = 0.95) -> tuple[float, float, float]:
    """Mean and percentile bootstrap interval, resampling cases."""
    m = mean(values)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    tail = (1 - level) / 2
    lo = means[int(tail * (resamples - 1))]
    hi = means[int((1 - tail) * (resamples - 1))]
    return m, lo, hi


def agreement(pairs: list[tuple[bool, bool]]) -> tuple[float, float]:
    """Raw agreement and Cohen's kappa between two binary graders."""
    if not pairs:
        raise ValueError("no pairs")
    n = len(pairs)
    agree = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    kappa = 1.0 if chance == 1 else (agree - chance) / (1 - chance)
    return agree, kappa
