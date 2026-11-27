"""Startup recovery as a background task: retry while M2 is down, then keep sweeping for orphans.

A restart can happen while M2 (the run mirror) is still starting, so a failed listing is retried
with a doubling delay. A crashed replica's leases lapse after their TTL, so one pass right after
startup can miss its runs: the loop sweeps again every ``interval_s`` and adopts them then. The same
sweep lets a healthy replica take over the runs of a dead one.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from common.logging import get_logger

log = get_logger("recovery")


class Recoverable(Protocol):
    async def recover(self) -> dict[str, int]: ...


async def recovery_loop(
    service: Recoverable,
    *,
    interval_s: float,
    attempts: int = 5,
    retry_delay_s: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rounds: int | None = None,
) -> None:
    """One pass, then (if ``interval_s`` > 0) another every ``interval_s``; ``rounds`` caps it."""
    done = 0
    while True:
        delay = retry_delay_s
        for attempt in range(1, attempts + 1):
            try:
                outcomes = await service.recover()
            except Exception as exc:  # M2 down or slow: try again, never crash the service
                log.warning("recovery_failed", attempt=attempt, error=type(exc).__name__)
                if attempt < attempts:
                    await sleep(delay)
                    delay *= 2
                continue
            if outcomes.keys() - {"known", "leased_elsewhere"}:
                log.info("recovery_done", **outcomes)
            break
        done += 1
        if interval_s <= 0 or (rounds is not None and done >= rounds):
            return
        await sleep(interval_s)
