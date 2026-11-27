"""M6 card storage on a real Postgres: ids, idempotent upserts, collisions, chunks, stats, migration."""

import asyncio
import hashlib
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.ids import canonical_url, card_id_for
from svc_kb.models.cards import CardIn
from svc_kb.repositories.cards import CardRepository
from svc_kb.services.chunking import chunk_text

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 27)

Sessions = async_sessionmaker[AsyncSession]


def card(url: str = "https://example.com/a", **changes: object) -> CardIn:
    base: dict[str, object] = {
        "name": "Dockerfile linter",
        "summary": "A CLI that lints Dockerfiles.",
        "source": "hackernews",
        "kind": "product",
        "url": url,
        "fetched_at": TODAY,
        "published_at": TODAY - timedelta(days=3),
        "text": "A CLI that lints Dockerfiles. Exporting reports is slow on big repos. Pricing is fair.",
        "niche_tags": ["devtools"],
    }
    return CardIn.model_validate({**base, **changes})


async def upsert(sessions: Sessions, c: CardIn, **kw: object):  # type: ignore[no-untyped-def]
    async with sessions() as session, session.begin():
        repo = CardRepository(session, **kw)  # type: ignore[arg-type]
        return await repo.upsert(c, chunk_text("card_00000000", c.text))


async def test_a_new_card_gets_an_id_from_its_canonical_url(sessions: Sessions) -> None:
    result = await upsert(sessions, card())
    assert result.created and result.changed
    assert result.card_id == card_id_for("https://example.com/a")
    async with sessions() as session:
        got = await CardRepository(session).get(result.card_id)
    assert got is not None and got.name == "Dockerfile linter" and got.platforms == ["hackernews"]
    assert got.url == canonical_url("https://example.com/a")
    assert got.content_hash and got.first_seen_at == got.last_seen_at


async def test_seeing_the_same_page_again_is_idempotent_even_via_a_tracking_url(
    sessions: Sessions,
) -> None:
    first = await upsert(sessions, card("https://example.com/a"))
    again = await upsert(sessions, card("HTTPS://Example.com/a/?utm_source=newsletter#top"))
    assert again.card_id == first.card_id
    assert not again.created and not again.changed
    async with sessions() as session:
        _, total = await CardRepository(session).list(limit=10, offset=0)
        got = await CardRepository(session).get(first.card_id)
    assert total == 1
    assert got is not None and got.last_seen_at > got.first_seen_at, "the sighting is recorded"


async def test_changed_text_updates_the_card_and_replaces_its_chunks(sessions: Sessions) -> None:
    first = await upsert(sessions, card())
    async with sessions() as session:
        old = await CardRepository(session).evidence([f"{first.card_id}#c0"])
    assert old
    new_text = "Completely different text about retrieval latency. " * 5
    changed = await upsert(sessions, card(text=new_text, summary="Different."))
    assert changed.card_id == first.card_id and changed.changed and not changed.created
    async with sessions() as session:
        repo = CardRepository(session)
        got = await repo.get(first.card_id)
        rows = await repo.evidence([f"{first.card_id}#c0"])
    assert got is not None and got.summary == "Different."
    assert rows and "retrieval latency" in rows[0].text and "Dockerfiles" not in rows[0].text


async def test_a_crafted_url_that_collides_cannot_overwrite_a_legitimate_card(
    sessions: Sessions,
) -> None:
    def forged(url: str) -> str:
        return "deadbeef" + hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()

    legit = await upsert(sessions, card("https://example.com/legit"), digest=forged)
    evil = await upsert(
        sessions,
        card("https://evil.example/crafted", name="Crafted", text="evil text"),
        digest=forged,
    )
    assert legit.card_id == "card_deadbeef"
    assert evil.card_id != legit.card_id and evil.created
    assert len(evil.card_id) == len("card_") + 10
    async with sessions() as session:
        repo = CardRepository(session)
        original = await repo.get(legit.card_id)
    assert original is not None
    assert original.name == "Dockerfile linter" and original.url == "https://example.com/legit"


async def test_parallel_upserts_of_one_page_make_exactly_one_card(sessions: Sessions) -> None:
    results = await asyncio.gather(
        *(upsert(sessions, card("https://example.com/race")) for _ in range(12))
    )
    assert sum(r.created for r in results) == 1
    assert len({r.card_id for r in results}) == 1
    async with sessions() as session:
        _, total = await CardRepository(session).list(limit=10, offset=0)
    assert total == 1


async def test_get_many_keeps_the_requested_order_and_skips_missing_ids(sessions: Sessions) -> None:
    a = await upsert(sessions, card("https://example.com/a"))
    b = await upsert(sessions, card("https://example.com/b"))
    async with sessions() as session:
        repo = CardRepository(session)
        got = await repo.get_many([b.card_id, "card_00000000", a.card_id])
        none = await repo.get_many([])
    assert [c.id for c in got] == [b.card_id, a.card_id]
    assert none == []


