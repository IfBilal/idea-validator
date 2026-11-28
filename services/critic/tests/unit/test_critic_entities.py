"""Hard rule 2: no product or company may be named unless the cited evidence names it too."""

from collections.abc import Sequence

import pytest

from common.schemas import Claim, ClaimCheckRequest
from svc_critic.models.evidence import EvidenceRecord
from svc_critic.services.claims import ClaimChecker
from svc_critic.services.entailment import LexicalOverlapJudge
from svc_critic.services.entities import find_unsupported_entities

TRELLO = "card_aaaaaaaa#c1"
BODY = "Trello charges $10 per user monthly."


class Source:
    def __init__(self, records: dict[str, EvidenceRecord]) -> None:
        self.records = records

    async def fetch_evidence(self, ids: Sequence[str]) -> dict[str, EvidenceRecord]:
        return {i: self.records[i] for i in ids if i in self.records}


def checker(body: str = BODY, name: str | None = "Trello") -> ClaimChecker:
    record = EvidenceRecord(evidence_id=TRELLO, card_name=name, text=body)
    return ClaimChecker(Source({TRELLO: record}), LexicalOverlapJudge())


async def check(text: str, dimension: str | None = None, **kw: str) -> tuple[bool, str]:
    claim = Claim(claim_id="c1", text=text, evidence_ids=[TRELLO], dimension=dimension)
    (r,) = (await checker(**kw).check(ClaimCheckRequest(run_id="r", claims=[claim]))).results
    return r.supported, r.reason


# ---- the reproduced defect, exactly as reported
@pytest.mark.parametrize("dimension", [None, "feasibility", "competition", "demand_pain"])
async def test_a_claim_about_another_product_is_not_supported_by_a_quote_about_trello(
    dimension: str | None,
) -> None:
    supported, reason = await check("Asana charges $10 per user monthly.", dimension)
    assert supported is False
    assert "Asana" in reason


@pytest.mark.parametrize(
    "text",
    [
        "competitors like asana charge $10 per user monthly",
        "Competitors such as Asana charge $10 per user monthly.",
        "alternatives including jira charge $10 per user monthly",
        "Tools versus jira charge $10 per user monthly",
    ],
)
async def test_brands_named_after_a_competition_cue_are_checked_even_in_lowercase(
    text: str,
) -> None:
    supported, reason = await check(text)
    assert supported is False
    assert "not in" in reason


# ---- the same claims about the cited product still pass
@pytest.mark.parametrize(
    "text",
    [
        "Trello charges $10 per user monthly.",
        "trello charges $10 per user monthly",
        "TRELLO charges $10 per user monthly.",
        "Competitors like Trello charge $10 per user monthly.",
    ],
)
async def test_claims_naming_only_the_cited_product_are_supported(text: str) -> None:
    supported, reason = await check(text)
    assert (supported, reason) == (True, "ok")


async def test_a_name_that_appears_in_the_evidence_text_but_not_the_card_name_is_fine() -> None:
    body = "Asana and Trello both charge $10 per user monthly."
    assert await check("Asana charges $10 per user monthly.", body=body, name="Trello") == (
        True,
        "ok",
    )


async def test_every_unknown_name_is_reported() -> None:
    _, reason = await check("Asana and Jira charge $10 per user monthly.")
    assert "Asana" in reason and "Jira" in reason


# ---- ordinary text must not be rejected for capital letters alone
@pytest.mark.parametrize(
    "text",
    [
        "Users pay $10 per user monthly.",
        "Most teams pay $10 per user monthly.",
        "The tool charges $10 per user monthly.",
        "In 2025 Trello charges $10 per user monthly.",
        "Trello charges $10 per user monthly via the API.",
        "On Monday Trello charges $10 per user monthly.",
    ],
)
async def test_common_words_dates_and_acronyms_are_not_mistaken_for_brands(text: str) -> None:
    supported, reason = await check(text)
    assert supported is True, reason


# ---- unit level
def test_the_helper_compares_against_card_names_and_every_evidence_text() -> None:
    assert find_unsupported_entities("Asana is pricey.", ["Trello is cheap."], ["Trello"]) == [
        "Asana"
    ]
    assert find_unsupported_entities("Asana is pricey.", ["Trello is cheap."], ["Asana"]) == []
    assert find_unsupported_entities("Asana is pricey.", ["asana is pricey too."], []) == []


def test_matching_is_whole_word_not_substring() -> None:
    assert find_unsupported_entities("Cal charges more.", ["Calendly charges more."], []) == ["Cal"]


def test_multi_word_names_must_appear_as_a_phrase() -> None:
    assert find_unsupported_entities("Google Docs is free.", ["Docs are free."], []) == [
        "Google Docs"
    ]
    assert find_unsupported_entities("Google Docs is free.", ["Google Docs is free."], []) == []


def test_camel_case_and_mixed_case_brands_count_even_at_the_sentence_start() -> None:
    assert find_unsupported_entities("GitHub is free.", ["Trello is free."], []) == ["GitHub"]
    assert find_unsupported_entities("iPhone sales rose.", ["Sales rose."], []) == ["iPhone"]


def test_the_result_is_deduplicated_and_ordered() -> None:
    claim = "Asana beats Jira, and Asana beats Zoho."
    assert find_unsupported_entities(claim, ["nothing"], []) == ["Asana", "Jira", "Zoho"]


def test_empty_inputs_are_safe() -> None:
    assert find_unsupported_entities("", [], []) == []
    assert find_unsupported_entities("no names here", [], []) == []


# ---- ordinary words that merely start a sentence are not brands (found on the first real run) -----
@pytest.mark.parametrize(
    "text",
    [
        "Effort fits within the available hours.",
        "Detecting API keys in commits is a local check.",
        "Teaches Git hygiene to students.",
        "Requires only a laptop and Python.",
        "Scanning APIs and PDFs is out of scope.",
    ],
)
def test_a_capitalised_ordinary_word_at_the_start_of_a_sentence_is_not_a_name(text: str) -> None:
    evidence = ["a note about hooks, Git and Python"]
    assert find_unsupported_entities(text, evidence, []) == []


@pytest.mark.parametrize("text", ["Asana charges $10.", "Zoom charges $10.", "Notion charges $10."])
def test_a_product_at_the_start_of_a_sentence_is_still_checked(text: str) -> None:
    assert find_unsupported_entities(text, ["Trello charges $10."], ["Trello"]) == [text.split()[0]]


def test_plural_acronyms_are_acronyms() -> None:
    assert find_unsupported_entities("Most APIs and PDFs are fine.", ["x"], []) == []
