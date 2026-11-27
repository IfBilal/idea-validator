"""Card and evidence ids: one scheme shared by ingestion (writes them) and M6 (serves them)."""

import re

import pytest

from common.ids import canonical_url, card_id_for, evidence_id, parse_evidence_id
from common.schemas.types import CardId, EvidenceId  # noqa: F401  (the patterns the ids must satisfy)

CARD = re.compile(r"^card_[0-9a-f]{8}$")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.COM/Path", "https://example.com/Path"),
        ("HTTP://example.com:80/a", "http://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
        ("https://example.com/a?utm_source=x&id=7&fbclid=zz&ref=hn", "https://example.com/a?id=7"),
        ("https://example.com/a?utm_medium=x", "https://example.com/a"),
        ("https://user:pw@example.com/a", "https://example.com/a"),
        ("  https://example.com/a  ", "https://example.com/a"),
        ("https://example.com/a%20b", "https://example.com/a%20b"),
        ("https://www.example.com/a", "https://www.example.com/a"),
    ],
)
def test_canonical_url_removes_everything_that_does_not_identify_the_page(
    raw: str, expected: str
) -> None:
    assert canonical_url(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "ftp://example.com/a",
        "javascript:alert(1)",
        "example.com/a",
        "https:///a",
        "file:///etc/passwd",
    ],
)
def test_canonical_url_refuses_anything_that_is_not_an_http_page(bad: str) -> None:
    with pytest.raises(ValueError, match="url"):
        canonical_url(bad)


def test_url_variants_of_one_page_give_one_card_id() -> None:
    variants = [
        "https://example.com/a",
        "HTTPS://EXAMPLE.com/a/",
        "https://example.com:443/a#top",
        "https://example.com/a?utm_source=newsletter",
    ]
    ids = {card_id_for(v) for v in variants}
    assert len(ids) == 1
    assert CARD.match(ids.pop())


def test_different_pages_give_different_card_ids() -> None:
    assert card_id_for("https://example.com/a") != card_id_for("https://example.com/b")
    assert card_id_for("https://example.com/a?id=1") != card_id_for("https://example.com/a?id=2")


def test_the_card_id_is_stable_across_runs_and_machines() -> None:
    # sha1(b"https://example.com/a").hexdigest()[:8], computed with hashlib; pinned so a change to the
    # scheme (which would orphan every stored card and Qdrant point) is a conscious, reviewed one
    assert card_id_for("https://example.com/a") == "card_c4ed1c21"


def test_evidence_ids_round_trip_and_match_the_schema_pattern() -> None:
    card = card_id_for("https://example.com/a")
    ev = evidence_id(card, 3)
    assert ev == f"{card}#c3"
    assert re.match(r"^card_[0-9a-f]{8,12}#c\d+$", ev)
    assert parse_evidence_id(ev) == (card, 3)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "card_xyz#c1",
        "card_c8ee86d2",
        "card_c8ee86d2#c",
        "card_c8ee86d2#c-1",
        "card_c8ee86d2#c1x",
        "CARD_c8ee86d2#c1",
        "card_c8ee86d2#c1#c2",
    ],
)
def test_parse_evidence_id_rejects_malformed_ids(bad: str) -> None:
    with pytest.raises(ValueError, match="evidence id"):
        parse_evidence_id(bad)


def test_evidence_id_rejects_a_bad_card_id_or_negative_chunk() -> None:
    with pytest.raises(ValueError, match="card id"):
        evidence_id("not-a-card", 0)
    with pytest.raises(ValueError, match="chunk"):
        evidence_id("card_c8ee86d2", -1)
