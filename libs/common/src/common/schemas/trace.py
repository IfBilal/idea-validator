from typing import Any, Literal

from pydantic import AwareDatetime

from .base import Message

TraceKind = Literal["tool_call", "llm_call", "decision"]
RunEventType = Literal[
    "run_started",
    "state_changed",
    "agent_started",
    "agent_finished",
    "tool_call",
    "llm_call",
    "spend_update",
    "question",
    "approval_required",
    "idea_dropped",
    "partial_result",
    "verdict",
    "run_completed",
    "run_failed",
    "budget_stopped",
    "heartbeat",
]


class TraceRecord(Message):
    run_id: str
    step_id: int
    parent_step: int | None
    agent: str
    kind: TraceKind
    tool: str | None
    input: Any
    output: Any
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    retries: int
    model: str | None
    cache_hit: bool
    error: str | None
    ts: AwareDatetime
    service: str | None = None
    prompt_version: str | None = None
    rubric_version: str | None = None
    fallback: bool | None = None


class RunEvent(Message):
    run_id: str
    seq: int
    ts: AwareDatetime
    type: RunEventType
    payload: dict[str, Any]
