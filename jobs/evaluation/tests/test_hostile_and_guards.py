"""Hostile gateway replies, response caps, resume tampering, baseline guards, total budget."""

import asyncio
import itertools
import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
from evaluation import progress, runner
from evaluation.gateway import MAX_TRACE_BYTES, Gateway, GatewayError
from evaluation.labelled import LabelledItem
from evaluation.run import _parser, main
from evaluation.runner import RunConfig, run_all, run_item, sign_in, slot_email
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
ENV = {"GATEWAY_URL": "http://gw", "EVAL_USER": "ev@example.com", "EVAL_PASSWORD": "placeholder"}
CFG = RunConfig(poll_s=0, item_timeout_s=0.3, item_retries=2)
_RUNS = itertools.count(100)


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


def item(n: str = "L1", **over: Any) -> LabelledItem:
    base: dict[str, Any] = {"id": n, "idea_text": "idea", "profile": PROFILE, "human_verdict": "GO"}
    return LabelledItem(**{**base, **over})


async def one(fake: FakeGateway, it: LabelledItem | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url="http://gw", transport=fake.transport()) as client:
        gw = Gateway(client, sleep=no_sleep)
        session = await sign_in(gw, "e+w0@example.com", "pw")
        return await run_item(gw, session, it or item(), CFG, sleep=no_sleep)


# ---- hostile replies: an error row or a failed check, never a crash ---------------------------
CARD = json.dumps(good_card())


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ({"view_raw": b"[1, 2]"}, "malformed_run"),
        ({"view_raw": b"not json"}, "http_200"),
        ({"view_raw": b"\xff\xfe\x00"}, "http_200"),
        ({"view_raw": b'{"state": ["succeeded"]}'}, "malformed_run"),
        ({"huge_view": True}, "response_too_large"),
        ({"bad_run_id": "../../admin"}, "malformed_run"),
        ({"bad_run_id": "run\n1"}, "malformed_run"),
        ({"bad_run_id": "r" * 65}, "malformed_run"),
        ({"bad_run_id": "ré"}, "malformed_run"),
        ({"bad_run_id": 7}, "malformed_run"),
        ({"raise_on_poll": httpx.DecodingError("bad gzip")}, "bad_response"),
        ({"raise_on_poll": httpx.TooManyRedirects("loop")}, "bad_response"),
    ],
)
async def test_hostile_replies_become_error_rows(spec: dict[str, Any], code: str) -> None:
    row = await one(FakeGateway({"eval L1": spec}))
    assert row["status"] == "error" and row["error"] == code and row["system"] is None


async def test_nan_and_inf_never_pass_as_numbers() -> None:
    raw = (
        b'{"state":"succeeded","spent":{"cost_usd":NaN,"tokens":Infinity},'
        b'"budget":{"max_cost_usd":NaN},"result":' + CARD.encode() + b"}"
    )
    row = await one(FakeGateway({"eval L1": {"view_raw": raw}}))
    assert row["status"] == "ok"
    assert row["cost_usd"] == 0.01  # the NaN spend was ignored, the trace cost used
    assert row["behaviour"]["budget_kept"] is True  # the NaN limit fell back to the item limit


async def test_a_string_cost_limit_does_not_crash_the_budget_check() -> None:
    raw = (
        b'{"state":"succeeded","spent":{"cost_usd":"lots"},"budget":{"max_cost_usd":"x"},'
        b'"result":' + CARD.encode() + b"}"
    )
    row = await one(FakeGateway({"eval L1": {"view_raw": raw}}))
    assert row["status"] == "ok"


async def test_unhashable_and_odd_evidence_ids_fail_the_check_not_the_run() -> None:
    card = good_card()
    card["scores"][0]["evidence_ids"] = [{"a": 1}, ["x"], None]
    card["claims"] = [{"claim_id": "c", "text": "t", "evidence_ids": [[1]]}]
    row = await one(FakeGateway({"eval L1": {"card": card}}))
    assert row["status"] == "ok" and row["behaviour"]["every_score_cites_valid_evidence"] is False


