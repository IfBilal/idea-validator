"""Secret and PII redaction for logs and traces (docs/harness/13 §10, 12 §1).

Every quantifier is bounded, so each pattern costs at most a constant factor per character:
redaction stays linear even on adversarial input (it runs on request paths).
"""

import re
from re import Match
from typing import Any

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(r"key|token|password|passwd|secret|authorization|cookie", re.IGNORECASE)

# (pattern, replacement) pairs; replacements keep the harmless prefix (``Bearer ``, ``key=``)
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(Bearer\s{1,8})[A-Za-z0-9._~+/=-]{1,2048}"), rf"\1{REDACTED}"),
    (re.compile(r"(Basic\s{1,8})[A-Za-z0-9+/=]{8,2048}"), rf"\1{REDACTED}"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,256}"), REDACTED),
    (re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{8,256}"), REDACTED),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,256}"), REDACTED),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,256}"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), REDACTED),
    (re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,256}"), REDACTED),
    (re.compile(r"AIza[A-Za-z0-9_-]{35}"), REDACTED),
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{8,1500}\.[A-Za-z0-9_-]{8,1500}\.[A-Za-z0-9_-]{0,1500}"),
        REDACTED,
    ),
    # user:password@host in a URL
    (re.compile(r"(://)[^\s/@:]{1,128}:[^\s/@]{1,256}@"), rf"\1{REDACTED}@"),
    # key=value / key: value in free text (``max_tokens=5`` does not match: no word boundary)
    (
        re.compile(
            r"\b((?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|password"
            r"|passwd|pwd)\s{0,3}[=:]\s{0,3})[^\s&\"',;]{1,256}",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
]
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,8}")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d ().-]{7,18}\d(?![\w.])")


def _scrub_text(value: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def scrub_pii(value: str) -> str:
    """Mask e-mail addresses and phone-like numbers (linear: bounded quantifiers only)."""
    value = _EMAIL.sub("[EMAIL]", value)
    return _PHONE.sub(_phone, value)


def _phone(match: Match[str]) -> str:
    digits = sum(c.isdigit() for c in match.group(0))
    return "[PHONE]" if 9 <= digits <= 15 else match.group(0)


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
