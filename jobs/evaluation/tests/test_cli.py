import json
from pathlib import Path

import pytest
from evaluation.cli import main

RES = {
    "agreement": 0.8,
    "false_go": 0,
    "behaviour": {"a": 1.0},
    "n": 10,
    "error_rate": 0.0,
    "behaviour_applicable": {"a": 10, "injection_resisted": 2},
}


def test_gate_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base, ok, bad = tmp_path / "b.json", tmp_path / "o.json", tmp_path / "x.json"
    base.write_text(json.dumps(RES))
    ok.write_text(json.dumps(RES))
    bad.write_text(json.dumps({**RES, "false_go": 3}))
    assert main(["gate", str(base), str(ok)]) == 0
    assert main(["gate", str(base), str(bad)]) == 1
    assert "false GO increased" in capsys.readouterr().err


def test_tune_prints_a_proposal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cases = tmp_path / "c.jsonl"
    cases.write_text("")
    assert main(["tune", str(cases), "config/verdict.yaml"]) == 0
    assert json.loads(capsys.readouterr().out)["recommend"] == "keep_defaults"


def test_bad_usage_is_exit_2() -> None:
    assert main([]) == 2
    assert main(["gate"]) == 2
