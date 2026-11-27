"""Shared plumbing for the typed module clients: JSON decoding and retry accounting."""

from contextvars import ContextVar
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from common.errors import UpstreamError
from common.http import ServiceClient

# Retries used by calls in the current task; the step runner reads it for the trace record.
retries_var: ContextVar[int] = ContextVar("orchestrator_retries", default=0)

M = TypeVar("M", bound=BaseModel)


class BaseClient:
    name = "service"

    def __init__(self, http: ServiceClient) -> None:
        self._http = http

    def bad(self, message: str) -> UpstreamError:
        """A reply that arrived but is unusable. Not retried, so status 200 marks it as such."""
        return UpstreamError(message, {"target": self.name, "status": 200})

    async def _call(
        self, method: str, path: str, body: Any = None, *, http: ServiceClient | None = None
    ) -> Any:
        response = await (http or self._http).request(method, path, json=body)
        retries_var.set(retries_var.get() + int(response.extensions.get("retries", 0)))
        try:
            return response.json()
        except ValueError as exc:
            raise self.bad(f"{self.name} returned a body that is not JSON") from exc

    def _parse(self, model: type[M], data: Any) -> M:
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise self.bad(f"{self.name} returned an invalid {model.__name__}") from exc

    def _object(self, data: Any, what: str) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise self.bad(f"{self.name} returned an invalid {what}: expected an object")
        return data
