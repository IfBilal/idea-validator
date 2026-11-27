"""Routing is pure: (state) -> next node name. One row per arrow in docs/harness/07 §2."""

from collections.abc import Callable

import pytest

from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.services import routing


def state(**kw: object) -> ValidateState:
    base: dict[str, object] = {
        "run_id": "run_1",
        "user_id": "u1",
        "idea_id": "idea_1",
        "idea_text": "an idea",
    }
    return ValidateState.model_validate({**base, **kw})


Route = Callable[[ValidateState], str]
STOPPED = [{"status": RunStatus.FAILED}, {"status": RunStatus.BUDGET_STOPPED}]

TABLE: list[tuple[Route, dict[str, object], str]] = [
    (routing.after_intake, {}, "cache_check"),
    (routing.after_cache_check, {"cache_hit": False}, "evidence"),
    (routing.after_cache_check, {"cache_hit": True}, "card"),
    (routing.after_evidence, {}, "features"),
    (routing.after_evidence, {"wants_deep_search": True}, "deep_search_decision"),
    (routing.after_evidence, {"wants_deep_search": True, "deep_search_done": True}, "features"),
    (routing.after_deep_search_decision, {}, "scout"),
    (
        routing.after_deep_search_decision,
        {"pending_approval": {"approval_id": "a1"}},
        "await_approval",
    ),
    (routing.after_deep_search_decision, {"deep_search_done": True}, "features"),
    (routing.after_await_approval, {"approval_decision": "approved"}, "scout"),
    (routing.after_await_approval, {"approval_decision": "declined"}, "features"),
    (routing.after_await_approval, {"approval_decision": None}, "features"),
    (routing.after_scout, {}, "evidence"),
    (routing.after_features, {}, "scorers"),
    (routing.after_scorers, {}, "clarify_check"),
    (routing.after_clarify_check, {}, "verdict"),
    (routing.after_clarify_check, {"pending_question": {"question_id": "q1"}}, "await_answer"),
    (routing.after_await_answer, {}, "clarify_check"),
    (routing.after_verdict, {}, "claims_check"),
    (routing.after_claims_check, {}, "stability"),
    (routing.after_claims_check, {"removed_claims": ["cl_risk"]}, "recompute"),
    (routing.after_claims_check, {"removed_claims": ["cl_risk"], "recomputed": True}, "stability"),
    (routing.after_recompute, {}, "stability"),
    (routing.after_stability, {}, "card"),
]


@pytest.mark.parametrize(("route", "fields", "expected"), TABLE)
def test_routing_table(route: Route, fields: dict[str, object], expected: str) -> None:
    assert route(state(**fields)) == expected


@pytest.mark.parametrize("stop", STOPPED)
@pytest.mark.parametrize(
    "route",
    [
        routing.after_intake,
        routing.after_cache_check,
        routing.after_evidence,
        routing.after_deep_search_decision,
        routing.after_await_approval,
        routing.after_scout,
        routing.after_features,
        routing.after_scorers,
        routing.after_clarify_check,
        routing.after_await_answer,
        routing.after_verdict,
        routing.after_claims_check,
        routing.after_recompute,
        routing.after_stability,
    ],
)
def test_a_failed_or_budget_stopped_run_always_goes_to_the_card(
    route: Route, stop: dict[str, object]
) -> None:
    # even with a pending question or a cache hit set: stopping wins, partial results are shown
    assert route(state(**stop, pending_question={"question_id": "q"}, cache_hit=True)) == "card"


def test_awaiting_states_are_not_stopped() -> None:
    assert routing.after_intake(state(status=RunStatus.AWAITING_INPUT)) == "cache_check"


def test_stop_beats_a_pending_approval_too() -> None:
    s = state(status=RunStatus.BUDGET_STOPPED, pending_approval={"approval_id": "a"})
    assert routing.after_deep_search_decision(s) == "card"


def test_every_routing_outcome_is_a_declared_edge_of_the_graph() -> None:
    from svc_orchestrator.services.validate_graph import EDGES

    by_route = {route: targets for route, targets in EDGES.values()}
    for route, _fields, expected in TABLE:
        assert expected in by_route[route], (route.__name__, expected)
    assert {r for r, _ in EDGES.values()} == {route for route, _, _ in TABLE}
