import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import CONFIG_FILE, GOOD, WORKED_EXAMPLE, WORKED_EXAMPLE_CONF
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from svc_verdict.config import Settings
from svc_verdict.main import build_app

SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"
HEADERS = {"X-Internal-Token": "tok"}


def verdict_validator() -> Draft202012Validator:
    registry = Registry().with_resources(
        [
            (p.name, Resource.from_contents(json.loads(p.read_text()), DRAFT202012))
            for p in SCHEMAS.glob("*.schema.json")
        ]
    )
    schema = json.loads((SCHEMAS / "verdict.schema.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def payload(
    goal: str = "portfolio",
    scores: dict[str, float | None] | None = None,
    conf: dict[str, float] | None = None,
    **gate: Any,
) -> dict[str, Any]:
    score_map: dict[str, float | None] = dict(scores or WORKED_EXAMPLE)
    conf_map = conf or WORKED_EXAMPLE_CONF
    return {
        "idea_id": "idea_1",
        "version": 1,
        "run_id": "run_1",
        "profile": {
            "goal": goal,
            "hours_per_week": 10,
            "weeks_available": 12,
            "team_size": 2,
        },
        "scores": [
            {
                "dimension": d,
                "score": score_map[d],
                "confidence": conf_map[d],
                "evidence_ids": ["card_8f21ab00#c2"] if score_map[d] is not None else [],
                "reason": "cites the evidence",
                "rubric_version": f"{d}@v1",
                "model": "scorer@v1",
            }
            for d in WORKED_EXAMPLE
        ],
        "gate_inputs": {
            "n_sources": 5,
            "n_platforms": 2,
            "estimated_effort_hours": 100,
            **gate,
        },
        "objections_accepted": [],
    }


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(config_dir=CONFIG_FILE.parent, internal_token="tok")
    with TestClient(build_app(settings)) as test_client:
        yield test_client


def test_verdict_response_is_valid_against_the_published_schema(client: TestClient) -> None:
    response = client.post("/verdict", json=payload(), headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(verdict_validator().iter_errors(body)) == []
    assert (body["verdict"], body["stability"], body["score"]) == ("GO", "BORDERLINE", 70.5)
    assert body["what_would_make_go"][0]["dimension"] == "competition"
    assert body["versions"] == {"engine": "m11@v1", "rubrics": "r1", "profile_weights": "w1"}


def test_the_caller_cannot_supply_a_verdict(client: TestClient) -> None:
    response = client.post("/verdict", json={**payload(), "verdict": "GO"}, headers=HEADERS)
    assert response.status_code == 422


def test_internal_endpoints_require_the_internal_token(client: TestClient) -> None:
    assert client.post("/verdict", json=payload()).status_code == 401
    assert (
        client.post("/verdict", json=payload(), headers={"X-Internal-Token": "x"}).status_code
        == 401
    )
    assert client.get("/config").status_code == 401


def test_unknown_goal_is_a_422_with_the_standard_envelope(client: TestClient) -> None:
    response = client.post("/verdict", json=payload(goal="hobby"), headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_scores_out_of_range_are_rejected(client: TestClient) -> None:
    bad = payload(scores={**WORKED_EXAMPLE, "risk": 6.0})
    assert client.post("/verdict", json=bad, headers=HEADERS).status_code == 422


def test_insufficient_evidence_says_what_is_missing(client: TestClient) -> None:
    response = client.post("/verdict", json=payload(n_sources=1, n_platforms=1), headers=HEADERS)
    body = response.json()
    assert body["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert body["missing"] == ["2 more independent source(s)", "1 more platform(s)"]
    assert list(verdict_validator().iter_errors(body)) == []


def test_abstention_is_reported_in_the_scores_and_missing(client: TestClient) -> None:
    scores = {**WORKED_EXAMPLE, "risk": None}
    body = client.post("/verdict", json=payload(scores=scores), headers=HEADERS).json()
    risk = next(s for s in body["scores"] if s["dimension"] == "risk")
    assert risk["score"] is None
    assert risk["abstained"] is True
    assert body["missing"] == ["evidence for risk"]


def test_challenge_adjustments_are_reported_per_dimension(client: TestClient) -> None:
    request = payload(scores={**GOOD, "competition": 3.0}, conf={d: 0.7 for d in GOOD})
    request["objections_accepted"] = [{"dimension": "competition", "objection_id": "o1"}]
    body = client.post("/verdict", json=request, headers=HEADERS).json()
    competition = next(s for s in body["scores"] if s["dimension"] == "competition")
    assert competition["score"] == 2.5
    assert competition["adjusted_by_challenge"] == -0.5


def test_whatif_applies_an_explicit_change(client: TestClient) -> None:
    request = {"input": payload(), "change": {"dimension": "competition", "to": 3.0}}
    body = client.post("/whatif", json=request, headers=HEADERS).json()
    assert (body["score"], body["stability"]) == (73.5, "STABLE")


def test_whatif_rejects_an_impossible_change(client: TestClient) -> None:
    request = {"input": payload(), "change": {"profile_field": "team_size", "to": 0}}
    assert client.post("/whatif", json=request, headers=HEADERS).status_code == 422


def test_value_of_information_endpoint(client: TestClient) -> None:
    request = {
        "input": payload(),
        "unknown": {"scope": "score", "field": "feasibility"},
        "range": [1.0, 5.0],
    }
    body = client.post("/value-of-information", json=request, headers=HEADERS).json()
    assert body["verdict_changes"] is True


def test_config_endpoint_exposes_rules_and_versions(client: TestClient) -> None:
    body = client.get("/config", headers=HEADERS).json()
    assert body["versions"]["engine"] == "m11@v1"
    assert body["rules"]["thresholds"]["T_go"] == 70


def test_reload_picks_up_changed_thresholds_and_rejects_invalid_rules(tmp_path: Path) -> None:
    rules = yaml.safe_load(CONFIG_FILE.read_text())
    (tmp_path / "verdict.yaml").write_text(yaml.safe_dump(rules))
    settings = Settings(config_dir=tmp_path, internal_token="tok")
    with TestClient(build_app(settings)) as client:
        assert client.get("/config", headers=HEADERS).json()["rules"]["thresholds"]["T_go"] == 70
        rules["thresholds"]["T_go"] = 60
        (tmp_path / "verdict.yaml").write_text(yaml.safe_dump(rules))
        assert client.post("/reload", headers=HEADERS).status_code == 200
        assert client.get("/config", headers=HEADERS).json()["rules"]["thresholds"]["T_go"] == 60
        rules["weights"]["startup"]["demand_pain"] = 99
        (tmp_path / "verdict.yaml").write_text(yaml.safe_dump(rules))
        assert client.post("/reload", headers=HEADERS).status_code == 422
        assert client.get("/config", headers=HEADERS).json()["rules"]["thresholds"]["T_go"] == 60


def test_invalid_rules_stop_the_service_from_starting(tmp_path: Path) -> None:
    rules = yaml.safe_load(CONFIG_FILE.read_text())
    rules["weights"]["startup"]["demand_pain"] = 99
    (tmp_path / "verdict.yaml").write_text(yaml.safe_dump(rules))
    app = build_app(Settings(config_dir=tmp_path))
    with pytest.raises(ValueError, match="must add to 100"), TestClient(app):
        pass


def test_every_internal_endpoint_is_closed_without_a_token(client: TestClient) -> None:
    body = {"input": payload(), "unknown": {"scope": "score", "field": "risk"}, "range": [1, 2]}
    calls = [
        client.post("/whatif", json={"input": payload()}),
        client.post("/value-of-information", json=body),
        client.post("/reload"),
        client.get("/config"),
    ]
    assert [c.status_code for c in calls] == [401, 401, 401, 401]


def test_endpoints_stay_closed_when_no_token_is_configured() -> None:
    settings = Settings(config_dir=CONFIG_FILE.parent)
    with TestClient(build_app(settings)) as unconfigured:
        assert unconfigured.post("/verdict", json=payload(), headers=HEADERS).status_code == 401
        assert unconfigured.get("/health").status_code == 200


def test_a_non_ascii_token_is_a_401_not_a_500(client: TestClient) -> None:
    headers: Any = {"X-Internal-Token": "tök".encode()}  # raw bytes: not representable as ASCII
    assert client.get("/config", headers=headers).status_code == 401


def test_scores_must_cover_each_dimension_exactly_once(client: TestClient) -> None:
    missing = payload()
    missing["scores"] = missing["scores"][:5]
    duplicate = payload()
    duplicate["scores"] = [*duplicate["scores"][:5], duplicate["scores"][0]]
    for bad in (missing, duplicate):
        assert client.post("/verdict", json=bad, headers=HEADERS).status_code == 422


def test_extra_fields_are_rejected_at_every_level(client: TestClient) -> None:
    nested = payload()
    nested["profile"]["surprise"] = 1
    assert client.post("/verdict", json=nested, headers=HEADERS).status_code == 422
    top = {"input": payload(), "verdict": "GO"}
    assert client.post("/whatif", json=top, headers=HEADERS).status_code == 422


def test_the_same_request_gives_byte_identical_responses(client: TestClient) -> None:
    first = client.post("/verdict", json=payload(), headers=HEADERS).content
    second = client.post("/verdict", json=payload(), headers=HEADERS).content
    assert first == second


def test_whatif_on_a_failed_feasibility_gate_returns_the_hours_needed(client: TestClient) -> None:
    request = {"input": payload(goal="course_project", estimated_effort_hours=300)}
    body = client.post("/whatif", json=request, headers=HEADERS).json()
    assert body["verdict"] == "NO-GO"
    gate = next(c for c in body["what_would_make_go"] if c["dimension"] == "feasibility_gate")
    assert gate["field"] == "hours_per_week"


def test_voi_over_http_for_gate_input_and_profile_unknowns(client: TestClient) -> None:
    gate = {
        "input": payload(),
        "unknown": {"scope": "gate_input", "field": "n_sources"},
        "range": [1, 5],
    }
    assert client.post("/value-of-information", json=gate, headers=HEADERS).json()[
        "verdict_changes"
    ]
    profile = {
        "input": payload(),
        "unknown": {"scope": "profile", "field": "hours_per_week"},
        "range": [20, 40],
    }
    body = client.post("/value-of-information", json=profile, headers=HEADERS).json()
    assert body["verdict_changes"] is False


def test_voi_rejects_an_inverted_range_and_out_of_range_values(client: TestClient) -> None:
    base = {"input": payload(), "unknown": {"scope": "score", "field": "risk"}}
    assert (
        client.post("/value-of-information", json={**base, "range": [4, 1]}, headers=HEADERS)
    ).status_code == 422
    assert (
        client.post("/value-of-information", json={**base, "range": [1, 9]}, headers=HEADERS)
    ).status_code == 422


def test_a_failed_reload_keeps_the_service_answering_with_the_old_rules(tmp_path: Path) -> None:
    rules = yaml.safe_load(CONFIG_FILE.read_text())
    target = tmp_path / "verdict.yaml"
    target.write_text(yaml.safe_dump(rules))
    with TestClient(build_app(Settings(config_dir=tmp_path, internal_token="tok"))) as client:
        target.write_text("- not\n- a mapping\n")
        missing_shape = client.post("/reload", headers=HEADERS)
        target.unlink()
        no_file = client.post("/reload", headers=HEADERS)
        rules["weights"]["hobby"] = rules["weights"]["startup"]
        target.write_text(yaml.safe_dump(rules))
        unknown_goal = client.post("/reload", headers=HEADERS)
        still_works = client.post("/verdict", json=payload(), headers=HEADERS)
    assert missing_shape.status_code == 500
    assert no_file.status_code == 500
    assert unknown_goal.status_code == 422
    assert unknown_goal.json()["error"]["details"]["errors"]
    assert still_works.status_code == 200