async def test_list_pages_are_stable_and_report_the_total(sessions: Sessions) -> None:
    for i in range(7):
        await upsert(sessions, card(f"https://example.com/p{i}"))
    async with sessions() as session:
        repo = CardRepository(session)
        page1, total = await repo.list(limit=3, offset=0)
        page2, _ = await repo.list(limit=3, offset=3)
        page3, _ = await repo.list(limit=3, offset=6)
        beyond, _ = await repo.list(limit=3, offset=50)
    ids = [c.id for c in page1 + page2 + page3]
    assert total == 7 and len(ids) == 7 and len(set(ids)) == 7
    assert ids == sorted(ids), "ordered by id so paging is stable"
    assert beyond == []


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (-1, 0), (501, 0), (10, -1)])
async def test_list_refuses_unreasonable_paging(
    sessions: Sessions, limit: int, offset: int
) -> None:
    async with sessions() as session:
        with pytest.raises(ValueError, match=r"limit|offset"):
            await CardRepository(session).list(limit=limit, offset=offset)


async def test_evidence_rows_join_the_chunk_to_its_card(sessions: Sessions) -> None:
    r = await upsert(sessions, card(name="Linty", source="github", kind="repo"))
    async with sessions() as session:
        rows = await CardRepository(session).evidence(
            [f"{r.card_id}#c0", "card_00000000#c0", f"{r.card_id}#c99"]
        )
    assert len(rows) == 1
    row = rows[0]
    assert (row.card_id, row.card_name, row.source, row.kind) == (
        r.card_id,
        "Linty",
        "github",
        "repo",
    )
    assert row.evidence_id == f"{r.card_id}#c0" and "Dockerfiles" in row.text


async def test_chunks_keep_the_card_id_of_the_card_they_belong_to(sessions: Sessions) -> None:
    """The caller's placeholder card id must never leak into the stored chunk ids."""
    r = await upsert(sessions, card())
    async with sessions() as session:
        result = await session.execute(text("SELECT id, card_id FROM kb.chunks"))
        rows = result.all()
    assert rows and all(cid == r.card_id and eid.startswith(f"{r.card_id}#c") for eid, cid in rows)


async def test_stats_count_sources_kinds_freshness_and_the_snapshot_date(
    sessions: Sessions,
) -> None:
    await upsert(sessions, card("https://example.com/1", source="hackernews", kind="product"))
    await upsert(
        sessions,
        card(
            "https://example.com/2",
            source="github",
            kind="repo",
            published_at=TODAY - timedelta(days=400),
        ),
    )
    await upsert(
        sessions,
        card(
            "https://example.com/3",
            source="github",
            kind="complaint",
            fetched_at=TODAY + timedelta(days=1),
            published_at=None,
        ),
    )
    async with sessions() as session:
        stats = await CardRepository(session).stats(today=TODAY)
    assert stats.cards == 3 and stats.chunks >= 3
    assert stats.by_source == {"github": 2, "hackernews": 1}
    assert stats.by_kind == {"complaint": 1, "product": 1, "repo": 1}
    assert set(stats.last_ingested_at) == {"github", "hackernews"}
    assert stats.snapshot_date == TODAY + timedelta(days=1), (
        "the snapshot date is the newest fetch date"
    )
    assert stats.fresh_90d_share == pytest.approx(1 / 2), (
        "of the cards that have a date, 1 of 2 is under 90 days old"
    )


async def test_stats_of_an_empty_knowledge_base_are_zeroes_not_errors(sessions: Sessions) -> None:
    async with sessions() as session:
        stats = await CardRepository(session).stats(today=TODAY)
    assert (stats.cards, stats.chunks, stats.by_source, stats.snapshot_date) == (0, 0, {}, None)
    assert stats.fresh_90d_share == 0.0


async def test_the_database_itself_refuses_unknown_sources_and_kinds(sessions: Sessions) -> None:
    await upsert(sessions, card())
    async with sessions() as session, session.begin():
        with pytest.raises(DBAPIError):
            await session.execute(text("UPDATE kb.cards SET source = 'myspace'"))
    async with sessions() as session, session.begin():
        with pytest.raises(DBAPIError):
            await session.execute(text("UPDATE kb.cards SET kind = 'rant'"))


async def test_the_generated_keyword_column_finds_cards_by_their_words(sessions: Sessions) -> None:
    r = await upsert(sessions, card())
    async with sessions() as session:
        found = await session.execute(
            text(
                "SELECT id FROM kb.cards WHERE tsv @@ plainto_tsquery('english', 'exporting reports')"
            )
        )
        missing = await session.execute(
            text("SELECT id FROM kb.cards WHERE tsv @@ plainto_tsquery('english', 'quantum')")
        )
    assert [row[0] for row in found] == [r.card_id] and missing.all() == []


async def test_the_migration_goes_down_and_up_cleanly(migrated_url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["DATABASE_URL"] = migrated_url
    try:
        await asyncio.to_thread(command.downgrade, config, "base")
        await asyncio.to_thread(command.upgrade, config, "head")
    finally:
        del os.environ["DATABASE_URL"]
