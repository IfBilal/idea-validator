"""Production wiring (Redis ledger + events) with a real Redis; modules are not reachable."""

from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from pydantic import SecretStr

from common.config import ConfigError
from svc_orchestrator.config import Settings
from svc_orchestrator.main import _wire, build_app

REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"


async def test_wiring_without_redis_is_a_config_error() -> None:
    async with AsyncExitStack() as stack:
        with pytest.raises(ConfigError, match="REDIS_URL"):
            await _wire(Settings(config_dir=REPO_CONFIG), build_app(Settings()), stack)


@pytest.mark.integration
async def test_wiring_with_redis_builds_a_working_run_service(redis_url: str) -> None:
    settings = Settings(redis_url=SecretStr(redis_url), config_dir=REPO_CONFIG)
    app = build_app(settings)
    async with AsyncExitStack() as stack:
        service = await _wire(settings, app, stack)
        assert await app.state.redis.ping()
        ctx = service._factory("run_wired")
        assert (await ctx.ledger.limits()).max_steps == 80
        await app.state.redis.delete("budget:run_wired")
        await service.aclose()
