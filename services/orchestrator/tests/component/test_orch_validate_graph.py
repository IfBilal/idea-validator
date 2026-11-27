"""Behaviour tests: the whole Validate graph against fake modules (httpx.MockTransport)."""

from typing import Any

import pytest

from common.budget import Spend
from common.schemas import Verdict
from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.testing import FakeWorld, Harness, fake_verdict, voi_response

DIMS = ("demand_pain", "competition", "differentiation", "feasibility", "risk", "learning")
VERDICT = ("verdict", "POST /verdict")
VERDICT_ROUTE = ("verdict", "POST /verdict")


async def run(h: Harness, **over: Any) -> ValidateState:
    await h.start(**over)
    return await h.state()


# ---- happy path ----------------------------------------------------------------------------
async def test_happy_path_produces_a_card_with_the_verdict_m11_returned() -> None:
    h = Harness()
    state = await run(h)
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card["verdict"] == "GO"
    assert state.card["partial"] is False
    assert state.card["cache_hit"] is False
    assert state.card["run_id"] == "run_1"
    Verdict.model_validate({k: v for k, v in state.card.items() if k in Verdict.model_fields})


async def test_each_module_is_called_the_expected_number_of_times() -> None:
    h = Harness()
    await run(h)
    w = h.world
    assert w.count("profile", "GET /internal/profiles/u1") == 1
    assert w.count("verdict", "GET /config") == 1
    assert w.count("kb", "POST /search") == 4
    assert w.count("analyst", "POST /features") == 1
    assert w.count("validators", "POST /score-all") == 1
    assert w.count("verdict", "POST /verdict") == 1
    assert w.count("critic", "POST /check") == 1
    assert w.count("scouts", "POST /scout") == 0  # evidence was not thin


async def test_the_scorers_only_get_evidence_that_exists_and_the_features() -> None:
    h = Harness()
    await run(h)
    body = h.world.bodies("validators", "POST /score-all")[0]
    assert {e["evidence_id"] for e in body["evidence"]} == {
        e["evidence_id"] for e in h.world.evidence
    }
    assert body["features"] == h.world.features
    assert body["run_id"] == "run_1"


async def test_evidence_queries_cover_the_four_angles() -> None:
    h = Harness()
    await run(h)
    queries = [b["query"] for b in h.world.bodies("kb", "POST /search")]
    assert len(set(queries)) == 4
    assert all(b["run_id"] == "run_1" for b in h.world.bodies("kb", "POST /search"))


async def test_the_verdict_request_carries_six_scores_gate_inputs_and_profile() -> None:
    h = Harness()
    await run(h)
    body = h.world.bodies(*VERDICT_ROUTE)[0]
    assert [s["dimension"] for s in body["scores"]] == list(DIMS)
    assert body["gate_inputs"] == {
        "n_sources": 4,
        "n_platforms": 3,
        "estimated_effort_hours": 100,
        "risk_blocker": None,
        "duplicate_of": None,
    }
    assert body["profile"] == {
        "goal": "startup",
        "hours_per_week": 10,
        "weeks_available": 12,
        "team_size": 2,
    }
    assert (body["idea_id"], body["version"], body["run_id"]) == ("idea_1", 1, "run_1")


async def test_the_card_lists_only_the_evidence_the_scores_cite() -> None:
    h = Harness()
    state = await run(h)
    assert state.card is not None
    cited = {e for s in state.scores for e in s["evidence_ids"]}
    assert set(state.card["evidence"]) == cited
    assert cited  # the fake scorer cites two items


# ---- the orchestrator never decides the verdict --------------------------------------------
@pytest.mark.parametrize("label", ["GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"])
async def test_the_card_verdict_is_exactly_what_m11_returned(label: str) -> None:
    h = Harness()
    h.world.label = label
    state = await run(h)
    assert state.card is not None
    assert state.card["verdict"] == label


async def test_contradictory_scores_do_not_change_the_forwarded_verdict() -> None:
    # every score is a perfect 5.0 with full confidence, yet the (fake) engine says NO-GO.
    # The orchestrator must forward NO-GO untouched, and the opposite case must hold too.
    for scores_value, label in ((5.0, "NO-GO"), (0.0, "GO")):
        h = Harness()
        ids = [h.world.evidence[0]["evidence_id"]]

        def scorer(body: dict[str, Any], v: float = scores_value, ids: list[str] = ids) -> Any:
            return {
                "scores": [
                    {
                        "dimension": d,
                        "score": v,
                        "confidence": 1.0,
                        "evidence_ids": ids,
                        "reason": "x",
                        "rubric_version": f"{d}@v1",
                        "model": "m",
                    }
                    for d in DIMS
                ],
                "extra": {"estimated_effort_hours": 10},
            }

        h.world.score_fn = scorer
        h.world.label = label
        state = await run(h)
        assert state.card is not None
        assert state.card["verdict"] == label
        assert state.card["stability"] == "STABLE"
        assert state.card["score"] == 72.5  # not recomputed from the scores


