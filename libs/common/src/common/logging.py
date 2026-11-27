"""structlog JSON logging to stdout with request/run context and secret redaction."""

import logging
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

from .context import parent_step_var, request_id_var, run_id_var
from .redaction import redact


def _add_service(service: str) -> Any:
    def processor(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        event_dict["service"] = service
        return event_dict

    return processor


def _add_context(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    for name, var in (
        ("request_id", request_id_var),
        ("run_id", run_id_var),
        ("parent_step", parent_step_var),
    ):
        value = var.get()
        if value is not None:
            event_dict.setdefault(name, value)
    return event_dict


def _redact(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    return dict(redact(dict(event_dict)))


def configure_logging(service: str, level: str = "INFO") -> None:
    """Call once at service start-up. Logs are JSON lines on stdout (NFR-09)."""
    structlog.configure(
        processors=[
            _add_service(service),
            _add_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            _redact,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(),  # resolves sys.stdout at call time
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
