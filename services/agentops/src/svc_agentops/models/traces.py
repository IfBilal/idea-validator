"""M13 API and storage models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from common.schemas.base import Message


@dataclass(frozen=True)
class IndexRow:
    run_id: str
    record_id: str
    ts: datetime
    kind: str
    agent: str
    service: str | None
    step_id: int
    parent_id: int | None
    status: str
    cost: float
    tokens_in: int
    tokens_out: int
    latency: int


class Rejection(Message):
    index: int
    reason: str


class IngestResult(Message):
    accepted: int
    duplicates: int
    rejected: list[Rejection]


class Totals(Message):
    model_config = ConfigDict(extra="forbid", frozen=False)  # filled in while summing

    steps: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0  # sum of the steps' own latencies (nested steps overlap)
    errors: int = 0


class RunTotals(Totals):
    started_at: datetime | None = None
    ended_at: datetime | None = None
    wall_ms: int = 0  # first start to last end


class TraceNode(Message):
    model_config = ConfigDict(extra="forbid", frozen=False)  # linked while the tree is built

    record_id: str
    record: dict[str, Any]
    children: list["TraceNode"] = Field(default_factory=list)
    orphan: bool = False  # parent_step names a step that is not in the store (yet)


class TraceTree(Message):
    run_id: str
    totals: RunTotals
    by_agent: dict[str, Totals]
    truncated: bool
    tree: list[TraceNode]


class TraceFlat(Message):
    run_id: str
    totals: RunTotals
    by_agent: dict[str, Totals]
    truncated: bool
    records: list[dict[str, Any]]


class RunSummary(Message):
    run_id: str
    started_at: datetime
    ended_at: datetime
    steps: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    errors: int


class RunList(Message):
    runs: list[RunSummary]
    next: str | None
