from datetime import UTC, date, datetime
from typing import Any

import pytest

from ingestion.mappers import Rejected, classify_kind, map_item
from ingestion.models import RawItem

TODAY = date(2026, 3, 1)


def raw(**kw: Any) -> RawItem:
    base: dict[str, Any] = {
        "source": "hackernews",
        "url": "https://example.com/p",
        "title": "A title",
        "text": "Some body text.",
    }
    return RawItem.model_validate(base | kw)


# ---- kind classification (deterministic rules) ----------------------------------------------


@pytest.mark.parametrize(
    ("kw", "kind"),
    [
        ({"source": "github"}, "repo"),
        ({"source": "producthunt"}, "product"),
        ({"source": "yc"}, "product"),
        ({"title": "Show HN: My thing"}, "product"),
        ({"title": "show hn - my thing"}, "product"),
        ({"title": "Launch HN: Acme (YC W24)"}, "product"),
        ({"title": "Ask HN: Is there a tool for tracking invoices?"}, "request"),
        ({"title": "Ask HN: I wish my IDE did X"}, "request"),
        ({"title": "Ask HN: Looking for an alternative to Notion"}, "request"),
        ({"title": "Why I hate invoicing software, so frustrating"}, "complaint"),
        ({"title": "Ask HN: What are you working on?"}, "discussion"),
        ({"source": "reddit", "title": "Is there a tool that does X?"}, "request"),
        ({"source": "reddit", "title": "I built a tool", "metadata": {"flair": "Show"}}, "product"),
        ({"source": "reddit", "title": "Weekly thread"}, "discussion"),
        ({"source": "stackexchange", "title": "How do I parse a CSV?"}, "request"),
        ({"source": "lobsters", "title": "Some article"}, "discussion"),
        ({"title": ""}, "discussion"),
    ],
)
def test_kind_rules(kw: dict[str, Any], kind: str) -> None:
    assert classify_kind(raw(**kw)) == kind


def test_kind_is_deterministic_and_ignores_case_and_unicode_noise() -> None:
    item = raw(title="ASK HN: IS THERE A TOOL FOR — INVOICES?")
    assert classify_kind(item) == classify_kind(item) == "request"


# ---- mapping -----------------------------------------------------------------------------


def test_a_show_hn_post_becomes_a_product_card() -> None:
    card = map_item(
        raw(
            title="Show HN: Invoicer \u2013 invoices for freelancers",
            text="Invoicer sends invoices. It is fast.",
            created_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
            url="https://Invoicer.example.com/?utm_source=hn",
            metadata={"topics": ["Invoicing", "SaaS"], "author": "someone", "email": "a@b.co"},
        ),
        fetched_on=TODAY,
    )
    assert card.name == "Invoicer \u2013 invoices for freelancers"
    assert card.kind == "product"
    assert card.source == "hackernews"
    assert card.url == "https://invoicer.example.com/"
    assert card.published_at == date(2026, 2, 1)
    assert card.fetched_at == TODAY
    assert card.niche_tags == ["invoicing", "saas"]
    assert card.summary == "Invoicer sends invoices."
    assert card.text.startswith(card.name)
    assert len(card.content_hash) == 64


def test_private_data_is_not_carried_into_the_card() -> None:
    card = map_item(
        raw(
            text="Contact me at jane.doe@example.com or ask u/janedoe on /u/other.",
            metadata={"author": "janedoe", "by": "x", "username": "y"},
        ),
        fetched_on=TODAY,
    )
    blob = card.model_dump_json()
    assert "jane.doe@example.com" not in blob
    assert "janedoe" not in blob
    assert "author" not in blob


def test_summary_is_capped_and_cut_cleanly() -> None:
    card = map_item(raw(text="word " * 500), fetched_on=TODAY)
    assert len(card.summary) <= 300


def test_summary_falls_back_to_the_name_when_there_is_no_body() -> None:
    card = map_item(raw(title="Only a title", text=""), fetched_on=TODAY)
    assert card.summary == "Only a title"
    assert card.text == "Only a title"


def test_long_title_and_huge_text_are_bounded() -> None:
    card = map_item(raw(title="t" * 5000, text="x " * 500_000), fetched_on=TODAY)
    assert len(card.name) <= 300
    assert len(card.text) <= 200_000


