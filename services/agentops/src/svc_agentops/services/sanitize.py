"""Validate and clean one trace record on ingest (docs/harness/12 §1, 13 §8).

The emitting services already redact and cap payloads; M13 does it again because it must not trust
its callers. It also scrubs e-mail addresses and phone-like numbers (PII) and bad run ids.
"""

import hashlib
import json
import math
import re
from typing import Any

from common.redaction import redact, scrub_pii
from common.schemas.trace import TraceRecord
from common.tracing import MAX_PAYLOAD_CHARS, bound_payload

RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # also the only characters allowed in a file name
MAX_NAME = 128
MAX_ERROR = 2000


class RejectedRecord(ValueError):
    """A record that cannot be stored; the message says why (never contains the payload)."""


MAX_STRING = 4096  # nothing longer than the payload cap is ever scanned
MAX_ITEMS = 200  # list items / dict keys kept per container
MAX_DEPTH = 8


def _clip(value: Any, depth: int = 0) -> Any:
    """Bound the size of an arbitrary payload BEFORE any regex sees it (no ReDoS, no huge scans)."""
    if isinstance(value, str):
        return value[:MAX_STRING]
    if depth >= MAX_DEPTH:
        return "[too deep]" if isinstance(value, dict | list | tuple) else value
    if isinstance(value, dict):
        return {str(k)[:MAX_NAME]: _clip(v, depth + 1) for k, v in list(value.items())[:MAX_ITEMS]}
    if isinstance(value, list | tuple):
        return [_clip(v, depth + 1) for v in list(value)[:MAX_ITEMS]]
    return value


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return scrub_pii(redact(value))
    return value


def _payload(value: Any) -> Any:
    bounded = bound_payload(_scrub(redact(_clip(value))))
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
        error = _scrub(error[:MAX_ERROR])[:MAX_ERROR]  # cut first, then scan
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