async def test_no_other_field_of_the_m11_verdict_is_touched() -> None:
    h = Harness()
    h.world.verdict_fn = lambda req: fake_verdict(
        req, "PIVOT", score=63.3333, confidence=0.55, stability="BORDERLINE"
    )
    state = await run(h)
    assert state.card is not None
    assert (state.card["score"], state.card["confidence"]) == (63.3333, 0.55)
    assert state.card["stability"] == "BORDERLINE"
    assert state.card["thresholds"] == {"T_go": 70, "T_no": 50, "floor": 1.5}


async def test_the_orchestrator_never_sends_a_verdict_in_any_request() -> None:
    h = Harness()
    await run(h)
    for service, route, body in h.world.calls:
        if service != "verdict" and body is not None:
            assert "verdict" not in body, (service, route)


# ---- cache -----------------------------------------------------------------------------------
async def test_a_complete_run_is_stored_under_a_key_with_every_version() -> None:
    h = Harness()
    await run(h)
    (key,) = h.world.cache
    assert key.startswith("verdict:idea_1:1:")
    assert key.endswith(":p3:rr1:ww1:em11@v1")


async def test_a_cache_hit_short_circuits_every_expensive_step() -> None:
    first = Harness()
    await run(first)
    second = Harness(first.world, run_id="run_2")
    first.world.calls.clear()
    state = await run(second)
    assert state.cache_hit is True
    assert state.card is not None
    assert state.card["cache_hit"] is True
    assert state.card["verdict"] == "GO"
    assert state.card["run_id"] == "run_2"
    for service, route in (
        ("kb", "POST /search"),
        ("analyst", "POST /features"),
        ("validators", "POST /score-all"),
        ("verdict", "POST /verdict"),
        ("critic", "POST /check"),
    ):
        assert first.world.count(service, route) == 0
    assert state.status is RunStatus.SUCCEEDED


async def test_a_cache_hit_is_traced_as_a_decision() -> None:
    first = Harness()
    await run(first)
    second = Harness(first.world, run_id="run_2")
    await run(second)
    assert any(d.output.get("decision") == "cache_hit" for d in second.decisions())


async def test_a_new_idea_version_is_a_cache_miss() -> None:
    first = Harness()
    await run(first)
    second = Harness(first.world, run_id="run_2")
    state = await run(second, idea_version=2)
    assert state.cache_hit is False
    assert len(first.world.cache) == 2


async def test_a_new_profile_version_is_a_cache_miss() -> None:
    first = Harness()
    await run(first)
    first.world.profile = {**first.world.profile, "version": 4}
    state = await run(Harness(first.world, run_id="run_2"))
    assert state.cache_hit is False


async def test_a_malformed_cache_entry_is_ignored() -> None:
    first = Harness()
    await run(first)
    key = next(iter(first.world.cache))
    first.world.cache[key] = {"verdict": "GO"}  # not a full card
    state = await run(Harness(first.world, run_id="run_2"))
    assert state.cache_hit is False
    assert state.card is not None
    assert state.card["verdict"] == "GO"


async def test_cache_lookup_failure_is_a_miss_not_a_failure() -> None:
    h = Harness()
    # every GET on the cache path fails with 503 (retried, then treated as a miss)
    original = h.world._answer

    def failing(route: str, path: str, body: Any) -> tuple[int, Any]:
        if route.startswith("GET /internal/cache/verdict/"):
            return 503, {}
        return original(route, path, body)

    h.world._answer = failing  # type: ignore[method-assign]
    state = await run(h)
    assert state.status is RunStatus.SUCCEEDED
    assert any("cache unavailable" in n for n in state.notes)


async def test_user_overrides_bypass_the_cache_both_ways() -> None:
    first = Harness()
    await run(first)
    second = Harness(first.world, run_id="run_2")
    state = await run(second, overrides={"estimated_effort_hours": 60})
    assert state.cache_hit is False
    assert len(first.world.cache) == 1  # not stored either
    body = first.world.bodies(*VERDICT_ROUTE)[-1]
    assert body["gate_inputs"]["estimated_effort_hours"] == 60


async def test_a_failed_cache_write_does_not_fail_the_run() -> None:
    h = Harness()
    original = h.world._answer

    def failing(route: str, path: str, body: Any) -> tuple[int, Any]:
        if route.startswith("PUT /internal/cache/verdict/"):
            return 400, {}
        return original(route, path, body)

    h.world._answer = failing  # type: ignore[method-assign]
    assert (await run(h)).status is RunStatus.SUCCEEDED


