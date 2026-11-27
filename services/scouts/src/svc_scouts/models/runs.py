"""Request/response shapes between connectors, the fetcher, the store and the API."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiRequest(BaseModel):
    """One call a connector wants made. GET without headers is the only thing M5 does today."""

    model_config = ConfigDict(frozen=True)

    url: str
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict, repr=False)  # may hold a token
    body: str | None = Field(default=None, repr=False)
    auth_required: bool = False  # True: the call cannot work without the headers/body


class Page(BaseModel):
    status: int
    json_: Any = None
    text: str | None = None
    flagged: bool = False  # M5's sanitiser removed instruction-like text


class ScoutState(BaseModel):
    """Per-source state kept between runs (table ``scouts.scout_state``)."""

    cursor: dict[str, Any] = Field(default_factory=dict)
    failures: int = 0
    opened_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None


RunStatus = Literal["ok", "empty", "partial", "failed", "disabled", "circuit_open"]


class RunReport(BaseModel):
    source: str
    run_id: str
    status: RunStatus
    requests: int = 0
    items: int = 0  # accepted by the connector (validated)
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    dropped: int = 0  # malformed items skipped
    flagged: int = 0  # pages where M5 removed instruction-like text
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_s: float = 0.0
