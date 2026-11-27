"""Shared by the typed clients: turn an upstream failure into an error safe for a browser.

The text of an upstream error, its host name and its status never reach the caller. A 401 or 403
from an internal service means *the gateway* is misconfigured, so it is reported as a 502, never as
the user's own authorisation failing.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from common.errors import (
    BudgetExceeded,
    Conflict,
    DomainError,
    NotFound,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
)

from ..models.base import Upstream

UNAVAILABLE = "the service is temporarily unavailable"
UNEXPECTED = "the service returned an unexpected answer"


def public_error(
    exc: DomainError, what: str, conflict: str = "request conflicts with the state"
) -> DomainError:
    if isinstance(exc, NotFound):
        return NotFound(f"{what} not found")
    if isinstance(exc, Conflict):
        return Conflict(conflict)
    if isinstance(exc, ValidationFailed):
        return ValidationFailed("request failed validation")
    if isinstance(exc, BudgetExceeded):
        return BudgetExceeded("the run budget is exhausted")
    if isinstance(exc, UpstreamTimeout):
        return UpstreamTimeout("the service took too long to answer")
    return UpstreamError(UNAVAILABLE)


async def guarded[T](call: Callable[[], Awaitable[T]], what: str, conflict: str | None = None) -> T:
    try:
        return await call()
    except DomainError as exc:
        raise public_error(exc, what, conflict or "request conflicts with the state") from exc
    except ValueError as exc:  # a body that is not JSON
        raise UpstreamError(UNEXPECTED) from exc


def _lenient[M: Upstream](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise UpstreamError(UNEXPECTED) from exc


def parse[M: Upstream](model: type[M], data: Any) -> M:
    return _lenient(model, data)


def parse_list[M: Upstream](model: type[M], data: Any) -> list[M]:
    if not isinstance(data, list):
        raise UpstreamError(UNEXPECTED)
    return [_lenient(model, item) for item in data]
