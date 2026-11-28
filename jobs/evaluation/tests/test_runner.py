import asyncio
from typing import Any

import httpx
import pytest
from evaluation.gateway import Gateway, GatewayError
from evaluation.labelled import LabelledItem
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
CFG = RunConfig(poll_s=0, item_timeout_s=5, item_retries=2)


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)  # yield, so timeouts can fire


def make_item(n: str = "L1", **over: Any) -> LabelledItem:
    base: dict[str, Any] = {"id": n, "idea_text": "idea", "profile": PROFILE, "human_verdict": "GO"}
    return LabelledItem(**{**base, **over})


async def run_one(fake: FakeGateway, item: LabelledItem, cfg: RunConfig = CFG) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url="http://gw", transport=fake.transport()) as client:
        gw = Gateway(client, sleep=no_sleep)
        session = await sign_in(gw, "e+w0@example.com", "pw")
        return await run_item(gw, gw, session, item, cfg, sleep=no_sleep)


@pytest.mark.parametrize("verdict", ["GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"])
async def test_every_verdict_is_passed_through_untouched(verdict: str) -> None:
    fake = FakeGateway({"eval L1": {"card": good_card(verdict)}})
    row = await run_one(fake, make_item())
    assert row["status"] == "ok" and row["system"] == verdict
    assert row["behaviour"]["search_kb_before_scoring"] is True
    assert row["run_id"].startswith("run_L1") and row["trace_available"] is True
    assert row["cost_usd"] == 0.01 and row["agent_latency_ms"]["validators"] == 400


async def test_registers_the_user_once_when_login_is_refused() -> None:
    fake = FakeGateway()
    await run_one(fake, make_item())
    assert fake.calls.count(("POST", "/auth/register")) == 1


async def test_login_server_error_is_not_swallowed() -> None:
    fake = FakeGateway()
    fake.login_status = 500
    with pytest.raises(GatewayError):
        await run_one(fake, make_item())


async def test_timeout_is_an_error_row() -> None:
    fake = FakeGateway({"eval L1": {"never_finishes": True}})
    row = await run_one(fake, make_item(), RunConfig(poll_s=0, item_timeout_s=0.05))
    assert row["status"] == "error" and row["error"] == "timeout" and row["system"] is None
    assert row["attempts"] == 1  # a timeout is not retried (it costs a full run)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_start_failures_are_retried(status: int) -> None:
    fake = FakeGateway({"eval L1": {"validate_failures": [status, status]}})
    row = await run_one(fake, make_item())
    assert row["status"] == "ok" and fake.validate_attempts["eval L1"] == 3


async def test_retries_are_bounded_at_two() -> None:
    fake = FakeGateway({"eval L1": {"validate_failures": [503] * 10}})
    row = await run_one(fake, make_item())
    assert row["status"] == "error" and row["error"] == "gateway_error"
    assert fake.validate_attempts["eval L1"] <= 9  # 3 item attempts x 3 http tries, never more


async def test_client_error_is_not_retried() -> None:
    fake = FakeGateway({"eval L1": {"validate_failures": [422]}})
    row = await run_one(fake, make_item())
    assert row["error"] == "http_422" and row["attempts"] == 1


async def test_failed_run_is_retried_then_reported() -> None:
    fake = FakeGateway({"eval L1": {"state": "failed"}})
    row = await run_one(fake, make_item())
    assert row["status"] == "error" and row["error"] == "run_failed" and row["attempts"] == 3
    assert len(set(fake.idem_keys)) == 3  # a retry is a new run, not an idempotent replay


async def test_budget_stopped_is_an_error_not_a_verdict() -> None:
    row = await run_one(FakeGateway({"eval L1": {"state": "budget_stopped"}}), make_item())
    assert row["error"] == "budget_stopped" and row["system"] is None


@pytest.mark.parametrize(
    ("card", "code"),
    [
        (None, "malformed_card"),
        ("text", "malformed_card"),
        (good_card(verdict=None, partial=True), "no_verdict"),
        (good_card(verdict="MAYBE"), "malformed_card"),
        (good_card(stability="SHAKY"), "malformed_card"),
        (good_card(score="high"), "malformed_card"),
        (good_card(confidence=True), "malformed_card"),
    ],
)
async def test_malformed_cards_are_errors(card: Any, code: str) -> None:
    row = await run_one(FakeGateway({"eval L1": {"card": card}}), make_item())
    assert row["status"] == "error" and row["error"] == code


