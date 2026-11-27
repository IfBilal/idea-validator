"""Security-review follow-ups: orphans, idempotency, stream caps, stale state, scrubbing, limits."""

from typing import Any

import pytest
from test_gateway_runs_api import (
    ALICE,
    BOB,
    CARD,
    LEAKS,
    SECRET,
    Env,
    build,
    discover,
    error_code,
    events_for,
    frames,
    seed_run,
    validate,
)

from svc_gateway.security import issue_token
from svc_gateway.testing import ScriptedBus, make_event

RUN = "run_000000000001"
# C1 control, zero-width space, bidi override and isolate, line and paragraph separator
HOSTILE = (0x85, 0x200B, 0x202E, 0x2028, 0x2029, 0x2066)


# =========================================================================================
# 1. orphans and idempotency
# =========================================================================================
def test_when_the_owner_record_cannot_be_written_the_run_is_cancelled_and_nothing_stays_running() -> (
    None
):
    e = build()
    with e.client:
        e.m2.fail_put_run = True
        response = validate(e)
        assert response.status_code == 502
        no_leak = response.text
        assert all(leak not in no_leak for leak in LEAKS)
        started = [c for c in e.m3.calls if c[0] == "POST" and c[1] == "/runs"]
        assert len(started) == 1
        assert len(e.m3.cancelled) == 1  # the run M3 started is stopped again
        assert e.m2.runs == {}


def test_a_retry_after_a_failed_owner_write_reuses_the_idea_instead_of_adding_a_draft() -> None:
    e = build()
    with e.client:
        e.m2.fail_put_run = True
        assert validate(e).status_code == 502
        e.m2.fail_put_run = False
        again = validate(e)
        assert again.status_code == 202
        assert len(e.m2.ideas) == 1
        assert e.m2.runs[again.json()["run_id"]]["user_id"] == ALICE


def test_when_m3_cannot_start_the_run_a_retry_reuses_the_idea_and_version() -> None:
    e = build()
    with e.client:
        e.m3.fail = 500
        assert validate(e).status_code == 502
        assert len(e.m2.ideas) == 1
        assert e.m2.runs == {}
        e.m3.fail = None
        again = validate(e)
        assert again.status_code == 202
        assert len(e.m2.ideas) == 1  # the same idea, not a second draft
        assert again.json()["version"] == 1
        assert len(e.m2.versions[again.json()["idea_id"]]) == 1


def test_a_failed_start_of_a_new_version_does_not_pile_up_versions_on_retry() -> None:
    e = build()
    with e.client:
        idea = e.m2.add_idea(ALICE, text="old")
        e.m3.fail = 500
        assert validate(e, idea_id=idea, idea_text="new text", title=None).status_code == 502
        assert validate(e, idea_id=idea, idea_text="new text", title=None).status_code == 502
        e.m3.fail = None
        assert validate(e, idea_id=idea, idea_text="new text", title=None).status_code == 202
        assert [v["text"] for v in e.m2.versions[idea]] == ["old", "new text"]


def test_a_double_submit_returns_the_same_run_and_starts_it_once() -> None:
    e = build()
    with e.client:
        first, second = validate(e), validate(e)
        assert first.status_code == second.status_code == 202
        assert first.json() == second.json()
        assert len(e.m3.posts("/runs")) == 1
        assert len(e.m2.ideas) == 1


def test_the_idempotency_key_header_makes_retries_reuse_the_run() -> None:
    e = build()
    with e.client:
        headers = {**e.headers(), "Idempotency-Key": "submit-123"}
        body = {"idea_text": "text", "title": "t"}
        a = e.client.post("/validate", json=body, headers=headers).json()
        b = e.client.post("/validate", json=body, headers=headers).json()
        assert a == b
        other = e.client.post(
            "/validate", json=body, headers={**e.headers(), "Idempotency-Key": "submit-456"}
        ).json()
        assert other["run_id"] != a["run_id"]


