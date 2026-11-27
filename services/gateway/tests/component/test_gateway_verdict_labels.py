"""Verdict labels are a closed set, and a stored card must agree with its row (never guessed)."""

import uuid
from typing import Any

import pytest
from test_gateway_runs_api import ALICE, Env, build

from svc_gateway.models.ideas import CompareRow, IdeaSummary, VersionSummary

LABELS = ["GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"]
NOW = "2026-01-01T12:00:00+00:00"


@pytest.fixture
def env() -> Any:
    e = build()
    with e.client:
        yield e


def version(e: Env, verdict: str | None, card: dict[str, Any] | None) -> tuple[str, Any]:
    idea = e.m2.add_idea(ALICE, card=card, verdict=verdict, score=70.0 if verdict else None)
    return idea, e.client.get(f"/ideas/{idea}/versions/1", headers=e.headers())


@pytest.mark.parametrize("label", LABELS)
def test_every_real_label_passes_through_unchanged(env: Env, label: str) -> None:
    _, response = version(env, label, {"verdict": label, "claims": []})
    assert response.status_code == 200
    assert response.json()["verdict"] == label
    assert response.json()["card"]["verdict"] == label


@pytest.mark.parametrize("label", ["MAYBE", "go", "Go", "NO_GO", "PASS", "", "GO "])
def test_an_unknown_label_is_a_generic_502_on_every_read(env: Env, label: str) -> None:
    idea = env.m2.add_idea(ALICE, verdict=label, score=70.0)
    other = env.m2.add_idea(ALICE)
    paths = [
        f"/ideas/{idea}/versions/1",
        f"/ideas/{idea}/versions",
        "/ideas",
        f"/compare?ids={idea},{other}",
    ]
    for path in paths:
        response = env.client.get(path, headers=env.headers())
        assert response.status_code == 502, path
        assert response.json()["error"]["code"] == "UPSTREAM_ERROR"
        assert label not in response.text or label == ""


@pytest.mark.parametrize(
    ("top", "card"),
    [
        ("GO", "NO-GO"),
        ("PIVOT", "GO"),
        ("NO-GO", None),  # the card says "no verdict", the row says there is one
        (None, "GO"),  # the card has a verdict, the row has none
        ("GO", "MAYBE"),
    ],
)
def test_a_card_that_disagrees_with_its_row_is_a_502_never_a_guess(
    env: Env, top: str | None, card: str | None
) -> None:
    idea = env.m2.add_idea(ALICE, card={"verdict": card}, verdict=top, score=70.0)
    response = env.client.get(f"/ideas/{idea}/versions/1", headers=env.headers())
    assert response.status_code == 502
    assert response.json()["error"]["message"] == "the service returned an unexpected answer"
    assert "GO" not in response.text and "PIVOT" not in response.text


def test_a_card_without_a_verdict_key_or_a_null_card_is_fine(env: Env) -> None:
    _, no_key = version(env, "GO", {"claims": []})
    _, none = version(env, None, None)
    _, partial = version(env, None, {"verdict": None, "partial": True})
    assert (no_key.status_code, none.status_code, partial.status_code) == (200, 200, 200)
    assert partial.json()["card"] == {"verdict": None, "partial": True}


def test_a_non_object_card_is_a_502(env: Env) -> None:
    idea = env.m2.add_idea(ALICE, verdict="GO", score=1.0)
    env.m2.versions[idea][0]["verdict_payload"] = ["GO"]
    assert env.client.get(f"/ideas/{idea}/versions/1", headers=env.headers()).status_code == 502


def test_compare_verdicts_must_be_labels_or_null(env: Env) -> None:
    a = env.m2.add_idea(ALICE, verdict="GO", score=80.0)
    b = env.m2.add_idea(ALICE)
    body = env.client.get(f"/compare?ids={a},{b}", headers=env.headers()).json()
    assert body["verdicts"] == {a: "GO", b: None}


@pytest.mark.parametrize("model", [IdeaSummary, VersionSummary, CompareRow])
def test_the_models_accept_none_and_reject_strangers(model: Any) -> None:
    base: dict[str, Any] = {
        IdeaSummary: {
            "idea_id": str(uuid.uuid4()),
            "title": "t",
            "source": "own",
            "latest_version": 1,
            "score": None,
            "updated_at": NOW,
        },
        VersionSummary: {
            "version": 1,
            "text": "t",
            "change_note": None,
            "parent_version": None,
            "created_at": NOW,
            "score": None,
            "stability": None,
        },
        CompareRow: {
            "idea_id": str(uuid.uuid4()),
            "title": "t",
            "version": 1,
            "stability": None,
            "score": None,
            "confidence": None,
            "scores": {},
        },
    }[model]
    field = "latest_verdict" if model is IdeaSummary else "verdict"
    assert model.model_validate({**base, field: None})
    assert model.model_validate({**base, field: "NO-GO"})
    with pytest.raises(ValueError, match=field):
        model.model_validate({**base, field: "SHIP-IT"})
