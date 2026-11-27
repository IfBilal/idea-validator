"""Connector contract and the defensive-parsing helpers every connector shares."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from svc_scouts.services.context import Credentials, ScoutContext
from svc_scouts.services.errors import MalformedPayload


class Payload(BaseModel):
    """Base of every payload schema: unknown keys are ignored, nothing is trusted."""

    model_config = ConfigDict(extra="ignore")


class Connector(Protocol):
    name: str

    def credentials_ok(self, credentials: Credentials) -> bool:
        """False: the source stays off (it needs a key nobody has configured)."""
        ...

    async def collect(self, ctx: ScoutContext) -> None: ...


def as_dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MalformedPayload(f"{what}: expected an object, got {type(value).__name__}")
    return value


def as_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise MalformedPayload(f"{what}: expected a list, got {type(value).__name__}")
    return value


def parse_rows[M: BaseModel](model: type[M], rows: list[Any], ctx: ScoutContext) -> Iterator[M]:
    """Validate each row; malformed ones are dropped and counted, the rest are yielded."""
    for row in rows:
        try:
            yield model.model_validate(row)
        except (ValidationError, RecursionError):
            ctx.dropped += 1


def from_epoch(value: int | float | None) -> datetime | None:
    """Unix seconds to an aware datetime; absurd values become None instead of an exception."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None
