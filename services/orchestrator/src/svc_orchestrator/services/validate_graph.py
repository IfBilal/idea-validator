"""The Validate workflow as a LangGraph ``StateGraph`` (docs/harness/07 §2, 29 §2).

The graph is built per run so the nodes can close over that run's ledger, tracer and clients; the
checkpointer (keyed by ``thread_id = run_id``) is what survives between the first invocation and a
resume after ``interrupt()``.
"""

from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..models.state import ValidateState
from . import routing
from .run_context import RunContext
from .validate_nodes import ValidateNodes

RECURSION_LIMIT = 100  # bounded loops only: questions <= 3, scouting once, one recompute

Route = Callable[[ValidateState], str]

# node -> (step name shown to users, routing function, every node it may lead to)
EDGES: dict[str, tuple[Route, list[str]]] = {
    "rewrite": (routing.after_rewrite, ["intake", "card"]),
    "intake": (routing.after_intake, ["cache_check", "card"]),
    "cache_check": (routing.after_cache_check, ["evidence", "card"]),
    "evidence": (routing.after_evidence, ["deep_search_decision", "features", "card"]),
    "deep_search_decision": (
        routing.after_deep_search_decision,
        ["await_approval", "scout", "features", "card"],
    ),
    "await_approval": (routing.after_await_approval, ["scout", "features", "card"]),
    "scout": (routing.after_scout, ["evidence", "card"]),
    "features": (routing.after_features, ["scorers", "card"]),
    "scorers": (routing.after_scorers, ["clarify_check", "card"]),
    "clarify_check": (routing.after_clarify_check, ["await_answer", "verdict", "card"]),
    "await_answer": (routing.after_await_answer, ["clarify_check", "card"]),
    "verdict": (routing.after_verdict, ["claims_check", "card"]),
    "claims_check": (routing.after_claims_check, ["recompute", "stability", "card"]),
    "recompute": (routing.after_recompute, ["stability", "card"]),
    "stability": (routing.after_stability, ["card"]),
}
STEP_NAMES = {
    "rewrite": "REWRITE",
    "intake": "INTAKE",
    "cache_check": "CACHE_CHECK",
    "evidence": "EVIDENCE",
    "deep_search_decision": "DEEP_SEARCH_DECISION",
    "await_approval": "WAIT_APPROVAL",
    "scout": "SCOUT",
    "features": "FEATURES",
    "scorers": "SCORERS",
    "clarify_check": "CLARIFY",
    "await_answer": "WAIT_ANSWER",
    "verdict": "VERDICT",
    "claims_check": "CHALLENGE",
    "recompute": "RECOMPUTE",
    "stability": "STABILITY",
    "card": "CARD",
}
PAUSING = frozenset({"await_approval", "await_answer"})  # resumed nodes: no duplicate announcements


def graph_config(run_id: str) -> RunnableConfig:
    return RunnableConfig(configurable={"thread_id": run_id}, recursion_limit=RECURSION_LIMIT)


def build_validate_graph(
    ctx: RunContext, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[ValidateState, None, ValidateState, ValidateState]:
    nodes = ValidateNodes(ctx)
    graph = StateGraph(ValidateState)
    graph.add_node("rewrite", nodes.rewrite_entry)  # a no-op unless this is a what-if
    for name in STEP_NAMES:
        if name == "rewrite":
            continue
        node = getattr(nodes, name)
        if name in PAUSING:
            graph.add_node(name, nodes.guarded(STEP_NAMES[name], node, announce=False))
        else:
            graph.add_node(name, nodes.guarded(STEP_NAMES[name], node))
    graph.add_edge(START, "rewrite")
    for name, (route, targets) in EDGES.items():
        graph.add_conditional_edges(name, _traced(ctx, name, route), targets)
    graph.add_edge("card", END)
    return graph.compile(checkpointer=checkpointer)


def _traced(ctx: RunContext, name: str, route: Route) -> Route:
    """Wrap a pure routing function so each transition is recorded as a decision (07 §8)."""

    def traced(state: ValidateState) -> str:
        target = route(state)
        ctx.runner.decision("transition", {"from": name}, {"to": target, "status": state.status})
        return target

    return traced
