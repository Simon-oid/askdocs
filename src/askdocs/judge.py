"""LLM judge for faithfulness: is each cited sentence supported by the
passage it cites?

One yes/no question per (sentence, cited passage), not one score per
answer: a narrow question is what a small local judge can answer
reliably, and the per-claim verdicts are what a human can spot-check.
A sentence counts as supported if any passage it cites supports it; a
sentence that cites nothing is unsupported by definition.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from .evaluation import Claim
from .llm import LLM, Delta
from .prompt import fenced

JUDGE_MAX_TOKENS = 4

JUDGE_SYSTEM = (
    "You check whether a claim is supported by a passage. Every fact in the claim "
    "(names, numbers, settings, reasons) must be stated in the passage or follow "
    "directly from it. Background knowledge does not count. "
    "Reply with exactly one word: YES or NO."
)


@dataclass(frozen=True)
class Verdict:
    claim: str
    cites: tuple[int, ...]
    supported: bool | None   # None: the judge gave no usable verdict
    by: int | None           # the cited passage that supported it


def judge_messages(claim: str, passage: str, fence: str | None = None) -> list[dict]:
    fence = fence or secrets.token_hex(6)
    user = (f"Passage and claim (each between <<<{fence} and {fence}>>>):\n\n"
            f"Passage:\n{fenced(passage, fence)}\n\nClaim:\n{fenced(claim, fence)}\n\n"
            "Is the claim supported by the passage? Answer YES or NO.")
    return [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}]


def parse_verdict(text: str) -> bool | None:
    words = text.strip().split()
    if not words:
        return None
    head = words[0].strip(".,!:*\"'").upper()
    return {"YES": True, "NO": False}.get(head)


async def complete(llm: LLM, messages: list[dict], max_tokens: int) -> str:
    parts = []
    async for ev in llm.stream(messages, max_tokens):
        if isinstance(ev, Delta):
            parts.append(ev.text)
    return "".join(parts)


async def judge_claim(llm: LLM, claim: Claim, passages: list[str]) -> Verdict:
    """`passages` are the texts the answer was given, in [n] order."""
    usable = [n for n in claim.cites if 1 <= n <= len(passages)]
    if not usable:
        return Verdict(claim.text, claim.cites, False, None)
    unsure = False
    for n in usable:
        out = parse_verdict(await complete(llm, judge_messages(claim.text, passages[n - 1]),
                                           JUDGE_MAX_TOKENS))
        if out:
            return Verdict(claim.text, claim.cites, True, n)
        unsure = unsure or out is None
    return Verdict(claim.text, claim.cites, None if unsure else False, None)
