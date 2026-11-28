import asyncio
import itertools
import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from evaluation.cli import main as cli_main
from evaluation.gate import check_results
from evaluation.report import build_results
from evaluation.run import main
from fake_gateway import FakeGateway, good_card

PROFILE = {
    "skills": ["python"],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 2,
    "compute": "laptop",
    "goal": "portfolio",
    "interests": [],
}
ENV = {
    "GATEWAY_URL": "http://gw",
    "EVAL_USER": "ev@example.com",
    "EVAL_PASSWORD": "s3cret-pw-x",
}


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)  # yield, so timeouts can fire


def labelled(tmp_path: Path, humans: list[str], **extra: Any) -> Path:
    path = tmp_path / "labelled_set.jsonl"
    rows = [
        {
            "id": f"L{i}",
            "idea_text": f"idea {i}",
            "profile": PROFILE,
            "human_verdict": h,
            **extra.get(f"L{i}", {}),
        }
        for i, h in enumerate(humans)
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


_RUNS = itertools.count()


def invoke(tmp_path: Path, fake: FakeGateway, *args: str, env: dict[str, str] | None = None) -> int:
    minute = next(_RUNS) % 59  # every call gets its own result file name
    clock = iter(datetime(2026, 11, 28, 2, minute, s, tzinfo=UTC) for s in range(59))
    return main(
        ["--out", str(tmp_path / "out"), "--poll-interval", "0", *args],
        env=ENV if env is None else env,
        transport=fake.transport(),
        sleep=no_sleep,
        now=lambda: next(clock),
    )


def results(tmp_path: Path) -> dict[str, Any]:
    files = sorted(p for p in (tmp_path / "out").glob("2026*.json"))
    assert files
    doc: dict[str, Any] = json.loads(files[-1].read_text())
    return doc


def test_end_to_end_metrics_and_final_json_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGateway(
        {
            "eval L0": {"verdict": "GO"},
            "eval L1": {"verdict": "GO"},
            "eval L2": {"verdict": "PIVOT"},
            "eval L3": {"verdict": "NO-GO"},
        }
    )
    path = labelled(tmp_path, ["GO", "NO-GO", "PIVOT", "NO-GO"])
    assert invoke(tmp_path, fake, "--labelled", str(path)) == 0
    doc = results(tmp_path)
    assert doc["n"] == 4 and doc["agreement"] == 0.75 and doc["false_go"] == 1
    assert doc["confusion_matrix"][2][0] == 1 and doc["n_errors"] == 0
    assert [r["id"] for r in doc["rows"]] == ["L0", "L1", "L2", "L3"]
    assert doc["behaviour"]["search_kb_before_scoring"] == 1.0
    assert doc["cost_per_verdict_usd"] == 0.01 and doc["agent_latency_ms"]["scorer_demand"] == 400
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(last)["agreement"] == 0.75
    assert not (tmp_path / "out" / "baseline.json").exists()  # never automatic


def test_output_is_deterministic_for_same_inputs(tmp_path: Path) -> None:
    """Fake wall clock and fake timestamps: the two result files must be byte-identical."""
    path = labelled(tmp_path, ["GO", "PIVOT", "NO-GO", "GO", "PIVOT"])
    texts = []
    for name in ("a", "b"):
        out = tmp_path / name
        fake = FakeGateway({f"eval L{i}": {"polls": (5 - i) % 3} for i in range(5)})
        stamps = iter(datetime(2026, 11, 28, 2, 0, s, tzinfo=UTC) for s in range(59))
        code = main(
            [
                "--labelled",
                str(path),
                "--out",
                str(out),
                "--poll-interval",
                "0",
                "--concurrency",
                "2",
            ],
            env=ENV,
            transport=fake.transport(),
            sleep=no_sleep,
            now=partial(next, stamps),
            clock=lambda: 0.0,
        )
        assert code == 0
        texts.append(next(out.glob("2026*.json")).read_text())
    assert texts[0] == texts[1]
    assert json.loads(texts[0])["latency_s"] == {"mean": 0.0, "p95": 0.0}


def test_errors_are_counted_not_dropped_and_ie_only_when_returned(tmp_path: Path) -> None:
    fake = FakeGateway(
        {"eval L1": {"state": "failed"}, "eval L2": {"verdict": "INSUFFICIENT_EVIDENCE"}}
    )
    path = labelled(tmp_path, ["GO", "GO", "INSUFFICIENT_EVIDENCE", "GO", "GO"])
    code = invoke(tmp_path, fake, "--labelled", str(path), "--max-error-rate", "0.5")
    doc = results(tmp_path)
    assert code == 0 and doc["n_total"] == 5 and doc["n"] == 4 and doc["n_errors"] == 1
    assert doc["errors"] == {"run_failed": 1} and doc["error_rate"] == 0.2
    assert doc["agreement_incl_errors"] == 0.8 and len(doc["rows"]) == 5
    assert doc["confusion_matrix"][3][3] == 1 and sum(doc["confusion_matrix"][3]) == 1


def test_too_many_errors_exit_1_but_results_are_written(tmp_path: Path) -> None:
    fake = FakeGateway({"eval L0": {"state": "failed"}, "eval L1": {"state": "failed"}})
    path = labelled(tmp_path, ["GO", "GO"])
    assert invoke(tmp_path, fake, "--labelled", str(path)) == 1
    doc = results(tmp_path)
    assert doc["n"] == 0 and doc["n_errors"] == 2 and doc["agreement"] == 0.0


def test_limit_takes_the_first_n(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO"] * 5)
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path), "--limit", "2") == 0
    assert [r["id"] for r in results(tmp_path)["rows"]] == ["L0", "L1"]