# ---- thin and empty evidence -----------------------------------------------------------------
def thin_world() -> FakeWorld:
    world = FakeWorld()
    world.evidence = world.evidence[:1]  # one card on one platform
    return world


async def test_empty_evidence_is_forwarded_as_insufficient_evidence_from_m11() -> None:
    world = FakeWorld()
    world.evidence = []

    def scorer(body: dict[str, Any]) -> Any:
        assert body["evidence"] == []
        return {"scores": [], "extra": {}}

    def engine(req: dict[str, Any]) -> Any:
        assert all(s["score"] is None for s in req["scores"])
        assert req["gate_inputs"]["n_sources"] == 0
        return fake_verdict(req, "INSUFFICIENT_EVIDENCE", score=None, confidence=0.0)

    world.score_fn, world.verdict_fn = scorer, engine
    h = Harness(world)
    state = await run(h)
    assert state.card is not None
    assert state.card["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert state.status is RunStatus.SUCCEEDED
    assert state.card["claims"] == []  # nothing to check: no score has evidence
    assert world.count("critic", "POST /check") == 0


async def test_scorers_that_cite_nothing_force_abstention_even_if_they_return_scores() -> None:
    world = FakeWorld()
    world.evidence = []
    world.score_fn = lambda body: {
        "scores": [
            {
                "dimension": d,
                "score": 4.0,
                "confidence": 0.9,
                "evidence_ids": ["card_deadbeef#c0"],
                "reason": "invented",
                "rubric_version": f"{d}@v1",
                "model": "m",
            }
            for d in DIMS
        ],
        "extra": {},
    }
    h = Harness(world)
    await run(h)
    sent = world.bodies(*VERDICT_ROUTE)[0]["scores"]
    assert all(s["score"] is None and s["evidence_ids"] == [] for s in sent)


async def test_thin_evidence_within_budget_scouts_without_asking() -> None:
    world = thin_world()
    world.after_scout = list(FakeWorld().evidence)  # scouting finds more cards
    h = Harness(world)
    state = await run(h)
    assert state.status is RunStatus.SUCCEEDED
    assert world.count("critic", "POST /approval/evaluate") == 1
    assert world.count("scouts", "POST /scout") == 3
    assert {b["source"] for b in world.bodies("scouts", "POST /scout")} == {
        "hackernews",
        "github",
        "reddit",
    }
    assert state.n_sources == 4  # the second evidence pass saw the new cards
    assert "approval_required" not in h.event_types()


async def test_evidence_is_searched_again_after_scouting_not_served_from_the_memo() -> None:
    world = thin_world()
    world.after_scout = list(FakeWorld().evidence)
    await run(Harness(world))
    assert world.count("kb", "POST /search") == 8


async def test_scouting_happens_at_most_once_even_if_evidence_stays_thin() -> None:
    world = thin_world()  # scouting finds nothing new
    await run(Harness(world))
    assert world.count("scouts", "POST /scout") == 3
    assert world.count("kb", "POST /search") == 8


async def test_deep_search_disabled_by_config_goes_straight_to_scoring() -> None:
    world = thin_world()
    state = await run(Harness(world, deep_search_pages=0))
    assert world.count("scouts", "POST /scout") == 0
    assert world.count("critic", "POST /approval/evaluate") == 0
    assert state.status is RunStatus.SUCCEEDED


@pytest.mark.parametrize(("sources", "platforms", "scouts"), [(3, 2, 0), (2, 2, 3), (3, 1, 3)])
async def test_thin_evidence_boundary_decides_whether_to_scout(
    sources: int, platforms: int, scouts: int
) -> None:
    world = FakeWorld()
    base = world.evidence
    plan = {
        (3, 2): [base[0], base[1], base[2]],  # 3 cards on hackernews+github
        (2, 2): [base[0], base[2]],  # 2 cards
        (3, 1): [
            base[0],
            base[1],
            {**base[3], "source": "hackernews"},  # 3 cards, one platform
        ],
    }
    world.evidence = plan[(sources, platforms)]
    await run(Harness(world))
    assert world.count("scouts", "POST /scout") == scouts


async def test_scout_failures_degrade_the_run_but_do_not_fail_it() -> None:
    world = thin_world()
    world.faults[("scouts", "POST /scout")] = [503] * 20
    state = await run(Harness(world))
    assert state.status is RunStatus.SUCCEEDED
    assert state.degraded is True
    assert len(world.cache) == 0  # a degraded result is never cached


# ---- approval --------------------------------------------------------------------------------
async def test_approval_needed_pauses_the_run_with_an_approval_request() -> None:
    world = thin_world()
    world.needs_approval = True
    h = Harness(world)
    result = await h.start()
    assert "__interrupt__" in result
    pending = await h.pending()
    assert pending is not None
    assert pending["type"] == "approval"
    assert pending["action"] == "deep_search"
    assert pending["estimated_cost_usd"] == pytest.approx(0.016)
    assert pending["approval_id"] == "ap_run_1_deep_search"
    assert world.count("scouts", "POST /scout") == 0
    event = next(e for e in h.publisher.events if e.type == "approval_required")
    assert event.payload["approval_id"] == "ap_run_1_deep_search"
    assert event.payload["budget_usd"] == pytest.approx(0.05)


async def test_approving_raises_the_cost_limit_once_and_scouts() -> None:
    world = thin_world()
    world.needs_approval = True
    h = Harness(world)
    await h.start()
    await h.resume("approve")
    state = await h.state()
    assert state.status is RunStatus.SUCCEEDED
    assert world.count("scouts", "POST /scout") == 3
    assert (await h.ledger.limits()).max_cost_usd == pytest.approx(0.05 + 0.016)
    assert state.approval_decision == "approved"
    assert state.pending_approval is None


async def test_declining_continues_with_the_evidence_already_collected() -> None:
    world = thin_world()
    world.needs_approval = True
    h = Harness(world)
    await h.start()
    await h.resume("decline")
    state = await h.state()
    assert state.status is RunStatus.SUCCEEDED
    assert world.count("scouts", "POST /scout") == 0
    assert world.count("validators", "POST /score-all") == 1
    assert state.approval_decision == "declined"
    assert any("declined" in n for n in state.notes)
    assert (await h.ledger.limits()).max_cost_usd == pytest.approx(0.05)  # unchanged


async def test_an_unknown_decision_value_counts_as_decline() -> None:
    world = thin_world()
    world.needs_approval = True
    h = Harness(world)
    await h.start()
    await h.resume("maybe")
    assert (await h.state()).approval_decision == "declined"


async def test_resuming_after_the_approval_does_not_repeat_earlier_steps() -> None:
    world = thin_world()
    world.needs_approval = True
    h = Harness(world)
    await h.start()
    before = world.count("kb", "POST /search"), world.count("profile", "GET /internal/profiles/u1")
    await h.resume("decline")
    assert (
        world.count("kb", "POST /search"),
        world.count("profile", "GET /internal/profiles/u1"),
    ) == before


async def test_local_threshold_forces_approval_even_if_m12_says_no() -> None:
    world = thin_world()  # M12 says no approval needed, but spend + estimate > threshold
    h = Harness(world)
    await h.ledger.record(Spend(cost_usd=0.04))
    await h.start()
    pending = await h.pending()
    assert pending is not None
    assert pending["type"] == "approval"


async def test_when_the_approval_check_is_down_the_user_is_asked() -> None:
    world = thin_world()
    world.faults[("critic", "POST /approval/evaluate")] = [503] * 3
    h = Harness(world)
    await h.start()
    pending = await h.pending()
    assert pending is not None
    assert pending["type"] == "approval"
    assert world.count("critic", "POST /approval/evaluate") == 3


async def test_deep_search_is_skipped_when_the_page_budget_cannot_take_it() -> None:
    world = thin_world()
    h = Harness(world, limits={"max_pages": 5})
    state = await run(h)
    assert world.count("scouts", "POST /scout") == 0
    assert any("deep search skipped" in n for n in state.notes)
    assert state.status is RunStatus.SUCCEEDED


# ---- scorer failures -------------------------------------------------------------------------
async def test_invalid_scorer_output_abstains_only_the_bad_dimensions() -> None:
    world = FakeWorld()
    ids = [world.evidence[0]["evidence_id"]]
    good = {
        "confidence": 0.8,
        "evidence_ids": ids,
        "reason": "ok",
        "model": "m",
    }
    world.score_fn = lambda body: {
        "scores": [
            {**good, "dimension": "demand_pain", "score": 4.0, "rubric_version": "demand_pain@v1"},
            {"dimension": "competition", "score": "high"},  # invalid JSON shape
            "garbage",
            {**good, "dimension": "feasibility", "score": 9.0, "rubric_version": "feasibility@v1"},
        ],
        "extra": {"estimated_effort_hours": 50},
    }
    state = await run(Harness(world))
    sent = {s["dimension"]: s for s in world.bodies(*VERDICT_ROUTE)[0]["scores"]}
    assert sent["demand_pain"]["score"] == 4.0
    for dim in ("competition", "differentiation", "feasibility", "risk", "learning"):
        assert sent[dim]["score"] is None
        assert sent[dim]["confidence"] == 0
    assert state.card is not None
    assert state.status is RunStatus.SUCCEEDED


async def test_a_scorer_that_returns_html_instead_of_json_makes_every_dimension_abstain() -> None:
    world = FakeWorld()
    world.faults[("validators", "POST /score-all")] = ["garbage"]
    h = Harness(world)
    state = await run(h)
    assert world.count("validators", "POST /score-all") == 1  # a bad body is not retried
    sent = world.bodies(*VERDICT_ROUTE)[0]["scores"]
    assert all(s["score"] is None for s in sent)
    assert state.degraded is True
    assert state.status is RunStatus.SUCCEEDED


async def test_scorer_timeout_is_retried_twice_then_every_dimension_abstains() -> None:
    world = FakeWorld()
    world.faults[("validators", "POST /score-all")] = ["timeout"] * 3
    h = Harness(world)
    state = await run(h)
    assert world.count("validators", "POST /score-all") == 3  # 1 try + 2 retries, no more
    sent = world.bodies(*VERDICT_ROUTE)[0]["scores"]
    assert all(s["score"] is None and s["abstained"] for s in sent)
    assert state.degraded is True
    assert any("abstained" in n for n in state.notes)
    assert state.card is not None
    assert state.card["degraded"] is True
    record = next(r for r in h.records if r.tool == "m9.score_all")
    assert record.retries == 2
    assert record.error is not None
    assert world.cache == {}


async def test_a_scorer_timeout_then_success_uses_the_retry() -> None:
    world = FakeWorld()
    world.faults[("validators", "POST /score-all")] = ["timeout"]
    h = Harness(world)
    state = await run(h)
    assert world.count("validators", "POST /score-all") == 2
    sent = world.bodies(*VERDICT_ROUTE)[0]["scores"]
    assert all(s["score"] is not None for s in sent)
    assert state.degraded is False
    record = next(r for r in h.records if r.tool == "m9.score_all")
    assert (record.retries, record.error) == (1, None)


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_retryable_statuses_are_retried(status: int) -> None:
    world = FakeWorld()
    world.faults[("validators", "POST /score-all")] = [status, status]
    await run(Harness(world))
    assert world.count("validators", "POST /score-all") == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_client_errors_are_never_retried(status: int) -> None:
    world = FakeWorld()
    world.faults[("validators", "POST /score-all")] = [status]
    state = await run(Harness(world))
    assert world.count("validators", "POST /score-all") == 1
    assert state.degraded is True


async def test_max_retries_zero_means_a_single_attempt() -> None:
    world = FakeWorld()
    world.faults[("validators", "POST /score-all")] = ["timeout"] * 3
    await run(Harness(world, max_retries=0))
    assert world.count("validators", "POST /score-all") == 1


async def test_scorer_that_always_hangs_is_bounded_by_the_timeout() -> None:
    world = FakeWorld()
    world.hang.add(("validators", "POST /score-all"))
    state = await run(Harness(world))
    assert world.count("validators", "POST /score-all") == 3
    assert state.degraded is True


async def test_features_failure_degrades_but_scoring_still_runs() -> None:
    world = FakeWorld()
    world.faults[("analyst", "POST /features")] = [503] * 3
    state = await run(Harness(world))
    assert state.features == {}
    assert state.degraded is True
    assert world.count("validators", "POST /score-all") == 1
    assert state.status is RunStatus.SUCCEEDED


async def test_unknown_effort_is_flagged_and_not_cached() -> None:
    world = FakeWorld()
    world.extra = {}
    state = await run(Harness(world))
    assert state.degraded is True
    assert any("effort" in n for n in state.notes)
    assert world.cache == {}


# ---- failures that must stop the run -----------------------------------------------------------
async def test_when_the_knowledge_base_is_down_the_run_fails_clearly() -> None:
    world = FakeWorld()
    world.faults[("kb", "POST /search")] = [503] * 20
    h = Harness(world)
    state = await run(h)
    assert state.status is RunStatus.FAILED
    assert state.error is not None
    assert "EVIDENCE" in state.error
    assert world.count("validators", "POST /score-all") == 0
    assert world.count(*VERDICT_ROUTE) == 0
    assert state.card is not None
    assert state.card["partial"] is True
    assert state.card["verdict"] is None
    assert "run_failed" in h.event_types()


async def test_one_failed_search_among_four_only_degrades() -> None:
    world = FakeWorld()
    original = world._answer

    def failing(route: str, path: str, body: Any) -> tuple[int, Any]:
        if route == "POST /search" and body["query"].startswith("demand"):
            return 503, {}
        return original(route, path, body)

    world._answer = failing  # type: ignore[method-assign]
    state = await run(Harness(world))
    assert state.status is RunStatus.SUCCEEDED
    assert state.degraded is True
    assert any("evidence searches failed" in n for n in state.notes)


async def test_when_m11_is_down_the_run_fails_and_no_verdict_is_invented() -> None:
    world = FakeWorld()
    world.faults[VERDICT_ROUTE] = [503] * 3
    h = Harness(world)
    state = await run(h)
    assert state.status is RunStatus.FAILED
    assert state.verdict is None
    assert state.card is not None
    assert state.card["verdict"] is None
    assert state.card["partial"] is True
    assert world.count(*VERDICT_ROUTE) == 3
    assert world.cache == {}


async def test_when_m11_config_is_unreachable_the_run_fails_at_intake() -> None:
    world = FakeWorld()
    world.faults[("verdict", "GET /config")] = [503] * 3
    state = await run(Harness(world))
    assert state.status is RunStatus.FAILED
    assert world.count("kb", "POST /search") == 0


async def test_unknown_user_profile_fails_the_run() -> None:
    world = FakeWorld()
    world.faults[("profile", "GET /internal/profiles/u1")] = [404]
    state = await run(Harness(world))
    assert state.status is RunStatus.FAILED
    assert "INTAKE" in (state.error or "")


async def test_when_the_critic_is_down_the_run_fails_rather_than_skip_the_guardrail() -> None:
    world = FakeWorld()
    world.faults[("critic", "POST /check")] = [503] * 3
    state = await run(Harness(world))
    assert state.status is RunStatus.FAILED
    assert world.cache == {}


# ---- claim check ------------------------------------------------------------------------------
async def test_unsupported_claims_are_removed_and_m11_recomputes() -> None:
    world = FakeWorld()

    def check(body: dict[str, Any]) -> Any:
        return {
            "results": [
                {
                    "claim_id": c["claim_id"],
                    "supported": c["claim_id"] != "cl_competition",
                    "reason": "quote does not support",
                }
                for c in body["claims"]
            ]
        }

    def engine(req: dict[str, Any]) -> Any:
        abstained = [s["dimension"] for s in req["scores"] if s["score"] is None]
        return fake_verdict(req, "INSUFFICIENT_EVIDENCE" if abstained else "GO")

    world.check_fn, world.verdict_fn = check, engine
    h = Harness(world)
    state = await run(h)
    assert world.count(*VERDICT_ROUTE) == 2
    second = world.bodies(*VERDICT_ROUTE)[1]["scores"]
    assert {s["dimension"]: s["score"] for s in second}["competition"] is None
    assert state.card is not None
    assert state.card["verdict"] == "INSUFFICIENT_EVIDENCE"  # M11's answer to the recompute
    assert state.card["challenge_log"][0]["claim_id"] == "cl_competition"
    assert {c["claim_id"]: c["removed"] for c in state.card["claims"]}["cl_competition"] is True
    assert world.count("critic", "POST /check") == 1  # the claim check is not repeated


async def test_supported_claims_cause_no_recompute() -> None:
    world = FakeWorld()
    await run(Harness(world))
    assert world.count(*VERDICT_ROUTE) == 1


# ---- clarifying questions via M11 value-of-information ------------------------------------------
def effort_voi(body: dict[str, Any]) -> Any:
    return voi_response(True)


def effort_engine(req: dict[str, Any]) -> Any:
    hours = req["gate_inputs"]["estimated_effort_hours"]
    return fake_verdict(req, "NO-GO" if hours > 150 else "GO")


def asking_world() -> FakeWorld:
    world = FakeWorld()
    world.voi_fn, world.verdict_fn = effort_voi, effort_engine
    return world


async def test_no_question_is_asked_when_the_answer_cannot_change_the_verdict() -> None:
    world = FakeWorld()
    h = Harness(world)
    state = await run(h)
    assert state.status is RunStatus.SUCCEEDED
    assert world.count("verdict", "POST /value-of-information") == 1
    assert "question" not in h.event_types()
    assert state.vetted == ["estimated_effort_hours"]


async def test_a_question_is_asked_when_the_verdict_could_flip() -> None:
    h = Harness(asking_world())
    result = await h.start()
    assert "__interrupt__" in result
    pending = await h.pending()
    assert pending is not None
    assert pending["type"] == "question"
    assert pending["question_id"] == "q_estimated_effort_hours"
    assert "GO" in pending["why_it_matters"] and "NO-GO" in pending["why_it_matters"]
    event = next(e for e in h.publisher.events if e.type == "question")
    assert event.payload["question_id"] == "q_estimated_effort_hours"
    assert h.world.count(*VERDICT_ROUTE) == 0  # nothing decided yet


async def test_the_voi_request_names_the_unknown_and_its_range() -> None:
    h = Harness(asking_world())
    await h.start()
    body = h.world.bodies("verdict", "POST /value-of-information")[0]
    assert body["unknown"] == {"scope": "gate_input", "field": "estimated_effort_hours"}
    assert body["range"] == [50, 200]
    assert body["input"]["gate_inputs"]["estimated_effort_hours"] == 100


@pytest.mark.parametrize(
    ("hours", "label"), [(80, "GO"), (150, "GO"), (151, "NO-GO"), (300, "NO-GO")]
)
async def test_the_answer_changes_the_verdict_through_m11_not_the_orchestrator(
    hours: float, label: str
) -> None:
    h = Harness(asking_world())
    await h.start()
    await h.resume(hours)
    state = await h.state()
    assert state.card is not None
    assert state.card["verdict"] == label
    assert h.world.bodies(*VERDICT_ROUTE)[0]["gate_inputs"]["estimated_effort_hours"] == hours
    assert state.status is RunStatus.SUCCEEDED


async def test_resume_after_the_question_does_not_repeat_evidence_or_scoring() -> None:
    h = Harness(asking_world())
    await h.start()
    before = {
        r: h.world.count(s, r)
        for s, r in (("kb", "POST /search"), ("validators", "POST /score-all"))
    }
    await h.resume(80)
    assert h.world.count("kb", "POST /search") == before["POST /search"] == 4
    assert h.world.count("validators", "POST /score-all") == 1


async def test_an_answered_run_is_not_cached() -> None:
    h = Harness(asking_world())
    await h.start()
    await h.resume(80)
    assert h.world.cache == {}


async def test_no_answer_falls_back_to_the_scorer_estimate_and_notes_it() -> None:
    h = Harness(asking_world())
    await h.start()
    await h.resume("timeout")  # the question timed out: anything but a number
    state = await h.state()
    assert h.world.bodies(*VERDICT_ROUTE)[0]["gate_inputs"]["estimated_effort_hours"] == 100
    assert state.degraded is True
    assert any("no answer" in n for n in state.notes)
    assert state.status is RunStatus.SUCCEEDED


async def test_a_user_supplied_value_means_no_question_about_it() -> None:
    h = Harness(asking_world())
    await h.start(overrides={"estimated_effort_hours": 80})
    state = await h.state()
    assert state.status is RunStatus.SUCCEEDED
    assert h.world.count("verdict", "POST /value-of-information") == 0


async def test_at_most_three_questions_are_asked() -> None:
    world = asking_world()
    unknowns = [
        {"scope": "profile", "field": "hours_per_week", "range": [5, 20]},
        {"scope": "profile", "field": "weeks_available", "range": [4, 20]},
        {"scope": "profile", "field": "team_size", "range": [1, 4]},
    ]
    h = Harness(world)
    result = await h.start(unknowns=unknowns)
    asked = 0
    while "__interrupt__" in result:
        asked += 1
        assert asked <= 3
        result = await h.resume(10)
    state = await h.state()
    assert asked == 3
    assert state.status is RunStatus.SUCCEEDED
    assert len(state.asked) == 3
    assert world.count(*VERDICT_ROUTE) == 1


async def test_profile_answers_reach_the_verdict_request() -> None:
    world = asking_world()
    h = Harness(world)
    result = await h.start(
        unknowns=[{"scope": "profile", "field": "hours_per_week", "range": [5, 20]}]
    )
    while "__interrupt__" in result:
        pending = await h.pending()
        assert pending is not None
        result = await h.resume(25 if pending["field"] == "hours_per_week" else 100)
    assert world.bodies(*VERDICT_ROUTE)[0]["profile"]["hours_per_week"] == 25


async def test_a_voi_failure_does_not_block_the_run() -> None:
    world = asking_world()
    world.faults[("verdict", "POST /value-of-information")] = [503] * 3
    state = await run(Harness(world))
    assert state.status is RunStatus.SUCCEEDED
    assert any("could not check" in n for n in state.notes)


# ---- budgets -----------------------------------------------------------------------------------
async def steps_of_a_clean_run() -> int:
    h = Harness()
    await run(h)
    return (await h.ledger.spent()).steps


async def test_every_step_is_charged_to_the_budget() -> None:
    assert await steps_of_a_clean_run() >= 10


async def test_a_run_that_just_fits_its_step_budget_succeeds_and_is_cached() -> None:
    n = await steps_of_a_clean_run()
    h = Harness(limits={"max_steps": n})
    assert (await run(h)).status is RunStatus.SUCCEEDED
    assert len(h.world.cache) == 1


async def test_without_room_for_the_cache_write_the_run_still_succeeds() -> None:
    n = await steps_of_a_clean_run()
    h = Harness(limits={"max_steps": n - 1})  # the last step (cache write) is the optional one
    assert (await run(h)).status is RunStatus.SUCCEEDED
    assert h.world.cache == {}


async def test_two_steps_less_stops_the_run_with_a_partial_card() -> None:
    n = await steps_of_a_clean_run()
    h = Harness(limits={"max_steps": n - 2})
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert state.stop_reason is not None
    assert state.card is not None
    assert state.card["partial"] is True
    assert "budget_stopped" in h.event_types()
    assert "run_completed" not in h.event_types()


async def test_a_run_that_is_already_over_budget_makes_no_module_calls() -> None:
    h = Harness()
    await h.ledger.record(Spend(cost_usd=0.06))  # M5 spent more than the cap before M3 started
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert h.world.calls == []
    assert state.card is not None
    assert state.card["verdict"] is None


async def test_budget_exhausted_mid_run_returns_the_scores_computed_so_far() -> None:
    world = FakeWorld()
    h = Harness(world)

    original = world.score_fn

    def spendy(body: dict[str, Any]) -> Any:
        # M5 mirrors the (hosted-model) cost of the scorers into the shared ledger hash
        h.redis.hashes.setdefault("budget:run_1", {})["cost_usd"] = 0.07
        return original(body) if original else {"scores": world._scores(body), "extra": world.extra}

    world.score_fn = spendy
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert world.count(*VERDICT_ROUTE) == 0
    assert state.card is not None
    assert state.card["partial"] is True
    assert len(state.card["scores"]) == 6
    assert state.card["stop_reason"]
    assert state.card["verdict"] is None


async def test_exactly_at_the_cost_cap_is_allowed() -> None:
    h = Harness()
    await h.ledger.record(Spend(cost_usd=0.05))
    assert (await run(h)).status is RunStatus.SUCCEEDED


async def test_budget_stop_is_traced_as_a_decision() -> None:
    h = Harness()
    await h.ledger.record(Spend(steps=999))
    await run(h)
    assert any(d.output.get("decision") == "budget_stop" for d in h.decisions())


# ---- tracing, events, memoisation ----------------------------------------------------------
async def test_every_module_call_has_a_trace_record_under_one_run_id() -> None:
    h = Harness()
    await run(h)
    tools = {r.tool for r in h.records}
    assert {
        "m2.get_profile",
        "m11.config",
        "m2.cache_get",
        "m6.search",
        "m7.features",
        "m9.score_all",
        "m11.verdict",
        "m12.check",
        "m2.cache_put",
        "transition",
    } <= tools
    assert {r.run_id for r in h.records} == {"run_1"}
    assert all(r.service == "orchestrator" for r in h.records)


async def test_transitions_are_traced_as_decisions() -> None:
    h = Harness()
    await run(h)
    transitions = [d for d in h.decisions() if d.output["decision"] == "transition"]
    assert [t.input["from"] for t in transitions][:3] == ["intake", "cache_check", "evidence"]
    assert transitions[-1].output["to"] == "card"


async def test_trace_records_never_contain_the_internal_token() -> None:
    h = Harness()
    await run(h)
    assert "test-token" not in "".join(r.model_dump_json() for r in h.records)


async def test_events_follow_the_run_through_its_states() -> None:
    h = Harness()
    await run(h)
    changes = [
        (e.payload["from"], e.payload["to"])
        for e in h.publisher.events
        if e.type == "state_changed"
    ]
    assert changes[0] == ("INTAKE", "INTAKE")
    assert changes[-1][1] == "CARD"
    types = h.event_types()
    assert types[-2:] == ["verdict", "run_completed"]
    assert "spend_update" in types
    seqs = [e.seq for e in h.publisher.events]
    assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))


