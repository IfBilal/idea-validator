import json
import time
from typing import Any

import pytest

from common.schemas.trace import TraceRecord
from svc_agentops.services.sanitize import RejectedRecord, canonical, record_id, sanitize
from svc_agentops.testing import rec

MAX = 16 * 1024


def clean(**over: Any) -> TraceRecord:
    return sanitize(TraceRecord.model_validate(rec(**over)), max_bytes=MAX)


def test_secrets_are_redacted_again_on_ingest() -> None:
    out = clean(input={"api_key": "sk-abcdefghijklmnopqrstuvwx", "note": "Bearer abc.def.ghi"})
    text = json.dumps(out.input)
    assert "sk-abc" not in text
    assert "abc.def" not in text


def test_emails_phones_and_jwts_are_scrubbed_from_payloads_and_errors() -> None:
    jwt = ".".join(
        ["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "abcdefghijk"]
    )  # built, not literal
    out = clean(
        input={"msg": "mail bob@example.com or call +92 300 1234567"},
        output=[{"t": jwt}],
        error=f"failed for alice@corp.io with {jwt}",
    )
    blob = json.dumps(out.model_dump(mode="json"))
    for leak in ("bob@example.com", "1234567", "alice@corp.io", "eyJhbGci"):
        assert leak not in blob


def test_oversized_payloads_are_truncated_to_the_documented_cap() -> None:
    out = clean(input={"blob": "x" * 500_000})
    assert out.input["_truncated"] is True
    assert len(canonical(out)) < MAX


@pytest.mark.parametrize(
    "over",
    [
        {"run_id": "../etc/passwd"},
        {"run_id": "a/b"},
        {"run_id": ""},
        {"run_id": "r" * 65},
        {"run_id": "run 1"},
        {"run_id": "run\n1"},
        {"step_id": -1},
        {"parent_step": -2},
        {"tokens_in": -1},
        {"latency_ms": -5},
        {"cost_usd": -0.1},
        {"cost_usd": float("inf")},
        {"cost_usd": float("nan")},
        {"agent": ""},
        {"agent": "a" * 129},
        {"service": "s" * 129},
    ],
)
def test_unacceptable_records_are_rejected(over: dict[str, Any]) -> None:
    with pytest.raises(RejectedRecord):
        clean(**over)


def test_a_record_over_the_byte_cap_after_cleaning_is_rejected() -> None:
    with pytest.raises(RejectedRecord):
        sanitize(TraceRecord.model_validate(rec(input={"a": "x" * 3000})), max_bytes=500)


def test_record_id_is_stable_and_changes_with_content() -> None:
    a, b = clean(), clean()
    assert record_id(a) == record_id(b)
    assert record_id(clean(step=2)) != record_id(a)
    assert record_id(clean(output={"ok": False})) != record_id(a)
    assert len(record_id(a)) == 32


def test_error_text_is_capped() -> None:
    assert len(clean(error="e" * 50_000).error or "") <= 2000


ADVERSARIAL = ["a" * 100_000, "a." * 50_000, "1-" * 50_000, "1" * 100_000, "a@" * 200_000]


@pytest.mark.parametrize("text", ADVERSARIAL, ids=lambda t: f"{t[:4]}x{len(t)}")
@pytest.mark.parametrize("where", ["error", "input", "output"])
def test_sanitize_is_fast_on_adversarial_strings(text: str, where: str) -> None:
    over: dict[str, Any] = {where: text if where == "error" else {"k": [text, {"n": text}]}}
    started = time.perf_counter()
    clean(**over)
    assert time.perf_counter() - started < 0.5


def test_a_two_megabyte_error_is_truncated_before_any_scan() -> None:
    started = time.perf_counter()
    out = clean(error="a" * 2_000_000)
    assert time.perf_counter() - started < 0.5
    assert len(out.error or "") <= 2000


def test_many_small_strings_are_bounded_too() -> None:
    started = time.perf_counter()
    out = clean(input={"items": ["x@" * 500] * 5000})
    assert time.perf_counter() - started < 0.5
    assert len(canonical(out)) < MAX


def test_new_secret_shapes_are_redacted_in_payloads_and_errors() -> None:
    from common.testing import REDACTION_POSITIVE

    for text, secret in REDACTION_POSITIVE:
        out = clean(input={"msg": text}, error=text)
        assert secret not in json.dumps(out.model_dump(mode="json")), text