def test_the_same_key_with_a_different_body_is_a_different_request() -> None:
    e = build()
    with e.client:
        headers = {**e.headers(), "Idempotency-Key": "k1"}
        a = e.client.post("/discover", json={"count": 3}, headers=headers).json()
        b = e.client.post("/discover", json={"count": 4}, headers=headers).json()
        assert a["run_id"] != b["run_id"]


def test_idempotency_is_per_user() -> None:
    e = build()
    with e.client:
        a = validate(e, user=ALICE).json()
        b = validate(e, user=BOB).json()
        assert a["run_id"] != b["run_id"]
        assert a["idea_id"] != b["idea_id"]


def test_discover_double_submit_is_deduplicated_too() -> None:
    e = build()
    with e.client:
        a = discover(e, count=3).json()
        b = discover(e, count=3).json()
        assert a == b
        assert len(e.m3.posts("/runs")) == 1


def test_a_run_that_already_failed_is_started_again_not_reused() -> None:
    e = build()
    with e.client:
        first = validate(e).json()
        e.m2.runs[first["run_id"]]["state"] = "failed"
        second = validate(e).json()
        assert second["run_id"] != first["run_id"]
        assert second["idea_id"] == first["idea_id"]


def test_a_request_that_is_still_being_processed_is_a_409() -> None:
    e = build()
    with e.client:
        body = {"idea_text": "x", "title": "t"}
        first = e.client.post("/validate", json=body, headers=e.headers())
        assert first.status_code == 202
        for key in list(e.store.values):  # simulate the other request still holding the claim
            e.store.values[key] = {"pending": True}
        again = e.client.post("/validate", json=body, headers=e.headers())
        assert again.status_code == 409
        assert error_code(again) == "CONFLICT"


@pytest.mark.parametrize("key", ["", "a" * 65, "has space", "semi;colon"])
def test_a_bad_idempotency_key_is_422(key: str) -> None:
    e = build()
    with e.client:
        headers = {**e.headers(), "Idempotency-Key": key}
        response = e.client.post("/discover", json={}, headers=headers)
        assert response.status_code == 422
        assert e.m3.calls == []


def test_a_64_character_idempotency_key_is_fine() -> None:
    e = build()
    with e.client:
        headers = {**e.headers(), "Idempotency-Key": "k" * 64}
        assert e.client.post("/discover", json={}, headers=headers).status_code == 202


def test_a_redis_outage_in_the_idempotency_store_fails_closed_with_503() -> None:
    e = build()
    with e.client:
        e.store.broken = True
        response = validate(e)
        assert response.status_code == 503
        assert e.m3.calls == [] and e.m2.calls == []


def test_a_stale_idempotency_entry_for_an_unknown_run_is_ignored() -> None:
    e = build()
    with e.client:
        first = validate(e).json()
        del e.m2.runs[first["run_id"]]  # M2 no longer has the record
        second = validate(e).json()
        assert second["run_id"] != first["run_id"]


# =========================================================================================
# 2. stream exhaustion
# =========================================================================================
def test_streams_are_capped_per_user_and_released_when_the_stream_ends() -> None:
    e = build(ScriptedBus(events_for(RUN, 1)), sse_streams_per_user=2)
    with e.client:
        seed_run(e)
        assert e.gate.acquire_sync(ALICE, 2) and e.gate.acquire_sync(ALICE, 2)  # two open streams
        blocked = e.client.get(f"/runs/{RUN}/events", headers=e.headers())
        assert blocked.status_code == 429
        assert error_code(blocked) == "RATE_LIMITED"
        assert int(blocked.headers["Retry-After"]) >= 1
        assert e.bus.channels == []  # it never subscribed
        e.gate.release_sync(ALICE)
        assert e.client.get(f"/runs/{RUN}/events", headers=e.headers()).status_code == 200
        assert e.gate.count(ALICE) == 1  # the finished stream gave its slot back


