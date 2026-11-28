"""Replay re-runs the pure M11 engine on a recorded run and proves (or disproves) the verdict."""

import copy
from pathlib import Path
from typing import Any

import pytest
from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine_config import EngineConfig, load_engine_config
from svc_verdict.services.whatif import evaluate_with_whatif

from agentops.replay import format_report, load_from_url, main, replay_records
from common.schemas.types import DIMENSIONS
from svc_agentops.repositories.files import JsonlTraceFiles
from svc_agentops.testing import rec

ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = ROOT / "config"


@pytest.fixture(scope="module")
def rules() -> EngineConfig:
    return load_engine_config(CONFIG_DIR)


def engine_input(**scores: float) -> dict[str, Any]:
    values = {d: 4.0 for d in DIMENSIONS} | scores
    return {
        "idea_id": "idea_1",
        "version": 1,
        "run_id": "run_1",
        "profile": {"goal": "startup", "hours_per_week": 10, "weeks_available": 12, "team_size": 2},
        "scores": [
            {
                "dimension": d,
                "score": values[d],
                "confidence": 0.8,
                "evidence_ids": [f"card_aaaaaaaa#c{i}"],
            }
            for i, d in enumerate(DIMENSIONS)
        ],
        "gate_inputs": {"n_sources": 6, "n_platforms": 3, "estimated_effort_hours": 100},
        "objections_accepted": [],
    }


def recorded_run(rules: EngineConfig, **scores: float) -> list[dict[str, Any]]:
    """Records as the orchestrator writes them, with M11's real answer as the recorded output."""
    request = engine_input(**scores)
    result = evaluate_with_whatif(EngineInput.model_validate(request), rules).model_dump(
        mode="json"
    )
    summary = {k: result[k] for k in ("verdict", "stability", "score", "confidence", "gates")}
    summary["versions"] = rules.versions
    return [
        rec(1, kind="tool_call", tool="m11.verdict"),
        rec(
            2,
            kind="decision",
            tool="transition",
            input=request,
            output={"decision": "m11_verdict", **summary},
            ts="2026-01-01T12:00:05+00:00",
        ),
    ]


@pytest.mark.parametrize("scores", [{}, {"risk": 0.5}, {"demand_pain": 1.0, "competition": 1.0}])
def test_a_real_m11_evaluation_is_reproduced(rules: EngineConfig, scores: dict[str, float]) -> None:
    report = replay_records("run_1", recorded_run(rules, **scores), rules)
    assert report.exit_code == 0
    assert report.steps[0].ok
    assert report.steps[0].diffs == []


def test_the_replayed_verdicts_cover_more_than_one_label(rules: EngineConfig) -> None:
    labels = {
        r["output"]["verdict"]
        for scores in ({}, {"risk": 0.5}, {d: 1.0 for d in DIMENSIONS})
        for r in recorded_run(rules, **scores)
        if r["kind"] == "decision"
    }
    assert len(labels) >= 2  # the test data is not trivially one verdict


def test_a_tampered_recorded_verdict_is_detected(rules: EngineConfig) -> None:
    records = recorded_run(rules)
    records[1]["output"]["verdict"] = (
        "NO-GO" if records[1]["output"]["verdict"] != "NO-GO" else "GO"
    )
    report = replay_records("run_1", records, rules)
    assert report.exit_code == 1
    assert any(d.startswith("verdict:") for d in report.steps[0].diffs)


def test_a_tampered_recorded_input_is_detected(rules: EngineConfig) -> None:
    records = recorded_run(rules)
    tampered = copy.deepcopy(records)
    for s in tampered[1]["input"]["scores"]:
        s["score"] = 0.1
    report = replay_records("run_1", tampered, rules)
    assert report.exit_code == 1


def test_a_tampered_score_value_is_detected_even_when_the_label_survives(
    rules: EngineConfig,
) -> None:
    records = recorded_run(rules)
    records[1]["output"]["score"] = records[1]["output"]["score"] - 0.5
    report = replay_records("run_1", records, rules)
    assert report.exit_code == 1
    assert any(d.startswith("score:") for d in report.steps[0].diffs)


def test_a_run_without_a_recorded_verdict_or_with_truncated_input_cannot_be_replayed(
    rules: EngineConfig,
) -> None:
    assert replay_records("r", [rec(1)], rules).exit_code == 2
    records = recorded_run(rules)
    records[1]["input"] = {"_truncated": True, "preview": "{...}"}
    report = replay_records("r", records, rules)
    assert report.exit_code == 2
    assert "truncated" in (report.problem or "")