async def test_step_outputs_are_memoised_in_the_state_by_step_and_input_hash() -> None:
    h = Harness()
    state = await run(h)
    keys = list(state.memo)
    assert any(k.startswith("evidence:") for k in keys)
    assert any(k.startswith("scorers:") for k in keys)
    assert any(k.startswith("verdict:") for k in keys)
    assert len(keys) == len(set(keys))


async def test_rerunning_with_the_memo_skips_every_module_call() -> None:
    first = Harness()
    first_state = await run(first)
    again = Harness(first.world, run_id="run_1")
    first.world.calls.clear()
    first.world.cache.clear()  # force a full run, only the memo is available
    await again.start(**{"memo": first_state.memo})
    kinds = {(s, r) for s, r, _ in first.world.calls}
    assert ("kb", "POST /search") not in kinds
    assert ("validators", "POST /score-all") not in kinds
    assert ("verdict", "POST /verdict") not in kinds
    assert any(r.cache_hit for r in again.records)


async def test_a_changed_profile_only_reruns_what_depends_on_it() -> None:
    first = Harness()
    first_state = await run(first)
    first.world.cache.clear()
    first.world.profile = {**first.world.profile, "hours_per_week": 40, "version": 4}
    again = Harness(first.world, run_id="run_1")
    first.world.calls.clear()
    await again.start(**{"memo": first_state.memo})
    searched = first.world.count("kb", "POST /search")
    assert searched == 0  # evidence does not depend on the profile
    assert first.world.count("validators", "POST /score-all") == 1  # scorers saw the profile
    assert first.world.count(*VERDICT_ROUTE) == 1
