import pytest
from evaluation.metrics import LABELS, EvalReport, build_report, cohen_kappa, confusion_matrix

GO, PIVOT, NOGO, IE = LABELS


def pairs(*items: tuple[str, str]) -> tuple[list[str], list[str]]:
    human = [h for h, _ in items]
    system = [s for _, s in items]
    return human, system


def test_labels_are_the_four_verdicts_in_the_documented_order() -> None:
    assert LABELS == ("GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE")


def test_confusion_matrix_rows_are_human_and_columns_are_system() -> None:
    human, system = pairs((GO, GO), (GO, PIVOT), (NOGO, GO), (IE, IE), (PIVOT, PIVOT))
    m = confusion_matrix(human, system)
    assert m[0] == [1, 1, 0, 0]  # human GO: one GO, one PIVOT
    assert m[2] == [1, 0, 0, 0]  # human NO-GO: system said GO once
    assert m[3] == [0, 0, 0, 1]
    assert sum(map(sum, m)) == 5


def test_perfect_agreement() -> None:
    human, system = pairs(*[(label, label) for label in LABELS for _ in range(3)])
    r = build_report(human, system)
    assert (r.n, r.agreement, r.kappa, r.false_go) == (12, 1.0, 1.0, 0)


def test_known_kappa_value() -> None:
    # 2x2 textbook case: 20 agree GO, 5 GO/NOGO, 10 NOGO/GO, 15 agree NOGO -> po=.7 pe=.5 kappa=.4
    human = [GO] * 25 + [NOGO] * 25
    system = [GO] * 20 + [NOGO] * 5 + [GO] * 10 + [NOGO] * 15
    assert cohen_kappa(human, system) == pytest.approx(0.4)


def test_kappa_when_everything_is_one_class() -> None:
    assert cohen_kappa([GO] * 5, [GO] * 5) == 1.0
    assert cohen_kappa([GO] * 5, [NOGO] * 5) == 0.0


def test_false_go_counts_only_system_go_when_human_said_no_go() -> None:
    human, system = pairs((NOGO, GO), (NOGO, GO), (PIVOT, GO), (GO, GO), (NOGO, NOGO))
    r = build_report(human, system)
    assert r.false_go == 2
    assert r.optimistic_go == 1  # system GO where the human said PIVOT: reported separately
    assert r.false_go_rate == pytest.approx(2 / 3)  # 2 of the 3 human NO-GO items


def test_false_go_rate_is_zero_when_there_are_no_human_no_go_items() -> None:
    human, system = pairs((GO, GO), (PIVOT, PIVOT))
    assert build_report(human, system).false_go_rate == 0.0


def test_per_class_precision_and_recall() -> None:
    human, system = pairs((GO, GO), (GO, PIVOT), (PIVOT, GO), (PIVOT, PIVOT))
    r = build_report(human, system)
    assert r.per_class["GO"] == {"precision": 0.5, "recall": 0.5, "support": 2}
    assert r.per_class["NO-GO"] == {"precision": 0.0, "recall": 0.0, "support": 0}


def test_agreement_is_a_fraction_of_all_items() -> None:
    human, system = pairs((GO, GO), (GO, GO), (GO, PIVOT), (PIVOT, PIVOT))
    assert build_report(human, system).agreement == 0.75


@pytest.mark.parametrize(
    ("human", "system", "message"),
    [
        ([GO], [GO, GO], "same length"),
        ([GO], ["MAYBE"], "unknown"),
        (["go"], [GO], "unknown"),
        ([], [], "empty"),
    ],
)
def test_bad_input_is_rejected(human: list[str], system: list[str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_report(human, system)


def test_report_serialises_to_plain_json() -> None:
    import json

    human, system = pairs((GO, GO), (NOGO, GO))
    data = build_report(human, system).to_dict()
    assert json.loads(json.dumps(data))["false_go"] == 1
    assert set(data) >= {"n", "agreement", "kappa", "confusion_matrix", "labels", "false_go"}


def test_report_type() -> None:
    human, system = pairs((GO, GO))
    assert isinstance(build_report(human, system), EvalReport)
