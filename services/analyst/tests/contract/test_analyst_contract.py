"""Contract: M7's feature names are exactly the ones the rubrics (read by M9) expect."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from svc_analyst.models.api import FeaturesResponse
from svc_analyst.models.cards import Card, EvidenceHit

ROOT = Path(__file__).resolve().parents[4]
DOC_09_FEATURES = {
    "n_sources",
    "n_platforms",
    "complaint_count",
    "trend_growth",
    "recency_share",
    "n_competitors",
    "competitor_strength",
    "niche_saturation",
    "max_similarity",
    "unaddressed_gap_count",
    "skill_overlap",
    "available_hours",
    "estimated_effort_hours",
    "compute_fit",
    "risk_flags",
    "platform_dependency",
    "skill_novelty",
    "cv_signal",
}


def test_response_has_every_documented_feature_plus_required_skills() -> None:
    assert set(FeaturesResponse.model_fields) == DOC_09_FEATURES | {"required_skills"}


@pytest.mark.parametrize("rubric", sorted((ROOT / "rubrics").glob("*@v1.yaml")))
def test_every_feature_a_rubric_reads_is_produced(rubric: Path) -> None:
    data: dict[str, Any] = yaml.safe_load(rubric.read_text())
    assert set(data["features"]) <= set(FeaturesResponse.model_fields)


def test_rubric_priors_only_use_produced_names() -> None:
    produced = set(FeaturesResponse.model_fields)
    for rubric in (ROOT / "rubrics").glob("*@v1.yaml"):
        data = yaml.safe_load(rubric.read_text())
        for rule in data["feature_prior"]:
            names = {w for w in _words(rule["when"]) if w in DOC_09_FEATURES}
            assert names <= produced


def _words(expression: str) -> list[str]:
    return expression.replace("(", " ").replace(")", " ").replace("*", " ").split()


def test_card_projection_accepts_a_real_m6_card_with_extra_fields() -> None:
    real: dict[str, Any] = {
        "id": "card_8f21a3b4",
        "name": "Notely",
        "summary": "x",
        "problem": None,
        "customer": "students",
        "niche_tags": ["notes"],
        "stage": "seed",
        "funding_usd": 1000,
        "source": "producthunt",
        "url": "https://example.com",
        "published_at": "2026-01-02",
        "fetched_at": "2026-02-01",
        "text": "body",
        "kind": "product",
        "content_hash": "abc",
        "platforms": ["producthunt"],
    }
    card = Card.model_validate(real)
    assert card.id == "card_8f21a3b4"
    assert card.evidence_id == "card_8f21a3b4#c0"


def test_card_projection_still_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="id"):
        Card.model_validate({"id": "nope", "name": "x", "source": "reddit"})
    with pytest.raises(ValueError, match="source"):
        Card.model_validate({"id": "card_8f21a3b4", "name": "x", "source": "myspace"})


def test_search_hit_ignores_extra_fields_of_m6() -> None:
    hit = EvidenceHit.model_validate(
        {
            "evidence_id": "card_8f21a3b4#c2",
            "card_id": "card_8f21a3b4",
            "source": "reddit",
            "rank": 1,
            "niche": "notes",
            "url": "https://example.com",
        }
    )
    assert hit.quote == ""
