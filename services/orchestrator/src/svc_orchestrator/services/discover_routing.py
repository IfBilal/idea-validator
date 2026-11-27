"""Pure routing for the Discover graph: ``(state) -> next node name``. No I/O.

A failed or budget-stopped run always goes to ``result`` so the user keeps what was found so far.
"""

from ..models.discover_state import DiscoverState


def _or_result(state: DiscoverState, next_node: str) -> str:
    return "result" if state.stopped else next_node


def _survivors_or_result(state: DiscoverState) -> str:
    return "validate_survivors" if state.candidates else "result"


def after_request(state: DiscoverState) -> str:
    return _or_result(state, "niches")


def after_niches(state: DiscoverState) -> str:
    if state.stopped or not state.gaps:
        return "result"  # no gap, nothing to build an idea on
    return "ideate"


def after_ideate(state: DiscoverState) -> str:
    if state.stopped:
        return "result"
    return "gap_analyst" if state.pending else _survivors_or_result(state)


def after_gap_analyst(state: DiscoverState) -> str:
    if state.stopped:
        return "result"
    return "skeptic" if state.pending else _survivors_or_result(state)


def after_skeptic(state: DiscoverState) -> str:
    return _or_result(state, "critic")


def after_critic(state: DiscoverState) -> str:
    if state.stopped:
        return "result"
    return "ideate" if state.continue_debate else _survivors_or_result(state)


def after_validate(state: DiscoverState) -> str:
    return "result"
