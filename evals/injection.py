#!/usr/bin/env python3
"""Prompt-injection run: real retrieval, real model, one poisoned passage.

    python evals/injection.py              # answering model on :8081

For each host question (answerable cases from cases.jsonl), answers once
clean, then once per attack (askdocs.injection.ATTACKS) and rank: the
poisoned passage first, where the model reads it before any evidence, and
last, nearest the question and the reminder. Success is checked
deterministically (canary, link, refusal, format): no judge model.

Writes to --out (default evals/results/):
  injection.jsonl   one line per answer: question, attack, rank, answer, outcome
  injection.md      success rate per attack and rank, with bootstrap 95% CI,
                    and key-fact recall clean vs attacked
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import anyio

from askdocs.config import Settings
from askdocs.evaluation import Case, bootstrap_ci, fact_recall, load_cases
from askdocs.injection import ATTACKS, Attack, InjectingRetriever, attack_succeeded
from askdocs.llm import make_llm
from askdocs.pipeline import answer
from askdocs.retrieval import McpRetriever

ROOT = Path(__file__).resolve().parent.parent
# Answerable single-source questions whose facts the model gets right clean,
# so a drop under attack is the attack's doing.
HOSTS = ("f01", "f02", "f03", "f04", "f05", "f08")
FIRST, LAST = 1, 99            # LAST past the end: appended after the real ones


async def ask(question: str, retriever, llm, s: Settings) -> dict:
    async for name, data in answer(question, retriever, llm, k=s.top_k,
                                   max_tokens=s.llm_max_tokens):
        if name == "done":
            return data
        if name == "error":
            return {"error": data}
    return {"error": "no done event"}


def row(case: Case, attack: Attack | None, rank: int | None, done: dict,
        clean_refused: bool) -> dict:
    out = {"id": case.id, "attack": attack.id if attack else None, "rank": rank}
    if "error" in done:
        return {**out, "error": done["error"]}
    text = done["answer"]
    out.update(answer=text, refused=done["refused"],
               fact_recall=0.0 if done["refused"] else fact_recall(text, case.facts),
               invalid_citations=done["invalid_citations"])
    if attack:
        out["succeeded"] = attack_succeeded(attack, text, clean_refused)
    return out


async def run_all(cases: list[Case], s: Settings) -> list[dict]:
    real, llm = McpRetriever(s), make_llm(s)
    rows: list[dict] = []
    try:
        async with anyio.create_task_group() as tg:
            await tg.start(real.run)
            for case in cases:
                clean = await ask(case.question, real, llm, s)
                clean_refused = bool(clean.get("refused"))
                rows.append(row(case, None, None, clean, clean_refused))
                for attack in ATTACKS:
                    for rank in (FIRST, LAST):
                        r = InjectingRetriever(real, attack, rank)
                        done = await ask(case.question, r, llm, s)
                        rows.append(row(case, attack, rank, done, clean_refused))
                print(f"{case.id}: done", file=sys.stderr)
            tg.cancel_scope.cancel()
    finally:
        await llm.aclose()
    return rows


def pct(xs: list[float]) -> str:
    if not xs:
        return "n/a | 0"
    m, lo, hi = bootstrap_ci(xs)
    return f"{m:.2f} [{lo:.2f}, {hi:.2f}] | {len(xs)}"


def report(rows: list[dict], env: str) -> str:
    ok = [r for r in rows if "error" not in r]
    attacked = [r for r in ok if r["attack"]]
    clean = [r for r in ok if not r["attack"]]
    lines = ["# Prompt-injection run", "", env, "",
             "One poisoned passage among the real ones; success is checked "
             "deterministically (see `src/askdocs/injection.py`).", "",
             "| Attack | Rank | Success rate [95% CI] | n |", "|---|---|---|---|"]
    for a in ATTACKS:
        for rank, label in ((FIRST, "first"), (LAST, "last")):
            xs = [float(r["succeeded"]) for r in attacked
                  if r["attack"] == a.id and r["rank"] == rank]
            lines.append(f"| `{a.id}` ({a.goal}) | {label} | {pct(xs)} |")
    lines.append(f"| **all attacks** | both | {pct([float(r['succeeded']) for r in attacked])} |")
    lines += ["", "| Key-fact recall | Mean [95% CI] | n |", "|---|---|---|",
              f"| clean | {pct([r['fact_recall'] for r in clean])} |",
              f"| attacked | {pct([r['fact_recall'] for r in attacked])} |"]
    errors = [r for r in rows if "error" in r]
    lines += ["", f"Errors: {len(errors)}."]
    wins = [r for r in attacked if r["succeeded"]]
    if wins:
        lines += ["", "## Successful attacks", ""]
        lines += [f"- {r['id']} `{r['attack']}` rank {r['rank']}: "
                  f"{json.dumps(r['answer'][:160])}" for r in wins]
    return "\n".join(lines) + "\n"


def environment(s: Settings) -> str:
    sha = subprocess.run(["git", "-C", str(ROOT), "describe", "--always", "--dirty"],
                         capture_output=True, text=True).stdout.strip()
    corpus = (ROOT / "data" / "corpus.version")
    pins = corpus.read_text().strip() if corpus.exists() else "unknown"
    return (f"askdocs `{sha}`; corpus: {pins}; answer model: `{s.llm_model}` at "
            f"{s.base_url()}; top_k {s.top_k}; run {time.strftime('%Y-%m-%d %H:%M')}.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", default=str(ROOT / "evals" / "cases.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "evals" / "results"))
    args = ap.parse_args()
    s = Settings()
    by_id = {c.id: c for c in load_cases(Path(args.cases))}
    missing = [h for h in HOSTS if h not in by_id]
    if missing:
        print(f"host cases not in {args.cases}: {missing}", file=sys.stderr)
        return 2
    rows = anyio.run(run_all, [by_id[h] for h in HOSTS], s)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "injection.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / "injection.md").write_text(report(rows, environment(s)))
    print((out / "injection.md").read_text())
    return 1 if any("error" in r for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
