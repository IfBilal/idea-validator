"""Postgres checkpointer wiring: URL handling (unit) and crash-safe resume (integration)."""

import uuid

import pytest

from svc_orchestrator.repositories.checkpoints import conninfo_for, postgres_saver, psycopg_url


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql+asyncpg://u:p@db:5432/app", "postgresql://u:p@db:5432/app"),
        ("postgresql+psycopg://u:p@db/app", "postgresql://u:p@db/app"),
        ("postgresql://u:p@db/app", "postgresql://u:p@db/app"),
        ("postgres://u:p@db/app", "postgresql://u:p@db/app"),
    ],
)
def test_sqlalchemy_urls_are_turned_into_plain_libpq_urls(given: str, expected: str) -> None:
    assert psycopg_url(given) == expected


def test_conninfo_pins_the_orchestrator_schema_in_search_path() -> None:
    info = conninfo_for("postgresql+asyncpg://u:p@db:5432/app", "orchestrator")
    assert "search_path=orchestrator" in info
    assert "dbname=app" in info


@pytest.mark.parametrize("bad", ["", "mysql://u@h/db", "not a url"])
def test_non_postgres_urls_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        psycopg_url(bad)


@pytest.mark.parametrize("schema", ["orch; drop table x", "a-b", "", "1abc"])
def test_schema_names_are_validated_to_prevent_injection(schema: str) -> None:
    with pytest.raises(ValueError):
        conninfo_for("postgresql://u:p@db/app", schema)


@pytest.mark.integration
async def test_a_run_paused_on_a_question_survives_a_restart(postgres_url: str) -> None:
    """Pause on interrupt(), throw away every in-memory object, resume from Postgres."""
    from svc_orchestrator.services.validate_graph import build_validate_graph
    from svc_orchestrator.testing import FakeWorld, Harness, fake_verdict, voi_response

    world = FakeWorld()
    world.voi_fn = lambda body: voi_response(True)
    world.verdict_fn = lambda req: fake_verdict(
        req, "NO-GO" if req["gate_inputs"]["estimated_effort_hours"] > 150 else "GO"
    )
    run_id = f"run_pg_{uuid.uuid4().hex[:8]}"
    first = Harness(world, run_id=run_id)
    async with postgres_saver(postgres_url) as saver:
        first.graph = build_validate_graph(first.ctx, saver)
        assert "__interrupt__" in await first.start()
    searches = world.count("kb", "POST /search")

    second = Harness(world, run_id=run_id)  # a new process: nothing but the database remains
    async with postgres_saver(postgres_url) as saver:
        second.graph = build_validate_graph(second.ctx, saver)
        pending = await second.pending()
        assert pending is not None and pending["type"] == "question"
        await second.resume(300)
        state = await second.state()
    assert state.card is not None
    assert state.card["verdict"] == "NO-GO"
    assert world.count("kb", "POST /search") == searches  # nothing before the pause was redone
