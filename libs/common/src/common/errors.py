"""Domain errors and the standard error JSON (docs/harness/05 §0). Never leak stack traces."""

from enum import StrEnum
from typing import Any

from pydantic import Field

from .schemas.base import Message


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"


class ErrorDetail(Message):
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorEnvelope(Message):
    error: ErrorDetail


class DomainError(Exception):
    """Base class: services raise these, one exception handler maps them to HTTP."""

    code: ErrorCode = ErrorCode.INTERNAL
    status_code: int = 500

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_envelope(self, request_id: str | None = None) -> ErrorEnvelope:
        return ErrorEnvelope(
            error=ErrorDetail(
                code=self.code, message=self.message, details=self.details, request_id=request_id
            )
        )


class ValidationFailed(DomainError):
    code, status_code = ErrorCode.VALIDATION_ERROR, 422


class Unauthorized(DomainError):
    code, status_code = ErrorCode.UNAUTHORIZED, 401


class Forbidden(DomainError):
    code, status_code = ErrorCode.FORBIDDEN, 403


class NotFound(DomainError):
    code, status_code = ErrorCode.NOT_FOUND, 404


class Conflict(DomainError):
    code, status_code = ErrorCode.CONFLICT, 409


class RateLimited(DomainError):
    code, status_code = ErrorCode.RATE_LIMITED, 429


class BudgetExceeded(DomainError):
    code, status_code = ErrorCode.BUDGET_EXCEEDED, 402


class UpstreamTimeout(DomainError):
    code, status_code = ErrorCode.UPSTREAM_TIMEOUT, 504


class UpstreamError(DomainError):
    code, status_code = ErrorCode.UPSTREAM_ERROR, 502
