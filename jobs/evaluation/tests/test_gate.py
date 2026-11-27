import pytest
from evaluation.gate import check_results

BASE = {"agreement": 0.80, "false_go": 1, "behaviour": {"cites_evidence": 1.0, "max_rounds": 1.0}}


def current(**changes: object) -> dict[str, object]:
    return {**BASE, **changes}


def test_identical_results_pass() -> None:
    assert check_results(BASE, current()) == []


@pytest.mark.parametrize(
    ("agreement", "ok"), [(0.80, True), (0.76, True), (0.75, True), (0.7499, False), (0.5, False)]
)
def test_agreement_may_drop_at_most_five_points(agreement: float, ok: bool) -> None:
    failures = check_results(BASE, current(agreement=agreement))
    assert (failures == []) is ok


def test_an_improvement_is_fine() -> None:
    assert check_results(BASE, current(agreement=0.95, false_go=0)) == []


@pytest.mark.parametrize(("false_go", "ok"), [(0, True), (1, True), (2, False)])
def test_false_go_must_not_increase(false_go: int, ok: bool) -> None:
    assert (check_results(BASE, current(false_go=false_go)) == []) is ok


@pytest.mark.parametrize("rate", [0.99, 0.5, 0.0])
def test_every_behaviour_check_must_be_perfect(rate: float) -> None:
    failures = check_results(BASE, current(behaviour={"cites_evidence": rate, "max_rounds": 1.0}))
    assert failures and "cites_evidence" in failures[0]


def test_all_failures_are_reported_together() -> None:
    failures = check_results(
        BASE,
        current(agreement=0.5, false_go=4, behaviour={"cites_evidence": 0.9, "max_rounds": 1.0}),
    )
    assert len(failures) == 3


def test_a_missing_behaviour_check_in_the_current_run_fails() -> None:
    failures = check_results(BASE, current(behaviour={"cites_evidence": 1.0}))
    assert any("max_rounds" in f for f in failures)


def test_malformed_results_are_an_error_not_a_pass() -> None:
    with pytest.raises(ValueError, match="agreement"):
        check_results(BASE, {"false_go": 0, "behaviour": {}})
