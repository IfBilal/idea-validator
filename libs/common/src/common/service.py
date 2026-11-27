"""FastAPI app factory shared by every module: probes, metrics, request ids, error mapping.

Every service gets ``/health`` (liveness), ``/ready`` (real dependency checks) and ``/metrics``
(Prometheus), JSON logs, ``X-Request-Id`` propagation and the standard error envelope
(CLAUDE.md rule 10, docs/harness/05 §0, docs/harness/21 §2).
"""

import hmac
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

from .config import ServiceSettings
from .context import (
    INTERNAL_TOKEN_HEADER,
    REQUEST_ID_HEADER,
    RUN_ID_HEADER,
    request_id_var,
    run_id_var,
)
from .errors import DomainError, ErrorCode, ErrorDetail, ErrorEnvelope, Unauthorized
from .logging import configure_logging, get_logger

ReadyCheck = Callable[[], Awaitable[bool]]


def _envelope_response(status: int, envelope: ErrorEnvelope) -> JSONResponse:
    return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))


def create_app(
    *,
    name: str,
    module: str,
    version: str = "0.1.0",
    settings: ServiceSettings | None = None,
    ready_checks: Mapping[str, ReadyCheck] | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """Build the app. ``ready_checks`` map a dependency name to an async "is it reachable?"."""
    settings = settings or ServiceSettings(service_name=name)
    configure_logging(name, settings.log_level)
    log = get_logger(name)
    checks = dict(ready_checks or {})

    registry = CollectorRegistry()
    requests_total = Counter(
        "http_requests_total",
        "HTTP requests",
        ["service", "method", "path", "status"],
        registry=registry,
    )
    latency = Histogram(
        "http_request_duration_seconds",
        "HTTP latency",
        ["service", "method", "path"],
        registry=registry,
    )

    app = FastAPI(title=f"{module} {name}", version=version, lifespan=lifespan)
    app.state.settings = settings
    app.state.metrics_registry = registry

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:12]}"
        # Not reset afterwards: each request runs in its own task, and the outer 500 handler
        # still needs the id.
        request_id_var.set(request_id)
        run_id_var.set(request.headers.get(RUN_ID_HEADER))
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            path = getattr(request.scope.get("route"), "path", "unmatched")
            latency.labels(name, request.method, path).observe(time.perf_counter() - started)
            requests_total.labels(name, request.method, path, str(status)).inc()
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        response = _envelope_response(exc.status_code, exc.to_envelope(request_id_var.get()))
        retry_after = exc.details.get("retry_after")
        if exc.status_code == 429 and isinstance(retry_after, int):
            response.headers["Retry-After"] = str(retry_after)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        envelope = ErrorEnvelope(
            error=ErrorDetail(
                code=ErrorCode.VALIDATION_ERROR,
                message="request failed validation",
                details={
                    "errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
                },
                request_id=request_id_var.get(),
            )
        )
        return _envelope_response(422, envelope)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error=type(exc).__name__, exc_info=True)
        envelope = ErrorEnvelope(
            error=ErrorDetail(
                code=ErrorCode.INTERNAL, message="internal error", request_id=request_id_var.get()
            )
        )
        return _envelope_response(500, envelope)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": name, "module": module, "version": version}

    @app.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        results: dict[str, bool] = {}
        for dep, check in checks.items():
            try:
                results[dep] = await check()
            except Exception:
                results[dep] = False
        ok = all(results.values())
        body = {"status": "ready" if ok else "not_ready", "checks": results}
        return JSONResponse(status_code=200 if ok else 503, content=body)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    return app


def require_internal_token(settings: ServiceSettings) -> Callable[[Request], None]:
    """Dependency for internal endpoints: ``X-Internal-Token`` must match (docs/harness/13)."""

    def dependency(request: Request) -> None:
        expected = settings.internal_token.get_secret_value() if settings.internal_token else None
        given = request.headers.get(INTERNAL_TOKEN_HEADER, "")
        if expected is None or not hmac.compare_digest(given.encode(), expected.encode()):
            raise Unauthorized("missing or invalid internal token")

    return dependency


__all__ = ["ReadyCheck", "create_app", "require_internal_token"]
