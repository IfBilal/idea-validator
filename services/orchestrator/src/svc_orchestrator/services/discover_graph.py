"""The Discover workflow as a LangGraph ``StateGraph`` (docs/harness/07 §3).

REQUEST -> NICHES -> IDEATE -> GAP_ANALYST -> SKEPTIC -> CRITIC -> (IDEATE, at most 3 rounds)
-> VALIDATE_SURVIVORS (the Validate graph, max 3 at a time) -> RESULT.
"""

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..models.discover_state import DiscoverState
from ..models.state import ValidateState
from ..repositories.events import QuietEmitter
from . import discover_routing as routing
from .discover_nodes import DiscoverNodes, RunChild
from .guard import guard
from .run_context import RunContext
from .validate_graph import RECURSION_LIMIT, build_validate_graph, graph_config

Route = Callable[[DiscoverState], str]

EDGES: dict[str, tuple[Route, list[str]]] = {
    "request": (routing.after_request, ["niches", "result"]),
    "niches": (routing.after_niches, ["ideate", "result"]),
    "ideate": (routing.after_ideate, ["gap_analyst", "validate_survivors", "result"]),
    "gap_analyst": (routing.after_gap_analyst, ["skeptic", "validate_survivors", "result"]),
    "skeptic": (routing.after_skeptic, ["critic", "result"]),
    "critic": (routing.after_critic, ["ideate", "validate_survivors", "result"]),
    "validate_survivors": (routing.after_validate, ["result"]),
}
STEP_NAMES = {
    "request": "REQUEST",
    "niches": "NICHES",
    "ideate": "IDEATE",
    "gap_analyst": "GAP_ANALYST",
    "skeptic": "SKEPTIC",
    "critic": "CRITIC",
    "validate_survivors": "VALIDATE_SURVIVORS",
    "result": "RESULT",
}


def child_context(ctx: RunContext) -> RunContext:
    """Same budget, trace and clients as the parent run, but no lifecycle events of its own."""
    return RunContext(
        run_id=ctx.run_id,
        config=ctx.config,
        ledger=ctx.ledger,
        tracer=ctx.tracer,
        emitter=QuietEmitter(ctx.emitter),
        clients=ctx.clients,
        verdict_ttl_s=ctx.verdict_ttl_s,
    )


def make_child_runner(ctx: RunContext, checkpointer: BaseCheckpointSaver[Any] | None) -> RunChild:
    """Run one idea through the Validate graph in its own checkpoint thread."""

    async def run_child(state: ValidateState, thread_id: str) -> ValidateState:
        graph = build_validate_graph(child_context(ctx), checkpointer)
        config = graph_config(thread_id)
        await graph.ainvoke(state, config)
        snapshot = await graph.aget_state(config)
        return ValidateState.model_validate(snapshot.values)

    return run_child


def build_discover_graph(
    ctx: RunContext,
    checkpointer: BaseCheckpointSaver[Any] | None,
    run_child: RunChild | None = None,
) -> CompiledStateGraph[DiscoverState, None, DiscoverState, DiscoverState]:
    nodes = DiscoverNodes(ctx, run_child or make_child_runner(ctx, checkpointer))
    graph = StateGraph(DiscoverState)
    for name, step in STEP_NAMES.items():
        graph.add_node(name, guard(ctx, step, getattr(nodes, name)))
    graph.add_edge(START, "request")
    for name, (route, targets) in EDGES.items():
        graph.add_conditional_edges(name, _traced(ctx, name, route), targets)
    graph.add_edge("result", END)
    return graph.compile(checkpointer=checkpointer)


def _traced(ctx: RunContext, name: str, route: Route) -> Route:
    def traced(state: DiscoverState) -> str:
        target = route(state)
        ctx.runner.decision("transition", {"from": name}, {"to": target, "status": state.status})
        return target

    return traced


__all__ = ["EDGES", "RECURSION_LIMIT", "build_discover_graph", "graph_config"]
