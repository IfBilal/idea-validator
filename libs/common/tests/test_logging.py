import json

import pytest
from common.context import request_id_var, run_id_var
from common.logging import configure_logging, get_logger


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    out = capsys.readouterr().out.strip().splitlines()
    return [json.loads(line) for line in out]


def test_logs_are_json_lines_with_service_and_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("kb", "INFO")
    req = request_id_var.set("req_1")
    run = run_id_var.set("run_1")
    try:
        get_logger().info("hello", n=2)
    finally:
        request_id_var.reset(req)
        run_id_var.reset(run)
    (line,) = _lines(capsys)
    assert line["event"] == "hello"
    assert line["service"] == "kb"
    assert line["request_id"] == "req_1"
    assert line["run_id"] == "run_1"
    assert line["level"] == "info"
    assert "timestamp" in line


def test_secrets_never_reach_the_log(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("tools", "INFO")
    get_logger().info("call", api_key="sk-supersecretvalue1234567", detail="Bearer abc123")
    out = capsys.readouterr().out
    assert "supersecret" not in out
    assert "abc123" not in out


def test_level_filter_drops_debug(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("kb", "WARNING")
    get_logger().info("quiet")
    get_logger().warning("loud")
    assert [line["event"] for line in _lines(capsys)] == ["loud"]
