"""Pure routing of the Discover graph: every arrow, and stopping always goes to the result."""

from collections.abc import Callable
from typing import Any

import pytest

from svc_orchestrator.models.discover_state import DiscoverState
from svc_orchestrator.models.state import RunStatus
from svc_orchestrator.services import discover_routing as r


def state(**kw: Any) -> DiscoverState:
    base: dict[str, Any] = {"run_id": "run_1", "user_id": "u1", "count": 5}
    return DiscoverState.model_validate({**base, **kw})


IDEA = {"idea_ref": "idea_1"}
Route = Callable[[DiscoverState], str]

TABLE: list[tuple[Route, dict[str, Any], str]] = [
    (r.after_request, {}, "niches"),
    (r.after_niches, {"gaps": [{"gap_id": "g"}]}, "ideate"),
    (r.after_niches, {"gaps": []}, "result"),  # nothing to ideate from
    (r.after_ideate, {"pending": [IDEA], "candidates": [IDEA]}, "gap_analyst"),
    (r.after_ideate, {"pending": [], "candidates": [IDEA]}, "validate_survivors"),
    (r.after_ideate, {"pending": [], "candidates": []}, "result"),
    (r.after_gap_analyst, {"pending": [IDEA], "candidates": [IDEA]}, "skeptic"),
    (r.after_gap_analyst, {"pending": [], "candidates": [IDEA]}, "validate_survivors"),
    (r.after_gap_analyst, {"pending": [], "candidates": []}, "result"),
    (r.after_skeptic, {}, "critic"),
    (r.after_critic, {"continue_debate": True, "candidates": [IDEA]}, "ideate"),
    (r.after_critic, {"continue_debate": False, "candidates": [IDEA]}, "validate_survivors"),
    (r.after_critic, {"continue_debate": False, "candidates": []}, "result"),
    (r.after_validate, {}, "result"),
]


@pytest.mark.parametrize(("route", "fields", "expected"), TABLE)
def test_discover_routing_table(route: Route, fields: dict[str, Any], expected: str) -> None:
    assert route(state(**fields)) == expected


@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.BUDGET_STOPPED])
@pytest.mark.parametrize(
    "route",
    [
        r.after_request,
        r.after_niches,
        r.after_ideate,
        r.after_gap_analyst,
        r.after_skeptic,
        r.after_critic,
    ],
)
def test_a_stopped_run_goes_straight_to_the_result(route: Route, status: RunStatus) -> None:
    got = route(
        state(
            status=status,
            gaps=[{"gap_id": "g"}],
            pending=[IDEA],
            candidates=[IDEA],
            continue_debate=True,
        )
    )
    assert got == "result"


def test_every_routing_outcome_is_a_declared_edge() -> None:
    from svc_orchestrator.services.discover_graph import EDGES

    declared = {route: targets for route, targets in EDGES.values()}
    for route, _fields, expected in TABLE:
        assert expected in declared[route], (route.__name__, expected)
    assert {route for route, _ in EDGES.values()} >= {route for route, _, _ in TABLE} - {
        r.after_validate
    }