def test_the_stream_cap_is_per_user() -> None:
    e = build(ScriptedBus(events_for("run_000000000002", 1)), sse_streams_per_user=1)
    with e.client:
        seed_run(e)
        seed_run(e, user=BOB, run_id="run_000000000002")
        assert e.gate.acquire_sync(ALICE, 1)
        ok = e.client.get("/runs/run_000000000002/events", headers=e.headers(BOB))
        assert ok.status_code == 200


def test_a_slot_is_released_even_if_the_stream_fails() -> None:
    class Boom(ScriptedBus):
        async def subscribe(self, run_id: str) -> Any:
            raise OSError("pool exhausted")

    e = build(Boom())
    with e.client:
        seed_run(e)
        response = e.client.get(f"/runs/{RUN}/events", headers=e.headers())
        assert response.status_code == 503  # fails closed: no silent 200 with an empty stream
        assert e.gate.count(ALICE) == 0  # and the slot is not leaked


def test_a_gate_outage_fails_closed() -> None:
    e = build()
    with e.client:
        seed_run(e)
        e.gate.broken = True
        assert e.client.get(f"/runs/{RUN}/events", headers=e.headers()).status_code == 503


def test_the_stream_lifetime_never_outlives_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import svc_gateway.services.runs as runs_module

    seen: dict[str, Any] = {}

    async def spy(**kwargs: Any) -> Any:
        seen.update(kwargs)
        yield "retry: 3000\n\n"

    monkeypatch.setattr(runs_module, "stream_run_events", spy)
    e = build(sse_max_s=900)
    with e.client:
        seed_run(e)
        short = {"Authorization": f"Bearer {issue_token(SECRET, ALICE, 40)}"}
        assert e.client.get(f"/runs/{RUN}/events", headers=short).status_code == 200
        assert 0 < seen["max_s"] <= 40
        assert e.client.get(f"/runs/{RUN}/events", headers=e.headers()).status_code == 200
        assert seen["max_s"] == 900  # the token lasts longer than the cap


def test_the_default_stream_lifetime_is_15_minutes() -> None:
    from svc_gateway.config import Settings

    assert Settings().sse_max_s == 900
    assert Settings().sse_streams_per_user == 3


# =========================================================================================
# 3. numeric answers that overflow
# =========================================================================================
@pytest.mark.parametrize("answer", ["1" + "0" * 400, "-" + "9" * 400, "9" * 20, "1e309"])
def test_huge_numbers_are_a_422_never_a_500(answer: str) -> None:
    e = build()
    with e.client:
        seed_run(e)
        raw = f'{{"question_id": "q1", "answer": {answer}}}'
        headers = {**e.headers(), "Content-Type": "application/json"}
        response = e.client.post(f"/runs/{RUN}/answer", content=raw, headers=headers)
        assert response.status_code == 422
        assert e.m3.posts("/answer") == []


def test_a_large_but_sane_answer_is_still_accepted() -> None:
    e = build()
    with e.client:
        seed_run(e)
        body = {"question_id": "q_effort", "answer": 10_000}
        assert (
            e.client.post(f"/runs/{RUN}/answer", json=body, headers=e.headers()).status_code == 202
        )


# =========================================================================================
# 4. stale M2 state
# =========================================================================================
def test_a_run_m3_forgot_but_m2_says_running_is_reported_failed_and_m2_is_corrected() -> None:
    e = build()
    with e.client:
        e.m2.add_run("run_lost", ALICE, state="running")
        e.m2.runs["run_lost"]["input"] = {"idea_id": "i"}
        body = e.client.get("/runs/run_lost", headers=e.headers()).json()
        assert body["state"] == "failed"
        assert body["result"] is None
        assert body["error"]
        assert e.m2.runs["run_lost"]["state"] == "failed"
        assert e.m2.runs["run_lost"]["input"] == {"idea_id": "i"}  # nothing else was lost


