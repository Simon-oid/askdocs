#!/usr/bin/env python3
"""Runs the labelled eval set against the real stack and writes a report.

    python evals/run.py                     # all cases, judge on :8082
    python evals/run.py --only f01,u03      # a few cases
    python evals/run.py --phase answer      # answers only (judge model not needed)
    python evals/run.py --phase judge       # then grade that saved run

Needs what the service needs (crag-mcp, the embedder, the answering model,
settings from .env / ASKDOCS_*), plus a judge model behind an
OpenAI-compatible endpoint (--judge-url). The judge should not be the
model that wrote the answers: a model grading itself shares its own
blind spots.

Writes to --out (default evals/results/):
  results.jsonl       one line per case: retrieval ranks, answer, verdicts
  passages.jsonl      the passages each answer was given (for --phase judge)
  report.md           means with bootstrap 95% intervals, failures listed
  judge_sample.jsonl  a seeded sample of judged claims, to grade independently
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import anyio

from askdocs.config import Settings
from askdocs.evaluation import (
    Case,
    bootstrap_ci,
    claims,
    fact_recall,
    load_cases,
    ranked_sources,
    refusal_ok,
    source_recall,
)
from askdocs.gate import faithfulness
from askdocs.judge import judge_claim
from askdocs.llm import ChatCompletionsLLM, make_llm
from askdocs.pipeline import answer
from askdocs.prompt import MAX_PASSAGE_CHARS
from askdocs.retrieval import McpRetriever

ROOT = Path(__file__).resolve().parent.parent
RECALL_KS = (1, 3, 5, 10)
SAMPLE_SIZE = 25
SAMPLE_SEED = 20261109


def judge_llm(url: str, model: str) -> ChatCompletionsLLM:
    return ChatCompletionsLLM(url, model, None, temperature=0.0, connect_timeout_s=5,
                              read_timeout_s=60, max_retries=2, retry_backoff_s=0.5)


async def run_case(case: Case, retriever, llm, s: Settings) -> dict:
    deep = await retriever.search(case.question, max(RECALL_KS))
    ranked = ranked_sources([p.source for p in deep])
    out: dict = {"id": case.id, "type": case.type, "question": case.question,
                 "ranked_sources": ranked,
                 "recall": {str(k): source_recall(case, ranked, k) for k in RECALL_KS}}
    passages, done, error = [], None, None
    async for name, data in answer(case.question, retriever, llm, k=s.top_k,
                                   max_tokens=s.llm_max_tokens):
        if name == "sources":
            passages = data["sources"]
        elif name == "done":
            done = data
        elif name == "error":
            error = data
    out["answer_sources"] = [p["source"] for p in passages]
    # What reached the model, as opposed to what the retriever can find at k=10.
    out["context_recall"] = source_recall(case, out["answer_sources"], len(passages))
    # What the model saw: the prompt truncates each passage.
    out["passages"] = [p["text"][:MAX_PASSAGE_CHARS] for p in passages]
    if done is None:
        return {**out, "error": error}
    out.update(answer=done["answer"], refused=done["refused"],
               refusal_ok=refusal_ok(case, done["refused"]),
               invalid_citations=done["invalid_citations"], truncated=done["truncated"],
               timings=done["timings"], usage=done["usage"])
    if case.answerable:
        out["fact_recall"] = 0.0 if done["refused"] else fact_recall(done["answer"], case.facts)
    return out


async def judge_results(results: list[dict], judge: ChatCompletionsLLM) -> None:
    """Adds per-sentence verdicts to the answered, answerable cases."""
    try:
        for r in results:
            if r["type"] in ("factual", "multi") and "error" not in r and not r["refused"]:
                verdicts = [await judge_claim(judge, c, r["passages"])
                            for c in claims(r["answer"])]
                r["claims"] = [asdict(v) for v in verdicts]
                print(f"{r['id']}: judged", file=sys.stderr)
    finally:
        await judge.aclose()


def pct(xs: list[float]) -> str:
    if not xs:
        return "n/a | 0"
    m, lo, hi = bootstrap_ci(xs)
    return f"{m:.2f} [{lo:.2f}, {hi:.2f}] | {len(xs)}"


def percentile(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


def summary_rows(results: list[dict]) -> list[str]:
    ok = [r for r in results if "error" not in r]
    ans = [r for r in ok if r["type"] in ("factual", "multi")]
    neg = [r for r in ok if r["type"] in ("unanswerable", "offtopic")]
    rows = [f"| Retrieval recall@{k} (answerable) | {pct([r['recall'][str(k)] for r in ans])} |"
            for k in RECALL_KS]
    rows.append("| Context recall: right file among the passages the model got | "
                f"{pct([r['context_recall'] for r in ans])} |")
    rows += [
        f"| Key-fact recall (answerable; refusal = 0) | {pct([r['fact_recall'] for r in ans])} |",
        f"| Wrong refusal (answerable) | {pct([float(r['refused']) for r in ans])} |",
        f"| Correct refusal (unanswerable + off-topic) | "
        f"{pct([float(r['refusal_ok']) for r in neg])} |",
        f"| Faithfulness: judged sentences supported (per answer) | "
        f"{pct([f for r in ans if (f := faithfulness(r)) is not None])} |",
        f"| Answers with an out-of-range citation | "
        f"{pct([float(bool(r['invalid_citations'])) for r in ok])} |",
    ]
    for t in ("factual", "multi"):
        sub = [r for r in ans if r["type"] == t]
        rows.append(f"| Key-fact recall, {t} only | {pct([r['fact_recall'] for r in sub])} |")
    return rows


def latency_rows(results: list[dict]) -> list[str]:
    rows = []
    for key in ("retrieval_ms", "ttft_ms", "total_ms"):
        xs = [r["timings"][key] for r in results if key in r.get("timings", {})]
        if xs:
            rows.append(f"| {key} | {percentile(xs, 50):.0f} | {percentile(xs, 95):.0f} "
                        f"| {len(xs)} |")
    return rows


def failures(results: list[dict]) -> list[str]:
    out = []
    for r in results:
        why = []
        if "error" in r:
            why.append(f"error: {r['error']}")
        else:
            if not r["refusal_ok"]:
                why.append("refused" if r["refused"] else "answered an unanswerable question")
            if r.get("fact_recall", 1.0) < 1.0 and not r["refused"]:
                why.append(f"key facts {r['fact_recall']:.2f}")
            if (f := faithfulness(r)) is not None and f < 1.0:
                why.append(f"faithfulness {f:.2f}")
        if r["recall"]["5"] is not None and r["recall"]["5"] < 1.0:
            why.append(f"recall@5 {r['recall']['5']:.2f}")
        if why:
            out.append(f"- `{r['id']}` ({r['type']}): {'; '.join(why)}")
    return out or ["- none"]


def environment(s: Settings, judge_model: str | None) -> str:
    commit = subprocess.run(["git", "-C", str(ROOT), "describe", "--always", "--dirty"],
                            capture_output=True, text=True).stdout.strip()
    corpus = ROOT / "data" / "corpus.version"
    corpus_v = corpus.read_text().strip() if corpus.exists() else "unknown"
    return (f"askdocs `{commit}`; corpus: {corpus_v}; answer model: `{s.llm_model}` at "
            f"{s.base_url()}; judge: `{judge_model or 'none'}`; top_k {s.top_k}; "
            f"run {time.strftime('%Y-%m-%d %H:%M')}.")


def write_report(path: Path, results: list[dict], env: str) -> None:
    lines = [
        "# askdocs eval report", "", env, "",
        "Means with bootstrap 95% intervals (10,000 resamples of the cases, seed 20261109).",
        "", "| Metric | Mean [95% CI] | n |", "|---|---|---|", *summary_rows(results), "",
        "## Latency (informational: one client, sequential)", "",
        "| Stage | P50 ms | P95 ms | n |", "|---|---|---|---|", *latency_rows(results), "",
        "## Cases that missed something", "", *failures(results), "",
    ]
    path.write_text("\n".join(lines))


def judge_sample(results: list[dict], cases: dict[str, Case]) -> list[dict]:
    pool = [(r, i, c) for r in results for i, c in enumerate(r.get("claims", []))
            if c["cites"]]
    rng = random.Random(SAMPLE_SEED)
    picked = rng.sample(pool, min(SAMPLE_SIZE, len(pool)))
    return [{"case": r["id"], "claim_index": i, "question": cases[r["id"]].question,
             "claim": c["claim"], "cites": c["cites"],
             "cited_sources": [r["answer_sources"][n - 1] for n in c["cites"]
                               if 1 <= n <= len(r["answer_sources"])],
             "cited_passages": [r["passages"][n - 1] for n in c["cites"]
                                if 1 <= n <= len(r["passages"])],
             "judge": c["supported"], "reference": None}
            for r, i, c in sorted(picked, key=lambda t: (t[0]["id"], t[1]))]


async def run_all(cases: list[Case], s: Settings) -> list[dict]:
    retriever, llm = McpRetriever(s), make_llm(s)
    results = []
    try:
        async with anyio.create_task_group() as tg:
            await tg.start(retriever.run)
            for case in cases:
                r = await run_case(case, retriever, llm, s)
                results.append(r)
                print(f"{case.id}: {'ERROR' if 'error' in r else 'ok'}", file=sys.stderr)
            tg.cancel_scope.cancel()
    finally:
        await llm.aclose()
    return results


def save_results(out: Path, results: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # Passages go to their own file (not committed): they would triple results.jsonl.
    slim = [{k: v for k, v in r.items() if k != "passages"} for r in results]
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in slim))
    (out / "passages.jsonl").write_text("".join(
        json.dumps({"id": r["id"], "passages": r["passages"]}) + "\n" for r in results))


def load_results(out: Path) -> list[dict]:
    def rows(name: str) -> list[dict]:
        return [json.loads(line) for line in (out / name).read_text().splitlines()]

    texts = {x["id"]: x["passages"] for x in rows("passages.jsonl")}
    return [{**r, "passages": texts[r["id"]]} for r in rows("results.jsonl")]


def write_outputs(out: Path, env: str, cases: list[Case], results: list[dict]) -> None:
    save_results(out, results)
    write_report(out / "report.md", results, env)
    sample = judge_sample(results, {c.id: c for c in cases})
    (out / "judge_sample.jsonl").write_text("".join(json.dumps(x) + "\n" for x in sample))
    print((out / "report.md").read_text())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", default=str(ROOT / "evals" / "cases.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "evals" / "results"))
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--phase", choices=("all", "answer", "judge"), default="all",
                    help="answer: no judge needed; judge: grade a saved answer run "
                         "(lets one GPU/RAM budget hold one model at a time)")
    ap.add_argument("--judge-url", default="http://localhost:8082/v1")
    ap.add_argument("--judge-model", default="qwen2.5-7b-instruct-q4_k_m")
    args = ap.parse_args()
    s = Settings()
    s.check()
    out = Path(args.out)
    cases = load_cases(Path(args.cases))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c.id in wanted]
    if args.phase == "judge":
        results = load_results(out)
        cases = [c for c in cases if c.id in {r["id"] for r in results}]
    else:
        results = asyncio.run(run_all(cases, s))
    if args.phase != "answer":
        asyncio.run(judge_results(results, judge_llm(args.judge_url, args.judge_model)))
    judged = args.judge_model if args.phase != "answer" else None
    write_outputs(out, environment(s, judged), cases, results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
