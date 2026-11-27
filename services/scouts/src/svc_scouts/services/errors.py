"""Failure types of a scout run. All are ``Exception`` subclasses the runner understands."""


class ScoutError(Exception):
    """A source could not be read; counts as one failure for the circuit breaker."""


class FetchFailed(ScoutError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SourceRateLimited(ScoutError):
    """The source or M5 said slow down. The run stops: hammering would make it worse."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class MalformedPayload(ScoutError):
    """The whole payload has the wrong shape (not one bad item: those are dropped and counted)."""


class UnsupportedRequest(ScoutError):
    """The request needs something M5 /fetch cannot do yet (POST body, auth headers)."""


class BudgetExhausted(Exception):
    """The per-run request or item budget is used up. A normal, graceful stop (not a failure)."""


class UnknownSource(Exception):
    pass


class AlreadyRunning(Exception):
    """Single-flight: a run of this source is already in progress in this process."""
