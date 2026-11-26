from common.redaction import REDACTED, redact


def test_sensitive_keys_are_blanked_at_any_depth() -> None:
    data = {
        "api_key": "abc",
        "nested": {"Authorization": "Bearer xyz", "ok": 1},
        "x": [{"token": "t"}],
    }
    out = redact(data)
    assert out["api_key"] == REDACTED
    assert out["nested"]["Authorization"] == REDACTED
    assert out["nested"]["ok"] == 1
    assert out["x"][0]["token"] == REDACTED


def test_secret_looking_values_are_scrubbed_in_text() -> None:
    out = redact("call with Bearer abc.def-123 and sk-abcdefghijklmnopqrstuv done")
    assert "abc.def-123" not in out
    assert "sk-abcdefghijklmnopqrstuv" not in out
    assert "Bearer " in out


def test_plain_values_are_unchanged() -> None:
    assert redact({"name": "idea", "n": 3, "flag": True}) == {"name": "idea", "n": 3, "flag": True}


def test_input_is_not_mutated() -> None:
    data = {"password": "p"}
    redact(data)
    assert data == {"password": "p"}