def test_a_finished_run_that_m3_forgot_keeps_its_recorded_state() -> None:
    e = build()
    with e.client:
        e.m2.add_run("run_done", ALICE, state="succeeded")
        assert e.client.get("/runs/run_done", headers=e.headers()).json()["state"] == "succeeded"


def test_the_stream_of_a_lost_run_ends_with_a_failure_event() -> None:
    e = build(ScriptedBus(then_idle=True))
    with e.client:
        e.m2.add_run("run_lost", ALICE, state="running")
        e.m3.replay_status = 404
        response = e.client.get("/runs/run_lost/events", headers=e.headers())
        closing = [f for f in frames(response) if f.get("event") == "run_failed"]
        assert len(closing) == 1
        assert e.m2.runs["run_lost"]["state"] == "failed"


def test_a_terminal_state_seen_in_m3_is_written_back_to_m2() -> None:
    e = build()
    with e.client:
        seed_run(e, state="succeeded", result={"verdict": "GO"})
        e.m2.runs[RUN]["state"] = "running"
        e.client.get(f"/runs/{RUN}", headers=e.headers())
        assert e.m2.runs[RUN]["state"] == "succeeded"


def test_a_failing_state_write_back_does_not_fail_the_read() -> None:
    e = build()
    with e.client:
        seed_run(e, state="succeeded")
        e.m2.runs[RUN]["state"] = "running"
        e.m2.fail_put_run = True
        assert e.client.get(f"/runs/{RUN}", headers=e.headers()).status_code == 200


# =========================================================================================
# 6. what M3 says is scrubbed
# =========================================================================================
def test_m3_error_progress_spent_and_budget_are_allow_listed() -> None:
    e = build()
    with e.client:
        seed_run(
            e,
            state="failed",
            error="Traceback (most recent call last): File '/app/x.py' http://m3.internal:8000 sk-live-ABCDEF123456",
            progress={"step": "EVIDENCE", "debug": "http://kb.internal:8000/x", "round": 2},
            spent={
                "cost_usd": 0.02,
                "tokens": 5,
                "leak": "sk-live-ABCDEF123456",
                "pages": "secret",
            },
            budget={"max_cost_usd": 0.05, "dsn": "postgres://u:p@db/x"},
        )
        response = e.client.get(f"/runs/{RUN}", headers=e.headers())
        body = response.json()
        assert body["error"] == "the run failed"
        assert body["progress"] == {"step": "EVIDENCE", "round": 2}
        assert body["spent"] == {"cost_usd": 0.02, "tokens": 5}
        assert body["budget"] == {"max_cost_usd": 0.05}
        for leak in ("Traceback", "internal", "sk-live", "postgres", "debug"):
            assert leak not in response.text


@pytest.mark.parametrize(
    "progress",
    [{"step": "x" * 41}, {"step": "bad\nstep"}, {"step": 5}, {"round": "two"}, {"round": -1}],
)
def test_progress_values_that_are_not_what_we_expect_are_dropped(progress: dict[str, Any]) -> None:
    e = build()
    with e.client:
        seed_run(e, progress=progress)
        assert e.client.get(f"/runs/{RUN}", headers=e.headers()).json()["progress"] == {}


def test_negative_infinite_and_boolean_numbers_are_dropped_from_spent() -> None:
    e = build()
    with e.client:
        seed_run(e, spent={"cost_usd": -1, "tokens": True, "pages": 3, "steps": 10**13})
        assert e.client.get(f"/runs/{RUN}", headers=e.headers()).json()["spent"] == {"pages": 3}


