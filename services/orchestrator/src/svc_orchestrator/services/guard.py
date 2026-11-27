"""Wrap a graph node: announce the transition, map failures and budget stops to run status."""

from collections.abc import Awaitable
from typing import Any, Protocol, TypeVar

from common.errors import BudgetExceeded, DomainError

from ..models.state import RunStatus
from .run_context import RunContext

Update = dict[str, Any]
S = TypeVar("S", contravariant=True)


class Node(Protocol[S]):
    def __call__(self, state: S) -> Awaitable[Update]: ...


def guard(
    ctx: RunContext,
    name: str,
    fn: Node[Any],
    *,
    announce: bool = True,
) -> Node[Any]:
    async def node(state: Any) -> Update:  # Any: LangGraph reads this hint
        if announce:
            await ctx.emitter.emit("state_changed", **{"from": state.step, "to": name})
        try:
            update = await fn(state)
        except BudgetExceeded as exc:
            ctx.runner.decision(
                "budget_stop", {"step": name}, {"reason": exc.message, "to": "card"}
            )
            update = {"status": RunStatus.BUDGET_STOPPED, "stop_reason": exc.message}
        except DomainError as exc:
            ctx.runner.decision("failed", {"step": name}, {"reason": exc.message, "to": "card"})
            update = {"status": RunStatus.FAILED, "error": f"{name}: {exc.message}"}
        return {"step": name, **update}

    return node
