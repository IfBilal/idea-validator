"""A run's trace as the gateway lets it reach a browser: M13's shape, bounded, nothing extra."""

from typing import Any

from pydantic import Field

from .base import Upstream

MAX_ITEMS = 5000  # M13 returns at most this many records per run


class TraceView(Upstream):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    totals: dict[str, Any] = Field(default_factory=dict, max_length=32)
    by_agent: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=200)
    truncated: bool = False
    tree: list[dict[str, Any]] | None = Field(default=None, max_length=MAX_ITEMS)
    records: list[dict[str, Any]] | None = Field(default=None, max_length=MAX_ITEMS)