def test_resume_skips_completed_ids_and_reruns_errors(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO", "GO"])
    first = FakeGateway({"eval L1": {"state": "failed"}})
    assert invoke(tmp_path, first, "--labelled", str(path), "--max-error-rate", "1") == 0
    second = FakeGateway()
    assert invoke(tmp_path, second, "--labelled", str(path), "--resume") == 0
    assert set(second.validate_attempts) == {"eval L1"}
    doc = results(tmp_path)
    assert doc["resumed"] == 2 and doc["n"] == 3 and doc["n_errors"] == 0


def test_without_resume_old_progress_is_discarded(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO"])
    invoke(tmp_path, FakeGateway(), "--labelled", str(path))
    again = FakeGateway()
    invoke(tmp_path, again, "--labelled", str(path))
    assert len(again.validate_attempts) == 2


def test_missing_labelled_set_exits_2_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(tmp_path / "labelled_set.jsonl")) == 2
    assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize("drop", ["EVAL_USER", "EVAL_PASSWORD", "GATEWAY_URL"])
def test_missing_config_exits_2(tmp_path: Path, drop: str) -> None:
    path = labelled(tmp_path, ["GO"])
    env = {k: v for k, v in ENV.items() if k != drop}
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path), env=env) == 2


def test_refused_login_exits_3(tmp_path: Path) -> None:
    fake = FakeGateway()
    fake.login_status = 403
    assert invoke(tmp_path, fake, "--labelled", str(labelled(tmp_path, ["GO"]))) == 3


POISON = {"poisoned": True, "injection_forces": "NO-GO", "injection_canary": "OBEY ME"}


def test_write_baseline_only_when_asked_and_clean(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO"], L0=POISON)
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path), "--write-baseline") == 0
    base = json.loads((tmp_path / "out" / "baseline.json").read_text())
    assert check_results(base, base) == []  # the gate accepts a run against itself
    dirty = tmp_path / "dirty"
    fake = FakeGateway({"eval L0": {"state": "failed"}})
    main(
        [
            "--labelled",
            str(path),
            "--out",
            str(dirty),
            "--poll-interval",
            "0",
            "--write-baseline",
            "--max-error-rate",
            "1",
        ],
        env=ENV,
        transport=fake.transport(),
        sleep=no_sleep,
    )
    assert not (dirty / "baseline.json").exists()


def test_result_feeds_the_existing_gate(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO"], L0=POISON)
    invoke(tmp_path, FakeGateway(), "--labelled", str(path), "--write-baseline")
    out = tmp_path / "out"
    current = next(out.glob("2026*.json"))
    assert cli_main(["gate", str(out / "baseline.json"), str(current)]) == 0


def test_missing_trace_fails_trace_behaviour(tmp_path: Path) -> None:
    fake = FakeGateway({"eval L0": {"trace": "missing"}})
    assert invoke(tmp_path, fake, "--labelled", str(labelled(tmp_path, ["GO"]))) == 0
    doc = results(tmp_path)
    assert doc["behaviour"]["search_kb_before_scoring"] == 0.0 and doc["traces_available"] == 0
    assert doc["behaviour"]["budget_kept"] == 0.0 and doc["rows"][0]["status"] == "ok"


def test_traces_come_only_through_the_gateway_with_the_users_token(tmp_path: Path) -> None:
    fake = FakeGateway()
    assert invoke(tmp_path, fake, "--labelled", str(labelled(tmp_path, ["GO", "GO"]))) == 0
    assert len(fake.trace_calls) == 2 and set(fake.trace_auth) == {"Bearer tok"}
    assert all("x-internal-token" not in k for k in ENV) and "INTERNAL_TOKEN" not in ENV
    doc = results(tmp_path)
    assert doc["traces_available"] == 2 and doc["behaviour"]["search_kb_before_scoring"] == 1.0


def test_report_with_no_rows_for_a_check_is_vacuously_complete() -> None:
    row = {
        "status": "ok",
        "human": "GO",
        "system": "GO",
        "latency_s": 1.0,
        "cost_usd": 0.0,
        "behaviour": {"injection_resisted": None, "budget_kept": True},
    }
    doc = build_results([row], {})
    assert (
        doc["behaviour"]["injection_resisted"] == 1.0
        and doc["behaviour_applicable"]["injection_resisted"] == 0
    )
    assert good_card()  # fixture sanity
