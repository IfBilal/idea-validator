import pytest

from svc_critic.services.competitors import extract_names, matches_any, names_competitors


def test_extracts_named_products_after_cue_phrases() -> None:
    names = extract_names("Competitors such as Notion and Todoist already exist.")
    assert names == ["Notion", "Todoist"]


def test_extracts_multiword_and_camelcase_names() -> None:
    assert extract_names("Students use Google Docs and GitHub for planning") == [
        "Google Docs",
        "GitHub",
    ]


def test_sentence_initial_words_are_not_names() -> None:
    assert extract_names("Students struggle. Planning is hard.") == []


def test_names_after_a_sentence_boundary_are_still_found_mid_sentence() -> None:
    assert extract_names("It is slow. Many switch to Trello instead.") == ["Trello"]


@pytest.mark.parametrize(
    "text",
    [
        "Posts on Hacker News and Reddit show demand",
        "In March the thread peaked",
        "On Monday users complained",
        "I think the idea is fine and I like it",
        "Product Hunt launches are rare",
    ],
)
def test_sources_dates_and_pronouns_are_not_competitors(text: str) -> None:
    assert extract_names(text) == []


def test_duplicates_are_removed_preserving_order() -> None:
    assert extract_names("like Notion, then Trello, then Notion again") == ["Notion", "Trello"]


@pytest.mark.parametrize(
    ("name", "kb", "expected"),
    [
        ("Notion", ["Notion"], True),
        ("notion", ["NOTION"], True),
        ("Notionn", ["Notion"], True),  # ratio 0.92
        ("Notely", ["Notion"], False),
        ("Trello", ["Notion", "Trello"], True),
        ("Trello", [], False),
        ("Google Docs", ["Google  Docs"], True),
    ],
)
def test_fuzzy_match_against_kb_names(name: str, kb: list[str], expected: bool) -> None:
    assert matches_any(name, kb) is expected


def test_fuzzy_threshold_boundary() -> None:
    # ratio("abcdefghij", "abcdefghix") == 0.9 -> accepted; one more edit -> rejected
    assert matches_any("Abcdefghij", ["Abcdefghix"])
    assert not matches_any("Abcdefghij", ["Abcdefghxx"])


@pytest.mark.parametrize(
    ("text", "dimension", "expected"),
    [
        ("Competitors like Notion exist", None, True),
        ("An alternative is Trello", None, True),
        ("Students want cheaper tools than Notion", "competition", True),
        ("Students use Notion daily", "differentiation", True),
        ("Students use Notion daily", "demand_pain", False),
        ("Students use Notion daily", None, False),
        ("Students are frustrated", "competition", False),
    ],
)
def test_competitor_check_applies_to_cues_or_competition_dimensions(
    text: str, dimension: str | None, expected: bool
) -> None:
    assert bool(names_competitors(text, dimension)) is expected
