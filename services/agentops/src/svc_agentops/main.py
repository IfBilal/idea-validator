from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from common.limits import BodyLimitMiddleware
from common.service import create_app

from .api.routes import make_router
from .config import Settings
from .repositories.drift_store import DriftStore
from .repositories.files import JsonlTraceFiles
from .repositories.index import PgTraceIndex
from .services.ingest import IngestService
from .services.metrics import TraceMetrics
from .services.ports import TraceFiles, TraceIndex
from .services.query import QueryService
from .services.registry import RegistryService


def build_app(
    settings: Settings, *, index: TraceIndex | None = None, files: TraceFiles | None = None
) -> FastAPI:
    """Without a database (and no injected index) the service starts but is not ready."""
    engine: AsyncEngine | None = None
    if index is None and settings.database_url:
        engine = create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
        index = PgTraceIndex(engine)
    store: TraceFiles = files or JsonlTraceFiles(settings.trace_dir)

    async def index_ready() -> bool:
        return index is not None and await index.ping()

    async def files_ready() -> bool:
        return store.writable() and store.free_bytes() >= settings.min_free_bytes

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        with suppress(OSError):  # /ready reports an unwritable directory
            store.prepare()
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    app = create_app(
        name="agentops",
        module="M13",
        settings=settings,
        ready_checks={"database": index_ready, "trace_dir": files_ready},
        lifespan=lifespan,
    )
    if index is not None:
        app.state.ingest = IngestService(
            index,
            store,
            max_batch=settings.max_batch,
            max_record_bytes=settings.max_record_bytes,
            metrics=TraceMetrics(app.state.metrics_registry),
            max_run_bytes=settings.max_run_bytes,
            max_run_records=settings.max_run_records,
            min_free_bytes=settings.min_free_bytes,
        )
        app.state.query = QueryService(
            index,
            store,
            max_run_records=settings.max_run_records,
            default_limit=settings.runs_default_limit,
            max_limit=settings.runs_max_limit,
        )
    app.state.drift = DriftStore(settings.drift_dir or settings.trace_dir / "drift")
    app.state.registry = RegistryService(settings.registry_dir)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