def test_an_invalid_recorded_input_is_a_mismatch_not_a_crash(rules: EngineConfig) -> None:
    records = recorded_run(rules)
    records[1]["input"] = {"scores": []}
    assert replay_records("r", records, rules).exit_code == 1


def test_a_rules_version_change_fails_unless_drift_is_allowed(rules: EngineConfig) -> None:
    records = recorded_run(rules)
    records[1]["output"]["versions"] = {"engine": "old"}
    strict = replay_records("r", records, rules)
    assert strict.exit_code == 1
    assert "allow-version-drift" in strict.steps[0].diffs[-1]
    relaxed = replay_records("r", records, rules, allow_version_drift=True)
    assert relaxed.exit_code == 0
    assert relaxed.steps[0].notes


def test_only_the_orchestrators_verdict_steps_count_and_they_are_counted(
    rules: EngineConfig,
) -> None:
    records = recorded_run(rules)
    forged = copy.deepcopy(records[1])
    forged["service"] = "tools"
    forged["output"]["verdict"] = "GO"
    forged["input"] = engine_input(**{d: 0.1 for d in DIMENSIONS})
    report = replay_records("r", [*records, forged], rules)
    assert report.exit_code == 0
    assert report.m11_steps == 1  # the forged one is not an M11 step
    assert "1 recorded M11 verdict step" in format_report(report)
    only_forged = replay_records("r", [forged], rules)
    assert only_forged.exit_code == 2


def test_an_incomplete_trace_cannot_be_replayed(rules: EngineConfig) -> None:
    report = replay_records("r", recorded_run(rules), rules, complete=False)
    assert report.exit_code == 2
    assert "truncated" in (report.problem or "")


def test_a_realistic_input_over_4kb_is_replayable(rules: EngineConfig) -> None:
    records = recorded_run(rules)
    for score in records[1]["input"]["scores"]:
        score["evidence_ids"] = [f"card_{i:08x}#c{i}" for i in range(60)]
    assert len(str(records[1]["input"])) > 4096
    result = evaluate_with_whatif(EngineInput.model_validate(records[1]["input"]), rules)
    records[1]["output"]["verdict"] = result.verdict
    records[1]["output"]["score"] = result.score
    records[1]["output"]["confidence"] = result.confidence
    records[1]["output"]["stability"] = result.stability
    records[1]["output"]["gates"] = result.gates
    assert replay_records("r", records, rules).exit_code == 0


def test_the_url_loader_validates_the_run_id_and_reports_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from svc_agentops.repositories.files import UnsafeRunId

    with pytest.raises(UnsafeRunId):
        load_from_url("../x", "http://agentops", None)

    class Response:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict[str, Any]:
            return {"records": [{"a": 1}, "junk"], "truncated": True}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response())
    records, complete = load_from_url("run_1", "http://agentops", "t")
    assert records == [{"a": 1}]
    assert complete is False


def test_hitting_the_record_limit_is_exit_2(
    rules: EngineConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentops.replay as replay_module

    files = JsonlTraceFiles(tmp_path)
    files.append("run_1", [(f"id{i}", r) for i, r in enumerate(recorded_run(rules))])
    monkeypatch.setattr(replay_module, "MAX_RECORDS", 1)
    argv = ["run_1", "--trace-dir", str(tmp_path), "--config-dir", str(CONFIG_DIR)]
    assert main(argv) == 2


def test_the_command_line_exits_zero_on_a_match_and_non_zero_on_a_tampered_trace(
    rules: EngineConfig, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = JsonlTraceFiles(tmp_path)
    good = recorded_run(rules)
    files.append("run_1", [(f"id{i}", r) for i, r in enumerate(good)])
    argv = ["run_1", "--trace-dir", str(tmp_path), "--config-dir", str(CONFIG_DIR)]
    assert main(argv) == 0
    assert "same verdict" in capsys.readouterr().out

    bad = copy.deepcopy(good)
    bad[1]["output"]["verdict"] = "INSUFFICIENT_EVIDENCE"
    bad[1]["output"]["confidence"] = 0.01
    files.append("run_2", [(f"id{i}", {**r, "run_id": "run_2"}) for i, r in enumerate(bad)])
    assert main(["run_2", *argv[1:]]) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_the_command_line_reports_unusable_input_with_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["../evil", "--trace-dir", str(tmp_path), "--config-dir", str(CONFIG_DIR)]) == 2
    assert main(["run_9", "--trace-dir", str(tmp_path), "--config-dir", str(CONFIG_DIR)]) == 2
    assert main(["run_9", "--trace-dir", str(tmp_path), "--config-dir", str(tmp_path)]) == 2
    capsys.readouterr()
