from datetime import UTC, datetime, timedelta

import pytest

from svc_scouts.models.runs import ScoutState
from svc_scouts.services.breaker import BreakerState, CircuitBreaker

T0 = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def breaker(failures: int = 0, opened: datetime | None = None, **kw: int) -> CircuitBreaker:
    state = ScoutState(failures=failures, opened_at=opened)
    return CircuitBreaker(
        state, threshold=kw.get("threshold", 3), cooldown_s=kw.get("cooldown", 60)
    )


def test_starts_closed_and_allows() -> None:
    b = breaker()
    assert b.status(T0) is BreakerState.CLOSED and b.allow(T0)


def test_opens_after_exactly_threshold_consecutive_failures() -> None:
    b = breaker()
    b.record_failure(T0)
    b.record_failure(T0)
    assert b.allow(T0) and b.status(T0) is BreakerState.CLOSED  # threshold - 1
    b.record_failure(T0)
    assert b.status(T0) is BreakerState.OPEN and not b.allow(T0)


def test_a_success_resets_the_count() -> None:
    b = breaker()
    b.record_failure(T0)
    b.record_failure(T0)
    b.record_success()
    b.record_failure(T0)
    b.record_failure(T0)
    assert b.allow(T0)


def test_cooldown_boundary_is_half_open_exactly_at_the_cooldown() -> None:
    b = breaker(failures=3, opened=T0)
    assert b.status(T0 + timedelta(seconds=59)) is BreakerState.OPEN
    assert b.status(T0 + timedelta(seconds=60)) is BreakerState.HALF_OPEN
    assert b.allow(T0 + timedelta(seconds=60))


def test_a_failed_probe_reopens_and_restarts_the_cooldown() -> None:
    b = breaker(failures=3, opened=T0)
    later = T0 + timedelta(seconds=120)
    b.record_failure(later)
    assert b.status(later) is BreakerState.OPEN
    assert b.status(later + timedelta(seconds=59)) is BreakerState.OPEN
    assert b.status(later + timedelta(seconds=60)) is BreakerState.HALF_OPEN


def test_a_successful_probe_closes_it() -> None:
    b = breaker(failures=3, opened=T0)
    b.record_success()
    assert b.status(T0) is BreakerState.CLOSED and b.failures == 0 and b.opened_at is None


def test_threshold_one_opens_on_the_first_failure() -> None:
    b = breaker(threshold=1)
    b.record_failure(T0)
    assert not b.allow(T0)


@pytest.mark.parametrize("threshold,cooldown", [(0, 10), (-1, 10), (3, -1)])
def test_invalid_configuration_is_refused(threshold: int, cooldown: int) -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(ScoutState(), threshold=threshold, cooldown_s=cooldown)


def test_zero_cooldown_is_always_a_probe() -> None:
    b = breaker(failures=3, opened=T0, cooldown=0)
    assert b.status(T0) is BreakerState.HALF_OPEN
