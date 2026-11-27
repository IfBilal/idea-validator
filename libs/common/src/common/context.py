"""Request-scoped context shared by logging, HTTP client and tracing."""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
parent_step_var: ContextVar[int | None] = ContextVar("parent_step", default=None)

REQUEST_ID_HEADER = "X-Request-Id"
RUN_ID_HEADER = "X-Run-Id"
PARENT_STEP_HEADER = "X-Parent-Step"
INTERNAL_TOKEN_HEADER = "X-Internal-Token"  # noqa: S105 (header name, not a secret)
