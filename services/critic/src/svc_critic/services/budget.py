"""One budget per claim-check request, shared by every judge call made while it is open.

A ``ContextVar`` holding a plain int would be copied into each task of a ``gather`` and the cap
would be bypassed; a mutable object created before the claims are checked is shared instead.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class RequestBudget:
    calls: int = 0
    started: float = field(default_factory=time.monotonic)

    def allow(self, max_calls: int, deadline_s: float) -> bool:
        """Take one call if the request is under both its call cap and its overall deadline."""
        if self.calls >= max_calls or time.monotonic() - self.started > deadline_s:
            return False
        self.calls += 1
        return True


_current: ContextVar[RequestBudget | None] = ContextVar("critic_request_budget", default=None)


def current_budget() -> RequestBudget | None:
    return _current.get()


@contextmanager
def request_scope() -> Iterator[RequestBudget]:
    budget = RequestBudget()
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)