async def test_hostile_trace_values_do_not_crash() -> None:
    raw = (
        b'{"run_id":"run_L1_1","records":['
        b'{"step_id":"x","agent":5,"kind":"tool_call","tool":"m6.search","ts":"nonsense"},'
        b'{"agent":["a"],"latency_ms":Infinity,"cost_usd":NaN,"ts":"9999-99-99"},'
        b'{"kind":"decision","name":"m11_verdict","output":"text"}]}'
    )
    row = await one(FakeGateway({"eval L1": {"trace_raw": raw}}))
    assert row["status"] == "ok" and row["behaviour"]["search_kb_before_scoring"] is False


async def test_an_unexpected_exception_is_an_error_row_without_its_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise ZeroDivisionError("the idea text: SECRET")

    monkeypatch.setattr(runner, "run_checks", boom)
    row = await one(FakeGateway())
    assert row["error"] == "unexpected_ZeroDivisionError" and "SECRET" not in json.dumps(row)
    assert row["attempts"] == 1  # not retried


async def test_trace_over_the_cap_counts_as_no_trace() -> None:
    row = await one(FakeGateway({"eval L1": {"trace": "huge"}}))
    assert row["status"] == "ok" and row["trace_available"] is False


# ---- the gateway client -------------------------------------------------------------------------
def client_for(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://gw", transport=httpx.MockTransport(handler))


async def test_content_length_over_the_cap_is_refused_before_reading() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}", headers={"content-length": str(3 << 20)})

    async with client_for(handler) as client:
        with pytest.raises(GatewayError, match="too large"):
            await Gateway(client, sleep=no_sleep).request("GET", "/x")


async def test_a_streamed_body_over_the_cap_is_aborted() -> None:
    async def body() -> Any:
        for _ in range(40):
            yield b"x" * (100 * 1024)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    async with client_for(handler) as client:
        with pytest.raises(GatewayError, match="too large"):
            await Gateway(client, sleep=no_sleep).request("GET", "/x")


async def test_the_cap_is_per_call_and_the_trace_cap_is_larger() -> None:
    payload = b'{"a":"' + b"x" * (3 << 20) + b'"}'

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async with client_for(handler) as client:
        gw = Gateway(client, sleep=no_sleep)
        with pytest.raises(GatewayError, match="too large"):
            await gw.request("GET", "/x")
        assert (await gw.request("GET", "/x", max_bytes=MAX_TRACE_BYTES))["a"].startswith("xx")
        with pytest.raises(GatewayError, match="too large"):
            await gw.request("GET", "/x", max_bytes=1024)


async def test_a_body_exactly_at_the_cap_is_accepted() -> None:
    body = b'{"a":"' + b"x" * 90 + b'"}'

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    async with client_for(handler) as client:
        out = await Gateway(client, sleep=no_sleep).request("GET", "/x", max_bytes=len(body))
        assert len(out["a"]) == 90


async def test_error_bodies_are_not_read_or_quoted() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(422, content=b"idea text SECRET " * 10)

    async with client_for(handler) as client:
        with pytest.raises(GatewayError) as caught:
            await Gateway(client, sleep=no_sleep).request("POST", "/x", body={})
    assert "SECRET" not in str(caught.value) and caught.value.status == 422


# ---- eval user accounts ---------------------------------------------------------------------
async def test_an_existing_account_with_another_password_is_reported_not_taken_over() -> None:
    fake = FakeGateway()
    fake.taken.add("e+w0@example.com")  # exists; login says 401 for this password
    async with httpx.AsyncClient(base_url="http://gw", transport=fake.transport()) as client:
        with pytest.raises(GatewayError, match="password was rejected"):
            await sign_in(Gateway(client, sleep=no_sleep), "e+w0@example.com", "pw")
    assert fake.calls.count(("POST", "/auth/login")) == 1  # no second login attempt


def test_default_concurrency_and_poll_interval_respect_the_ip_limit() -> None:
    args = _parser().parse_args([])
    assert args.concurrency == 4 and args.poll_interval >= 4.0
    assert args.concurrency * 60 / args.poll_interval <= 100


