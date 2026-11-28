"""Validate and clean one trace record on ingest (docs/harness/12 §1, 13 §8).

The emitting services already redact and cap payloads; M13 does it again because it must not trust
its callers. It also scrubs e-mail addresses and phone-like numbers (PII) and bad run ids.
"""

import hashlib
import json
import math
import re
from typing import Any

from common.redaction import redact
from common.schemas.trace import TraceRecord
from common.tracing import MAX_PAYLOAD_CHARS, bound_payload

RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # also the only characters allowed in a file name
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{8,}\d(?![\w.])")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
MAX_NAME = 128
MAX_ERROR = 2000


class RejectedRecord(ValueError):
    """A record that cannot be stored; the message says why (never contains the payload)."""


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        text = _JWT.sub("[REDACTED]", value)
        text = _EMAIL.sub("[EMAIL]", text)
        return _PHONE.sub("[PHONE]", text)
    return value


def _payload(value: Any) -> Any:
    bounded = bound_payload(_scrub(bound_payload(value)))
    text = json.dumps(bounded, default=str)
    if len(text) > MAX_PAYLOAD_CHARS + 64:  # scrubbing can only shorten; belt and braces
        return {"_truncated": True, "preview": text[:MAX_PAYLOAD_CHARS]}
    return bounded


def sanitize(record: TraceRecord, *, max_bytes: int) -> TraceRecord:
    if not RUN_ID.fullmatch(record.run_id):
        raise RejectedRecord("run_id must match [A-Za-z0-9_-]{1,64}")
    if record.step_id < 0 or (record.parent_step is not None and record.parent_step < 0):
        raise RejectedRecord("step ids must not be negative")
    if min(record.tokens_in, record.tokens_out, record.latency_ms, record.retries) < 0:
        raise RejectedRecord("counters must not be negative")
    if not math.isfinite(record.cost_usd) or record.cost_usd < 0:
        raise RejectedRecord("cost_usd must be a finite, non-negative number")
    for name in (record.agent, record.service, record.tool, record.model):
        if name is not None and len(name) > MAX_NAME:
            raise RejectedRecord("agent, service, tool and model names are limited to 128 chars")
    if not record.agent:
        raise RejectedRecord("agent must not be empty")
    error = record.error
    if error is not None:
        error = _scrub(redact(error))[:MAX_ERROR]
    clean = record.model_copy(
        update={"input": _payload(record.input), "output": _payload(record.output), "error": error}
    )
    size = len(canonical(clean).encode())
    if size > max_bytes:
        raise RejectedRecord(f"record is {size} bytes after cleaning; the limit is {max_bytes}")
    return clean


def canonical(record: TraceRecord) -> str:
    return json.dumps(
        record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), default=str
    )


def record_id(record: TraceRecord) -> str:
    """Identity of a record: a retry of the same span (same content) is the same record."""
    return hashlib.sha256(canonical(record).encode()).hexdigest()[:32]
