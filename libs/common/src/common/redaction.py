"""Secret redaction for logs and traces (docs/harness/13 §10, 12 §1)."""

import re
from typing import Any

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(r"key|token|password|passwd|secret|authorization|cookie", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9._~+/=-]+|sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}"
)


def _scrub_text(value: str) -> str:
    return _SECRET_VALUE.sub(lambda m: f"{m.group(1)}{REDACTED}" if m.group(1) else REDACTED, value)


def redact(value: Any) -> Any:
    """Return a copy with sensitive keys blanked and secret-looking strings scrubbed."""
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value