# ---- total budget -----------------------------------------------------------------------------
async def test_total_cost_stops_the_queue_and_reports_the_rest() -> None:
    items = [item(f"L{i}") for i in range(5)]
    fake = FakeGateway()
    async with httpx.AsyncClient(base_url="http://gw", transport=fake.transport()) as client:
        gw = Gateway(client, sleep=no_sleep)
        sessions = [await sign_in(gw, slot_email("e@example.com", 0), "pw")]
        rows = await run_all(
            items, sessions, gw, RunConfig(poll_s=0), sleep=no_sleep, max_total_cost_usd=0.015
        )
    assert [r["id"] for r in rows] == [i.id for i in items]
    assert [r["status"] for r in rows] == ["ok", "ok", "error", "error", "error"]
    assert {r["error"] for r in rows[2:]} == {"total_budget_stop"}
    assert len(fake.started) == 2  # nothing was started for the skipped items


# ---- resume tampering -------------------------------------------------------------------------
def labelled(tmp_path: Path, humans: list[str], **extra: Any) -> Path:
    path = tmp_path / "labelled_set.jsonl"
    rows = [
        {"id": f"L{i}", "idea_text": f"idea {i}", "profile": PROFILE, "human_verdict": h}
        | extra.get(f"L{i}", {})
        for i, h in enumerate(humans)
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def invoke(tmp_path: Path, fake: FakeGateway, *args: str, env: dict[str, str] | None = None) -> int:
    minute = next(_RUNS) % 59
    stamps = iter(datetime(2026, 11, 28, 3, minute, s, tzinfo=UTC) for s in range(59))
    return main(
        ["--out", str(tmp_path / "out"), "--poll-interval", "0", *args],
        env=ENV if env is None else env,
        transport=fake.transport(),
        sleep=no_sleep,
        now=lambda: next(stamps),
    )


def progress_lines(tmp_path: Path) -> list[str]:
    return (tmp_path / "out" / "progress.jsonl").read_text().splitlines()


def test_resume_refuses_a_changed_labelled_set(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO"])
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path)) == 0
    labelled(tmp_path, ["GO", "NO-GO"])  # a human relabelled L1 in between
    fake = FakeGateway()
    assert invoke(tmp_path, fake, "--labelled", str(path), "--resume") == 2
    assert fake.calls == []  # refused before any spend


def test_resume_refuses_another_image_tag(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO"])
    assert (
        invoke(tmp_path, FakeGateway(), "--labelled", str(path), env=ENV | {"EVAL_IMAGE_TAG": "a"})
        == 0
    )
    fake = FakeGateway()
    code = invoke(
        tmp_path, fake, "--labelled", str(path), "--resume", env=ENV | {"EVAL_IMAGE_TAG": "b"}
    )
    assert code == 2 and fake.calls == []


def test_resume_refuses_a_file_without_a_header(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO"])
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "progress.jsonl").write_text(
        json.dumps({"id": "L0", "status": "ok"}) + "\n"
    )
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path), "--resume") == 2


def test_resume_without_any_progress_file_is_refused(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO"])
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path), "--resume") == 2


def test_tampered_rows_are_dropped_and_human_labels_come_from_the_current_set(
    tmp_path: Path,
) -> None:
    path = labelled(tmp_path, ["GO", "NO-GO", "GO", "GO"])
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path)) == 0
    header, *rows = [json.loads(line) for line in progress_lines(tmp_path)]
    by_id = {r["id"]: r for r in rows}
    by_id["L0"]["human"] = "NO-GO"  # forged label
    by_id["L0"]["poisoned"] = True
    by_id["L1"]["system"] = "MAYBE"  # not a verdict
    by_id["L2"]["behaviour"] = {"budget_kept": "yes"}  # wrong shape
    by_id["L3"]["score"] = "NaN"  # wrong type
    extra = {**by_id["L0"], "id": "L99"}  # not in the labelled set
    lines = [header, *by_id.values(), extra]
    (tmp_path / "out" / "progress.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    fake = FakeGateway()
    assert invoke(tmp_path, fake, "--labelled", str(path), "--resume") == 0
    assert sorted(t.split()[-1] for t in fake.validate_attempts) == ["L1", "L2", "L3"]
    results = sorted((tmp_path / "out").glob("2026*.json"))[-1]
    final = {r["id"]: r for r in json.loads(results.read_text())["rows"]}
    assert final["L0"]["human"] == "GO" and final["L0"]["poisoned"] is False
    assert [r["human"] for r in final.values()] == ["GO", "NO-GO", "GO", "GO"]
    assert "L99" not in final


def test_a_torn_last_line_is_ignored(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO"])
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path)) == 0
    log = tmp_path / "out" / "progress.jsonl"
    log.write_text(log.read_text() + '{"id": "L1", "stat')
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path), "--resume") == 0