def test_failure_events_carry_a_fixed_reason_not_m3_text() -> None:
    leaky = make_event(
        RUN,
        2,
        "run_failed",
        status="failed",
        reason="Traceback http://m3.internal:8000 sk-live-ABCDEF123456",
        stack="x",
    ).model_dump_json()
    call_event = make_event(
        RUN, 1, "tool_call", tool="fetch", error="ConnectError http://kb.internal:8000"
    ).model_dump_json()
    e = build(ScriptedBus([call_event, leaky]))
    with e.client:
        seed_run(e)
        response = e.client.get(f"/runs/{RUN}/events", headers=e.headers())
        for leak in ("Traceback", "internal", "sk-live", "stack"):
            assert leak not in response.text
        failed = next(f for f in frames(response) if f.get("event") == "run_failed")
        assert failed["json"]["payload"] == {"status": "failed", "reason": "the run failed"}


# =========================================================================================
# 7. the user id travels with every M3 call
# =========================================================================================
def test_every_m3_call_after_the_start_carries_the_user_id() -> None:
    e = build(ScriptedBus(events_for(RUN, 1)))
    with e.client:
        seed_run(e)
        e.client.get(f"/runs/{RUN}", headers=e.headers())
        e.client.post(
            f"/runs/{RUN}/answer",
            json={"question_id": "q_effort", "answer": 1},
            headers=e.headers(),
        )
        e.client.post(
            f"/runs/{RUN}/approve",
            json={"approval_id": f"ap_{RUN}_deep_search", "decision": "approve"},
            headers=e.headers(),
        )
        e.client.get(f"/runs/{RUN}/events", headers=e.headers())
        paths = {p for p, _ in e.m3.user_headers}
        assert paths >= {f"/runs/{RUN}", f"/runs/{RUN}/answer", f"/runs/{RUN}/approve"}
        assert all(value == ALICE for _, value in e.m3.user_headers)
        assert any(p.endswith("/events") for p, _ in e.m3.user_headers)


def test_the_cancel_after_a_failed_owner_write_also_carries_the_user_id() -> None:
    e = build()
    with e.client:
        e.m2.fail_put_run = True
        validate(e)
        cancels = [(p, v) for p, v in e.m3.user_headers if p.endswith("/cancel")]
        assert cancels and all(v == ALICE for _, v in cancels)


@pytest.mark.parametrize("path", ["", "/events"])
def test_an_m2_404_means_m3_is_never_called_for_reads(path: str) -> None:
    e = build()
    with e.client:
        e.m3.runs[RUN] = {"kind": "validate", "state": "running", "input": {}}  # only M3 knows it
        response = e.client.get(f"/runs/{RUN}{path}", headers=e.headers())
        assert response.status_code == 404
        assert e.m3.calls == []


@pytest.mark.parametrize("action", ["answer", "approve"])
def test_an_m2_404_means_m3_is_never_called_for_writes(action: str) -> None:
    e = build()
    with e.client:
        e.m3.runs[RUN] = {"kind": "validate", "state": "running", "input": {}}
        body = (
            {"question_id": "q", "answer": 1}
            if action == "answer"
            else {"approval_id": "a", "decision": "approve"}
        )
        assert (
            e.client.post(f"/runs/{RUN}/{action}", json=body, headers=e.headers()).status_code
            == 404
        )
        assert e.m3.calls == []


