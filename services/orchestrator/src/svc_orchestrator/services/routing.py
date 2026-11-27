"""Pure routing for the Validate graph: ``(state) -> next node name``. No I/O, no clock.

Every arrow in docs/harness/07 §2 is one function here. A failed or budget-stopped run always goes
to ``card`` so the user sees a partial result instead of a dead end.
"""

from ..models.state import ValidateState


def _or_card(state: ValidateState, next_node: str) -> str:
    return "card" if state.stopped else next_node


def after_intake(state: ValidateState) -> str:
    return _or_card(state, "cache_check")


def after_cache_check(state: ValidateState) -> str:
    if state.stopped or state.cache_hit:
        return "card"
    return "evidence"


def after_evidence(state: ValidateState) -> str:
    if state.stopped:
        return "card"
    if state.wants_deep_search and not state.deep_search_done:
        return "deep_search_decision"
    return "features"


def after_deep_search_decision(state: ValidateState) -> str:
    if state.stopped:
        return "card"
    if state.pending_approval:
        return "await_approval"
    return "features" if state.deep_search_done else "scout"  # done = skipped for lack of budget


def after_await_approval(state: ValidateState) -> str:
    if state.stopped:
        return "card"
    return "scout" if state.approval_decision == "approved" else "features"


def after_scout(state: ValidateState) -> str:
    return _or_card(state, "evidence")


def after_features(state: ValidateState) -> str:
    return _or_card(state, "scorers")


def after_scorers(state: ValidateState) -> str:
    return _or_card(state, "clarify_check")


def after_clarify_check(state: ValidateState) -> str:
    if state.stopped:
        return "card"
    return "await_answer" if state.pending_question else "verdict"


def after_await_answer(state: ValidateState) -> str:
    return _or_card(state, "clarify_check")


def after_verdict(state: ValidateState) -> str:
    return _or_card(state, "claims_check")


def after_claims_check(state: ValidateState) -> str:
    if state.stopped:
        return "card"
    return "recompute" if state.removed_claims and not state.recomputed else "stability"


def after_recompute(state: ValidateState) -> str:
    return _or_card(state, "stability")


def after_stability(state: ValidateState) -> str:
    return "card"