def test_unicode_survives_and_control_characters_do_not() -> None:
    card = map_item(
        raw(title="日本語 éè \U0001f680", text="ok\x00\x07 text​"),
        fetched_on=TODAY,
    )
    assert "日本語" in card.name and "\U0001f680" in card.name
    assert "\x00" not in card.text and "\x07" not in card.text


def test_complaints_and_requests_carry_the_problem() -> None:
    card = map_item(
        raw(title="Ask HN: Is there a tool for X?", text="I need to do X daily and it hurts."),
        fetched_on=TODAY,
    )
    assert card.kind == "request"
    assert card.problem is not None and "hurts" in card.problem


def test_a_product_has_no_problem_field() -> None:
    assert map_item(raw(source="producthunt"), fetched_on=TODAY).problem is None


def test_a_future_post_date_is_dropped_rather_than_failing_the_card() -> None:
    card = map_item(raw(created_at=date(2030, 1, 1)), fetched_on=TODAY)
    assert card.published_at is None


def test_the_scouts_fetch_date_wins_over_the_run_date() -> None:
    card = map_item(raw(fetched_at=datetime(2026, 2, 20, tzinfo=UTC)), fetched_on=TODAY)
    assert card.fetched_at == date(2026, 2, 20)


def test_tags_are_normalised_deduplicated_and_bounded() -> None:
    tags = ["  AI ", "ai", "x" * 100, "", "a/b c", *[f"t{i}" for i in range(50)]]
    card = map_item(raw(metadata={"topics": tags, "language": "Python"}), fetched_on=TODAY)
    assert len(card.niche_tags) <= 20
    assert len(set(card.niche_tags)) == len(card.niche_tags)
    assert card.niche_tags[0] == "ai"
    assert all(0 < len(t) <= 60 for t in card.niche_tags)


def test_non_string_tags_in_metadata_are_ignored() -> None:
    card = map_item(raw(metadata={"topics": [1, None, {"a": 1}, "ok"]}), fetched_on=TODAY)
    assert card.niche_tags == ["ok"]


def test_github_repo_is_a_repo_card_with_the_source() -> None:
    card = map_item(
        raw(source="github", url="https://github.com/o/r", title="o/r", stars=900),
        fetched_on=TODAY,
    )
    assert (card.kind, card.source) == ("repo", "github")


def test_producthunt_is_launched_and_yc_stage_is_mapped() -> None:
    assert map_item(raw(source="producthunt"), fetched_on=TODAY).stage == "launched"
    yc = map_item(raw(source="yc", metadata={"stage": "seed"}), fetched_on=TODAY)
    assert yc.stage == "seed"
    odd = map_item(raw(source="yc", metadata={"stage": "weird"}), fetched_on=TODAY)
    assert odd.stage == "unknown"


def test_same_input_gives_the_same_card() -> None:
    a = map_item(raw(), fetched_on=TODAY)
    assert a == map_item(raw(), fetched_on=TODAY)


@pytest.mark.parametrize(
    "kw",
    [
        {"url": "not a url"},
        {"url": "ftp://x.com/a"},
        {"title": "", "text": ""},
        {"title": "   ", "text": " \n "},
        {"title": "\x00\x01", "text": "\x02"},
    ],
)
def test_unusable_items_are_rejected_with_a_reason(kw: dict[str, Any]) -> None:
    with pytest.raises(Rejected) as err:
        map_item(raw(**kw), fetched_on=TODAY)
    assert err.value.reason


def test_the_card_validates_against_the_m6_wire_model() -> None:
    from svc_kb.models.cards import CardIn

    card = map_item(raw(created_at=date(2026, 1, 1)), fetched_on=TODAY)
    parsed = CardIn.model_validate(card.wire())
    assert parsed.content_hash == card.content_hash  # the job and M6 hash the text the same way


def test_raw_item_accepts_aliases_and_ignores_unknown_fields() -> None:
    item = RawItem.model_validate(
        {"source": "hn_show", "url": "https://a.com/x", "title": None, "bogus": 1, "stars": 3}
    )
    assert item.source == "hackernews" and item.title == "" and item.popularity == 3


def test_raw_item_rejects_an_unknown_source() -> None:
    with pytest.raises(ValueError, match="unknown source"):
        RawItem.model_validate({"source": "myspace", "url": "https://a.com/x"})
