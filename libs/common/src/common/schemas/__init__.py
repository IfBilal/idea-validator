"""Pydantic mirrors of ``schemas/*.schema.json`` (JSON Schema is the source of truth)."""

from .base import Message
from .cards import EvidenceItem, IdeaCard
from .claims import Claim, ClaimCheckRequest, ClaimCheckResponse, ClaimResult
from .fetch import FetchRequest, FetchResponse, SanitiserReport
from .profile import StudentProfile
from .scoring import DimensionScore, ScoreEvidence, ScoreRequest
from .trace import RunEvent, TraceRecord
from .types import DIMENSIONS
from .verdict import (
    Gates,
    Sensitivity,
    Thresholds,
    Verdict,
    VerdictVersions,
    Weights,
    WhatWouldMakeGo,
)

__all__ = [
    "DIMENSIONS",
    "Claim",
    "ClaimCheckRequest",
    "ClaimCheckResponse",
    "ClaimResult",
    "DimensionScore",
    "EvidenceItem",
    "FetchRequest",
    "FetchResponse",
    "Gates",
    "IdeaCard",
    "Message",
    "RunEvent",
    "SanitiserReport",
    "ScoreEvidence",
    "ScoreRequest",
    "Sensitivity",
    "StudentProfile",
    "Thresholds",
    "TraceRecord",
    "Verdict",
    "VerdictVersions",
    "Weights",
    "WhatWouldMakeGo",
]
