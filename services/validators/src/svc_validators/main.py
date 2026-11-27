from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from common.errors import UpstreamError
from common.logging import get_logger
from common.service import create_app

from .api.routes import make_router
from .clients.scorer import ScorerClient
from .config import Settings, resolve_alpha
from .models.rubric import Rubric
from .services.rubrics import load_rubrics
from .services.runner import ValidationRunner

log = get_logger("validators")


class Runtime:
    """Holds the scorer client and builds the runner once (rubrics are validated at startup)."""

    def __init__(
        self, settings: Settings, scorer_transport: httpx.AsyncBaseTransport | None
    ) -> None:
        self._settings = settings
        self._rubrics: dict[str, Rubric] | None = None
        self._alpha: float | None = None
        self._runner: ValidationRunner | None = None
        self.scorer: ScorerClient | None = None
        if settings.scorer_url:
            self.scorer = ScorerClient(
                settings.scorer_url,
                timeout_s=settings.scorer_timeout_s,
                max_retries=settings.scorer_max_retries,
                internal_token=(
                    settings.internal_token.get_secret_value() if settings.internal_token else None
                ),
                transport=scorer_transport,
            )

    def validate_config(self) -> None:
        """Load and check rubrics + alpha (raises RubricError / ConfigError)."""
        if self._rubrics is None:
            self._rubrics = load_rubrics(self._settings.rubrics_dir, self._settings.rubric_versions)
        if self._alpha is None:
            self._alpha = resolve_alpha(self._settings)

    def runner(self) -> ValidationRunner:
        if self.scorer is None:
            raise UpstreamError("SCORER_URL is not configured")
        if self._runner is None:
            self.validate_config()
            assert self._rubrics is not None and self._alpha is not None
            self._runner = ValidationRunner(
                self.scorer,
                self._rubrics,
                alpha=self._alpha,
                scorer_timeout_s=self._settings.scorer_timeout_s,
            )
        return self._runner

    async def config_ok(self) -> bool:
        try:
            self.validate_config()
        except Exception:
            log.error("validators_config_invalid", exc_info=True)
            return False
        return True

    async def scorer_up(self) -> bool:
        return self.scorer is not None and await self.scorer.ping()


def build_app(
    settings: Settings, *, scorer_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    runtime = Runtime(settings, scorer_transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime.validate_config()  # fail fast in the pod on a bad rubric or alpha
        try:
            yield
        finally:
            if runtime.scorer is not None:
                await runtime.scorer.aclose()

    app = create_app(
        name="validators",
        module="M9",
        settings=settings,
        ready_checks={"config": runtime.config_ok, "scorer": runtime.scorer_up},
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
