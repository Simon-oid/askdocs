import pytest

from askdocs.evaluation import Case
from askdocs.gate import (
    GateError,
    case_metrics,
    check_coverage,
    check_floors,
    compare,
    faithfulness,
    measure_noise,
    paired_diffs,
    values,
)


def ans(i, facts=1.0, refused=False, claims=None, invalid=(), recall1=1.0):
    row = {"id": i, "type": "factual", "question": f"q{i}?",
           "recall": {"1": recall1, "3": 1.0, "5": 1.0, "10": 1.0},
           "context_recall": 1.0, "fact_recall": facts,
           "refused": refused, "refusal_ok": not refused,
           "invalid_citations": list(invalid)}
    if claims is not None:
        row["claims"] = [{"supported": s} for s in claims]
    return row


def neg(i, refused=True):
    return {"id": i, "type": "offtopic", "question": f"q{i}?",
            "recall": {"1": None, "3": None, "5": None, "10": None},
            "refused": refused, "refusal_ok": refused, "invalid_citations": []}


def err(i):
    return {"id": i, "type": "factual", "question": f"q{i}?",
            "recall": {"1": 0.0, "5": 1.0}, "error": {"stage": "generation"}}


class TestCaseMetrics:
    def test_answerable_row(self):
        m = case_metrics(ans("a", facts=0.5, claims=[True, False, None]))
        assert m["key_facts"] == 0.5 and m["faithfulness"] == 0.5
        assert m["correct_refusal"] is None and m["error"] == 0.0
        assert m["context_recall"] == 1.0

    def test_unanswerable_row(self):
        m = case_metrics(neg("n", refused=False))
        assert m["correct_refusal"] == 0.0 and m["key_facts"] is None
        assert m["recall@5"] is None

    def test_failed_row_keeps_retrieval_and_counts_as_error(self):
        m = case_metrics(err("e"))
        assert m["error"] == 1.0 and m["recall@5"] == 1.0 and m["key_facts"] is None

    def test_faithfulness_without_known_verdicts_is_none(self):
        assert faithfulness({"claims": [{"supported": None}]}) is None
        assert faithfulness({}) is None

    def test_values_skip_inapplicable_cases(self):
        assert values([ans("a"), neg("n")], "key_facts") == {"a": 1.0}


class TestCoverage:
    def test_matching_results_have_no_problems(self):
        cases = [Case("a", "factual", "qa?", ("x",), ("y",))]
        assert check_coverage([ans("a")], cases) == []

    def test_missing_extra_and_changed_cases_are_reported(self):
        cases = [Case("a", "factual", "changed?", ("x",), ("y",)),
                 Case("b", "factual", "qb?", ("x",), ("y",))]
        got = check_coverage([ans("a"), ans("z")], cases)
        assert got == ["no result for case b", "result for unknown case z",
                       "a: question changed since the run"]


class TestFloors:
    def test_floor_and_ceiling(self):
        rows = [ans("a", facts=0.5), ans("b", facts=0.5, invalid=[9])]
        assert check_floors(rows, {"key_facts": {"min": 0.4}}) == []
        assert check_floors(rows, {"key_facts": {"min": 0.6}}) == [
            "key_facts 0.500 below the floor 0.6"]
        assert check_floors(rows, {"bad_citation": {"max": 0.1}}) == [
            "bad_citation 0.500 above the ceiling 0.1"]

    def test_metric_with_no_cases_fails(self):
        assert check_floors([ans("a")], {"correct_refusal": {"min": 0.9}}) == [
            "correct_refusal: no cases to score"]

    def test_unknown_metric_is_a_config_error(self):
        with pytest.raises(GateError):
            check_floors([ans("a")], {"accuracy": {"min": 1}})


class TestCompare:
    BASE = [ans(str(i)) for i in range(20)]

    def test_diffs_are_oriented_so_negative_is_worse(self):
        new = [ans("0", invalid=[7])] + self.BASE[1:]
        assert paired_diffs(self.BASE, new, "bad_citation")[0] == -1.0

    def test_only_cases_in_both_runs_are_paired(self):
        assert len(paired_diffs(self.BASE[:5], self.BASE[3:], "key_facts")) == 2

    def test_identical_runs_do_not_regress(self):
        assert not any(d.regressed for d in compare(self.BASE, self.BASE, {}, resamples=200))

    def test_a_broad_drop_beyond_noise_regresses(self):
        new = [ans(str(i), facts=0.0 if i < 12 else 1.0) for i in range(20)]
        d = {x.metric: x for x in compare(self.BASE, new, {"key_facts": 0.1}, resamples=500)}
        assert d["key_facts"].regressed and d["key_facts"].mean == -0.6

    def test_a_drop_within_noise_does_not_regress(self):
        new = [ans(str(i), facts=0.0 if i < 12 else 1.0) for i in range(20)]
        d = {x.metric: x for x in compare(self.BASE, new, {"key_facts": 0.7}, resamples=500)}
        assert not d["key_facts"].regressed

    def test_one_case_moving_is_not_significant(self):
        new = [ans("0", facts=0.0)] + self.BASE[1:]
        d = {x.metric: x for x in compare(self.BASE, new, {}, resamples=500)}
        assert d["key_facts"].hi >= 0 and not d["key_facts"].regressed


class TestNoise:
    def test_noise_is_the_largest_gap_between_runs(self):
        a = [ans("1"), ans("2")]
        b = [ans("1", facts=0.0), ans("2")]
        c = [ans("1", facts=0.5), ans("2")]
        assert measure_noise([a, b, c])["key_facts"] == 0.5

    def test_needs_two_runs(self):
        with pytest.raises(GateError):
            measure_noise([[ans("1")]])
