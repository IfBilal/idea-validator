import time

import pytest

from common.redaction import REDACTED, redact, scrub_pii
from common.testing import REDACTION_NEGATIVE, REDACTION_POSITIVE


@pytest.mark.parametrize(("text", "secret"), REDACTION_POSITIVE)
def test_secret_shapes_are_redacted_in_free_text(text: str, secret: str) -> None:
    out = redact(text)
    assert secret not in out
    assert REDACTED in out


@pytest.mark.parametrize("text", REDACTION_NEGATIVE)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert redact(text) == text
    assert scrub_pii(text) == text


def test_pii_is_scrubbed() -> None:
    out = scrub_pii("mail bob@example.com or call +92 300 1234567 now")
    assert "bob@example.com" not in out
    assert "1234567" not in out


ADVERSARIAL = [
    "a" * 100_000,
    "a." * 50_000,
    "1-" * 50_000,
    "1" * 100_000,
    "@" * 100_000,
    "Bearer " * 20_000,
    "eyJ" * 40_000,
    "a@" * 50_000,
    "://" + "a:" * 50_000,
]


@pytest.mark.parametrize("text", ADVERSARIAL, ids=lambda t: f"{t[:6]}x{len(t)}")
def test_redaction_is_linear_on_adversarial_strings(text: str) -> None:
    started = time.perf_counter()
    redact(text)
    scrub_pii(text)
    assert time.perf_counter() - started < 0.5