@pytest.mark.parametrize("mode", ["missing", "bad", "down"])
async def test_missing_trace_keeps_the_verdict_but_fails_trace_checks(mode: str) -> None:
    row = await run_one(FakeGateway({"eval L1": {"trace": mode}}), make_item())
    assert row["status"] == "ok" and row["system"] == "GO" and row["trace_available"] is False
    assert row["behaviour"]["search_kb_before_scoring"] is False
    assert row["behaviour"]["max_debate_rounds_le_3"] is False
    assert row["behaviour"]["every_score_cites_valid_evidence"] is True


async def test_approval_is_declined_never_approved() -> None:
    approval = {"approval_id": "ap_1", "estimated_cost_usd": 0.02}
    fake = FakeGateway({"eval L1": {"approval": approval}})
    row = await run_one(fake, make_item())
    assert row["status"] == "ok" and row["notes"] == ["approval_declined"]
    assert fake.approvals == [{"approval_id": "ap_1", "decision": "decline"}]


async def test_question_answered_from_the_labelled_profile() -> None:
    q = {"question_id": "q_hours_per_week", "field": "hours_per_week", "required": False}
    fake = FakeGateway({"eval L1": {"question": q}})
    row = await run_one(fake, make_item())
    assert row["status"] == "ok"
    assert fake.answers == [{"question_id": "q_hours_per_week", "answer": 10.0}]


async def test_effort_question_uses_only_the_labelled_estimate() -> None:
    q = {"question_id": "q_estimated_effort_hours", "field": "estimated_effort_hours"}
    fake = FakeGateway({"eval L1": {"question": q}})
    await run_one(fake, make_item(estimated_effort_hours=80))
    assert fake.answers[0]["answer"] == 80.0


async def test_unknown_question_is_left_unanswered_and_reported() -> None:
    q = {"question_id": "q_estimated_effort_hours", "field": "estimated_effort_hours"}
    fake = FakeGateway({"eval L1": {"question": q, "question_blocks": True}})
    row = await run_one(fake, make_item(), RunConfig(poll_s=0, item_timeout_s=0.05, item_retries=0))
    assert row["error"] == "unanswered_question" and fake.answers == []


async def test_budget_overrun_is_flagged_not_hidden() -> None:
    fake = FakeGateway(
        {"eval L1": {"spent": {"cost_usd": 0.5, "tokens": 1, "pages": 1, "steps": 1}}}
    )
    row = await run_one(fake, make_item())
    assert row["behaviour"]["budget_kept"] is False


async def test_poisoned_item_checks_run() -> None:
    item = make_item(poisoned=True, injection_forces="GO", human_verdict="NO-GO")
    row = await run_one(FakeGateway({"eval L1": {"card": good_card("GO")}}), item)
    assert row["behaviour"]["injection_resisted"] is False


async def test_run_all_orders_rows_by_input_and_bounds_workers() -> None:
    items = [make_item(f"L{i}") for i in range(7)]
    fake = FakeGateway({"eval L0": {"polls": 4}, "eval L3": {"state": "failed"}})
    async with httpx.AsyncClient(base_url="http://gw", transport=fake.transport()) as client:
        gw = Gateway(client, sleep=no_sleep)
        sessions = [await sign_in(gw, slot_email("e@example.com", s), "pw") for s in range(2)]
        seen: list[str] = []
        rows = await run_all(
            items, sessions, gw, gw, CFG, on_row=lambda r: seen.append(r["id"]), sleep=no_sleep
        )
    assert [r["id"] for r in rows] == [i.id for i in items]
    assert sorted(seen) == sorted(i.id for i in items)
    assert [r["status"] for r in rows].count("error") == 1
    assert len({e for e in fake.users}) == 2  # one eval user per slot


def test_slot_email_keeps_the_domain() -> None:
    assert slot_email("eval@x.org", 3) == "eval+w3@x.org"
