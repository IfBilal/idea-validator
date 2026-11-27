import pytest
from pydantic import SecretStr

from svc_scouts.cli import run_cli
from svc_scouts.config import Settings


async def test_an_unknown_source_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli(["--source", "myspace"], Settings()) == 2
    assert "unknown source" in capsys.readouterr().err


@pytest.mark.parametrize("limit", ["0", "-3"])
async def test_a_non_positive_limit_is_a_usage_error(limit: str) -> None:
    assert await run_cli(["--source", "hn", "--limit", limit], Settings()) == 2


async def test_the_source_argument_is_required() -> None:
    with pytest.raises(SystemExit) as caught:
        await run_cli([], Settings())
    assert caught.value.code == 2


async def test_a_non_integer_limit_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit):
        await run_cli(["--source", "hn", "--limit", "many"], Settings())


async def test_missing_database_or_tools_url_is_a_config_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await run_cli(["--source", "hn"], Settings()) == 2
    assert "DATABASE_URL and TOOLS_URL" in capsys.readouterr().err
    only_db = Settings(database_url=SecretStr("postgresql+asyncpg://x/y"))
    assert await run_cli(["--source", "hn"], only_db) == 2


async def test_missing_sources_config_is_a_config_error(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://x/y"),
        tools_url="http://tools:8000",
        config_dir=tmp_path,
    )
    assert await run_cli(["--source", "hn"], settings) == 2
    assert "config file not found" in capsys.readouterr().err