# =========================================================================================
# 8, 9. read limits and CORS
# =========================================================================================
def test_every_authenticated_endpoint_has_a_per_user_budget() -> None:
    e = build(user_per_min=3)
    with e.client:
        codes = [e.client.get("/ideas", headers=e.headers()).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        assert e.client.get("/ideas", headers=e.headers(BOB)).status_code == 200


def test_the_default_per_user_budget_is_120_a_minute() -> None:
    from svc_gateway.config import Settings

    assert Settings().user_per_min == 120


def test_preflight_allows_last_event_id_and_idempotency_key() -> None:
    e = build()
    with e.client:
        response = e.client.options(
            f"/runs/{RUN}/events",
            headers={
                "Origin": "https://ui.test",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,last-event-id,idempotency-key",
            },
        )
        assert response.status_code == 200
        allowed = response.headers["access-control-allow-headers"].lower()
        assert "last-event-id" in allowed and "idempotency-key" in allowed


def test_retry_after_is_readable_by_the_browser() -> None:
    e = build(user_per_min=1)
    with e.client:
        headers = {**e.headers(), "Origin": "https://ui.test"}
        e.client.get("/ideas", headers=headers)
        blocked = e.client.get("/ideas", headers=headers)
        assert blocked.status_code == 429
        exposed = blocked.headers["access-control-expose-headers"].lower()
        assert "retry-after" in exposed


# =========================================================================================
# 10. hostile characters, title only for new ideas
# =========================================================================================
def test_the_title_is_not_sent_to_m3_when_validating_an_existing_idea() -> None:
    e = build()
    with e.client:
        idea = e.m2.add_idea(ALICE, text="old")
        validate(e, idea_id=idea, idea_text="new", title="Ignored title")
        assert "title" not in e.m3.posts("/runs")[0][2]["input"]


def test_the_title_is_sent_for_a_new_idea() -> None:
    e = build()
    with e.client:
        validate(e)
        assert e.m3.posts("/runs")[0][2]["input"]["title"] == "Study planner"


@pytest.mark.parametrize("bad", [f"a{chr(c)}b" for c in HOSTILE])
def test_invisible_and_bidi_characters_are_refused_in_free_text(bad: str) -> None:
    e = build()
    with e.client:
        idea = seeded(e)
        assert validate(e, idea_text=bad).status_code == 422
        assert validate(e, title=bad).status_code == 422
        assert discover(e, niche=bad).status_code == 422
        body = {"version": 1, "custom_change": bad}
        assert (
            e.client.post(f"/ideas/{idea}/whatif", json=body, headers=e.headers()).status_code
            == 422
        )
        assert e.m3.calls == []


def seeded(e: Env) -> str:
    return e.m2.add_idea(ALICE, card=CARD, verdict="GO", score=80.0)


def test_ordinary_unicode_text_is_still_fine() -> None:
    e = build()
    with e.client:
        assert (
            validate(e, idea_text="Café app for étudiants, 日本語 ok\nsecond line").status_code
            == 202
        )


# =========================================================================================
# 11. compare keeps all six dimensions
# =========================================================================================
def test_compare_lists_all_six_dimensions_even_when_scores_are_missing() -> None:
    e = build()
    with e.client:
        a = e.m2.add_idea(ALICE, card={"scores": {"demand_pain": 4.0}}, verdict="GO", score=80.0)
        b = e.m2.add_idea(ALICE)
        body = e.client.get(f"/compare?ids={a},{b}", headers=e.headers()).json()
        names = [d["dimension"] for d in body["dimensions"]]
        assert names == [
            "demand_pain", "competition", "differentiation", "feasibility", "risk", "learning",
        ]  # fmt: skip
        empty = next(d for d in body["dimensions"] if d["dimension"] == "risk")
        assert empty["scores"] == {a: None, b: None}
        assert empty["delta"] is None


def test_the_event_stream_is_a_503_when_m3_times_out() -> None:
    e = build(ScriptedBus(then_idle=True))
    with e.client:
        seed_run(e)
        e.m3.timeout = True
        response = e.client.get(f"/runs/{RUN}/events", headers=e.headers())
        assert response.status_code == 503
        assert e.gate.count(ALICE) == 0


def test_a_replay_failure_after_the_stream_opened_sends_an_error_event_and_closes() -> None:
    e = build(ScriptedBus(then_idle=True))
    with e.client:
        seed_run(e)
        e.m3.replay_status = 500
        response = e.client.get(f"/runs/{RUN}/events", headers=e.headers())
        assert response.status_code == 200
        assert response.text.startswith("retry: 3000")
        assert [f["event"] for f in frames(response) if "event" in f] == ["error"]
        assert e.gate.count(ALICE) == 0
