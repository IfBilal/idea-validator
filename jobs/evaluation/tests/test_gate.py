import pytest
from evaluation.gate import check_results

APPLICABLE = {"cites_evidence": 40, "max_rounds": 40, "injection_resisted": 5}
BASE = {
    "agreement": 0.80,
    "false_go": 1,
    "behaviour": {"cites_evidence": 1.0, "max_rounds": 1.0},
    "n": 40,
    "error_rate": 0.0,
    "behaviour_applicable": APPLICABLE,
}


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
    with pytest.raises(ValueError, match="behaviour_applicable"):
        check_results(BASE, {k: v for k, v in BASE.items() if k != "behaviour_applicable"})


@pytest.mark.parametrize(("rate", "ok"), [(0.0, True), (0.1, True), (0.1001, False), (0.5, False)])
def test_error_rate_is_bounded(rate: float, ok: bool) -> None:
    assert (check_results(BASE, current(error_rate=rate)) == []) is ok


def test_error_bound_can_be_tightened() -> None:
    assert check_results(BASE, current(error_rate=0.05), max_error_rate=0.0)


@pytest.mark.parametrize(("n", "ok"), [(40, True), (41, True), (39, False), (1, False), (0, False)])
def test_fewer_scored_items_than_the_baseline_fails(n: int, ok: bool) -> None:
    assert (check_results(BASE, current(n=n)) == []) is ok


def test_a_check_that_applied_to_nothing_fails() -> None:
    zero = {**APPLICABLE, "max_rounds": 0}
    failures = check_results(BASE, current(behaviour_applicable=zero))
    assert any("max_rounds" in f and "tested nothing" in f for f in failures)
    missing = {k: v for k, v in APPLICABLE.items() if k != "cites_evidence"}
    assert check_results(BASE, current(behaviour_applicable=missing))


def test_a_check_that_applied_to_fewer_items_than_the_baseline_fails() -> None:
    fewer = {**APPLICABLE, "cites_evidence": 39}
    assert check_results(BASE, current(behaviour_applicable=fewer))
    assert (
        check_results(BASE, current(behaviour_applicable={**APPLICABLE, "cites_evidence": 41}))
        == []
    )


@pytest.mark.parametrize("value", [0, None])
def test_the_injection_check_must_have_been_exercised(value: int | None) -> None:
    applicable = {k: v for k, v in APPLICABLE.items() if k != "injection_resisted"}
    if value is not None:
        applicable["injection_resisted"] = value
    failures = check_results(BASE, current(behaviour_applicable=applicable))
    assert any("injection_resisted" in f for f in failures)
