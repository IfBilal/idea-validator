"""Per-source circuit breaker. State lives in ``scout_state`` because CronJob pods are short-lived.

closed     fewer than ``threshold`` consecutive failures: runs are allowed.
open       ``threshold`` failures in a row and the cooldown has not passed: runs are skipped.
half-open  cooldown passed: exactly one probe run is allowed; success closes, failure re-opens.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from svc_scouts.models.runs import ScoutState


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, state: ScoutState, *, threshold: int, cooldown_s: int) -> None:
        if threshold < 1 or cooldown_s < 0:
            raise ValueError("threshold must be >= 1 and cooldown >= 0")
        self._state = state
        self._threshold = threshold
        self._cooldown = timedelta(seconds=cooldown_s)

    @property
    def failures(self) -> int:
        return self._state.failures

    @property
    def opened_at(self) -> datetime | None:
        return self._state.opened_at

    def status(self, now: datetime) -> BreakerState:
        if self._state.failures < self._threshold or self._state.opened_at is None:
            return BreakerState.CLOSED
        if now - self._state.opened_at < self._cooldown:
            return BreakerState.OPEN
        return BreakerState.HALF_OPEN

    def allow(self, now: datetime) -> bool:
        return self.status(now) is not BreakerState.OPEN

    def record_success(self) -> None:
        self._state.failures = 0
        self._state.opened_at = None

    def record_failure(self, now: datetime) -> None:
        self._state.failures += 1
        if self._state.failures >= self._threshold:
            self._state.opened_at = now  # a failed half-open probe restarts the cooldown
