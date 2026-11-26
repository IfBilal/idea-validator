from collections.abc import Callable

import pytest
from conftest import GOOD, WORKED_EXAMPLE, WORKED_EXAMPLE_CONF

from common.errors import ValidationFailed
from svc_verdict.models.api import Change, Unknown
from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine import evaluate
from svc_verdict.services.engine_config import EngineConfig
from svc_verdict.services.whatif import (
    apply_change,
    evaluate_with_whatif,
    suggest,
    value_of_information,
)

Build = Callable[..., EngineInput]


def worked(build: Build) -> EngineInput:
    return build("portfolio", WORKED_EXAMPLE, WORKED_EXAMPLE_CONF)


def test_worked_example_first_candidate_is_competition_to_three_and_stable(
    build: Build, cfg: EngineConfig
) -> None:
    (first, *_) = evaluate_with_whatif(worked(build), cfg).what_would_make_go or []
    assert (first.dimension, first.from_, first.to) == ("competition", 2.0, 3.0)
    assert (first.new_score, first.new_verdict, first.stable) == (73.5, "GO", True)


def test_a_stable_go_has_no_candidates(build: Build, cfg: EngineConfig) -> None:
    assert evaluate_with_whatif(build(), cfg).what_would_make_go == []


def test_candidates_are_sorted_by_smallest_delta_and_capped_at_three(
    build: Build, cfg: EngineConfig
) -> None:
    candidates = evaluate_with_whatif(
        build("course_project", {d: 3.0 for d in GOOD}), cfg
    ).what_would_make_go
    assert candidates is not None
    assert len(candidates) <= cfg.whatif.max_candidates
    deltas = [c.delta for c in candidates if c.delta is not None]
    assert deltas == sorted(deltas)


def test_feasibility_gate_failure_names_the_hours_needed(build: Build, cfg: EngineConfig) -> None:
    inp = build("course_project", effort=30, hpw=20, weeks=1, team=1)
    candidates = suggest(inp, cfg, evaluate(inp, cfg))
    gate = next(c for c in candidates if c.dimension == "feasibility_gate")
    assert (gate.field, gate.from_, gate.to) == ("hours_per_week", 20, 30)
    assert gate.alt == "or cut scope by 10 h"


def test_a_change_to_a_dimension_re_evaluates_with_the_new_value(
    build: Build, cfg: EngineConfig
) -> None:
    changed = apply_change(worked(build), Change(dimension="competition", to=3.0))
    result = evaluate(changed, cfg)
    assert (result.score, result.stability) == (73.5, "STABLE")


def test_a_profile_change_can_clear_the_feasibility_gate(build: Build, cfg: EngineConfig) -> None:
    inp = build("course_project", effort=30, hpw=20, weeks=1, team=1)
    assert evaluate(inp, cfg).verdict == "NO-GO"
    changed = apply_change(inp, Change(profile_field="hours_per_week", to=30))
    assert evaluate(changed, cfg).gates["feasibility"] == "pass"


def test_an_impossible_change_is_a_validation_error(build: Build) -> None:
    with pytest.raises(ValidationFailed):
        apply_change(build(), Change(profile_field="team_size", to=0))


def test_value_of_information_says_a_question_is_worth_asking_when_the_verdict_flips(
    build: Build, cfg: EngineConfig
) -> None:
    inp = build("portfolio", {d: 3.5 for d in GOOD})
    answer = value_of_information(inp, cfg, Unknown(scope="score", field="feasibility"), 2.0, 4.5)
    assert answer.low.verdict == "PIVOT"
    assert answer.high.verdict == "GO"
    assert answer.verdict_changes is True


def test_value_of_information_says_not_to_ask_when_the_verdict_cannot_change(
    build: Build, cfg: EngineConfig
) -> None:
    answer = value_of_information(
        build(), cfg, Unknown(scope="profile", field="hours_per_week"), 20, 40
    )
    assert answer.verdict_changes is False


def test_value_of_information_on_a_gate_input(build: Build, cfg: EngineConfig) -> None:
    answer = value_of_information(
        build(), cfg, Unknown(scope="gate_input", field="n_sources"), 1, 5
    )
    assert answer.low.verdict == "INSUFFICIENT_EVIDENCE"
    assert answer.verdict_changes is True


def test_unknown_must_name_a_field_of_its_scope() -> None:
    with pytest.raises(ValueError, match="not a valid"):
        Unknown(scope="score", field="hours_per_week")