# ---- baseline guards ----------------------------------------------------------------------------
POISON = {"poisoned": True, "injection_forces": "NO-GO"}


@pytest.mark.parametrize("flags", [["--limit", "1"], ["--resume"]])
def test_baseline_is_refused_before_any_spend_for_partial_runs(
    tmp_path: Path, flags: list[str]
) -> None:
    path = labelled(tmp_path, ["GO", "GO"], L0=POISON)
    fake = FakeGateway()
    assert invoke(tmp_path, fake, "--labelled", str(path), "--write-baseline", *flags) == 2
    assert fake.calls == [] and not (tmp_path / "out" / "baseline.json").exists()


def test_baseline_is_refused_when_a_check_applied_to_no_item(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO"])  # nothing poisoned
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path), "--write-baseline") == 2
    assert not (tmp_path / "out" / "baseline.json").exists()
    assert list((tmp_path / "out").glob("2026*.json"))  # the run itself is still saved


def test_baseline_is_refused_without_traces(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO"], L0=POISON)
    fake = FakeGateway({"eval L1": {"trace": "missing"}})
    assert invoke(tmp_path, fake, "--labelled", str(path), "--write-baseline") == 2
    assert not (tmp_path / "out" / "baseline.json").exists()


def test_baseline_is_refused_when_a_behaviour_check_fails(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO"], L0=POISON)
    fake = FakeGateway({"eval L1": {"spent_final": {"cost_usd": 9.0}}})
    assert invoke(tmp_path, fake, "--labelled", str(path), "--write-baseline") == 2
    assert not (tmp_path / "out" / "baseline.json").exists()


def test_a_baseline_replaces_the_old_one_atomically(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO", "GO"], L0=POISON)
    out = tmp_path / "out"
    out.mkdir()
    (out / "baseline.json").write_text("old")
    assert invoke(tmp_path, FakeGateway(), "--labelled", str(path), "--write-baseline") == 0
    assert json.loads((out / "baseline.json").read_text())["n"] == 2
    assert not [p for p in out.iterdir() if p.name.startswith(".")]  # no temp files left


def test_an_existing_result_file_is_never_overwritten(tmp_path: Path) -> None:
    path = labelled(tmp_path, ["GO"])
    stamps = [datetime(2026, 11, 28, 4, 0, s, tzinfo=UTC) for s in range(59)]
    for expected in (0, 1):
        it = iter(stamps)
        code = main(
            ["--labelled", str(path), "--out", str(tmp_path / "out"), "--poll-interval", "0"],
            env=ENV,
            transport=FakeGateway().transport(),
            sleep=no_sleep,
            now=partial(next, it),
        )
        assert code == expected
    assert len(list((tmp_path / "out").glob("2026*.json"))) == 1


# ---- atomic file helpers -----------------------------------------------------------------------
def test_atomic_write_exclusive_and_replace(tmp_path: Path) -> None:
    target = tmp_path / "r.json"
    progress.atomic_write(target, "one", exclusive=True)
    with pytest.raises(FileExistsError):
        progress.atomic_write(target, "two", exclusive=True)
    assert target.read_text() == "one"
    progress.atomic_write(target, "three")
    assert target.read_text() == "three"
    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]
