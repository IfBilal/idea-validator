"""Typed HTTP client for service-to-service calls: timeouts, bounded retries, trace headers.

Retry policy (docs/harness/07 §5): at most ``max_retries`` retries (default 2) with jittered
exponential back-off, only on timeouts, transport errors, 429, 502, 503, 504. Other 4xx are never
retried. The number of retries used is exposed on ``response.extensions["retries"]``.
"""

import json
from collections.abc import Mapping
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .context import (
    INTERNAL_TOKEN_HEADER,
    PARENT_STEP_HEADER,
    REQUEST_ID_HEADER,
    RUN_ID_HEADER,
    parent_step_var,
    request_id_var,
    run_id_var,
)
from .errors import (
    BudgetExceeded,
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    RateLimited,
    Unauthorized,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
)

RETRY_STATUS = frozenset({429, 502, 503, 504})

_STATUS_ERRORS: dict[int, type[DomainError]] = {
    400: ValidationFailed,
    401: Unauthorized,
    402: BudgetExceeded,
    403: Forbidden,
    404: NotFound,
    409: Conflict,
    422: ValidationFailed,
    429: RateLimited,
}


class _Retryable(Exception):
    def __init__(self, error: DomainError) -> None:
        super().__init__(error.message)
        self.error = error


def _error_for_status(response: httpx.Response, target: str) -> DomainError:
    details = {"target": target, "status": response.status_code}
    cls = _STATUS_ERRORS.get(response.status_code, UpstreamError)
    return cls(f"{target} returned HTTP {response.status_code}", details)


class ServiceClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float,
        max_retries: int = 2,
        internal_token: str | None = None,
        backoff_s: float = 0.2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._max_retries = max_retries
        self._internal_token = internal_token
        self._backoff_s = backoff_s
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_s, transport=transport, follow_redirects=False
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self, extra: Mapping[str, str] | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name, var in ((REQUEST_ID_HEADER, request_id_var), (RUN_ID_HEADER, run_id_var)):
            if (value := var.get()) is not None:
                headers[name] = value
        if (parent := parent_step_var.get()) is not None:
            headers[PARENT_STEP_HEADER] = str(parent)
        if self._internal_token:
            headers[INTERNAL_TOKEN_HEADER] = self._internal_token
        headers.update(extra or {})
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        retrying = AsyncRetrying(
            retry=retry_if_exception_type(_Retryable),
            stop=stop_after_attempt(self._max_retries + 1),
            wait=wait_exponential_jitter(
                multiplier=self._backoff_s, max=2.0, jitter=self._backoff_s
            ),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    response = await self._attempt(method, path, json, params, headers)
                    response.extensions["retries"] = attempt.retry_state.attempt_number - 1
                    return response
        except _Retryable as exc:
            raise exc.error from None
        raise UpstreamError(f"{self._base_url} unreachable")  # pragma: no cover

    async def _attempt(
        self,
        method: str,
        path: str,
        json: Any,
        params: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method, path, json=json, params=params, headers=self._headers(headers)
            )
        except httpx.TimeoutException as exc:
            raise _Retryable(UpstreamTimeout(f"{self._base_url}{path} timed out")) from exc
        except httpx.TransportError as exc:
            raise _Retryable(UpstreamError(f"{self._base_url}{path} unreachable")) from exc
        if response.status_code in RETRY_STATUS:
            raise _Retryable(_error_for_status(response, self._base_url))
        if response.status_code >= 400:
            raise _error_for_status(response, self._base_url)
        return response

    async def get_json_capped(
        self,
        path: str,
        *,
        max_bytes: int,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """GET and parse JSON, reading at most ``max_bytes``: one attempt, never retried.

        A declared ``Content-Length`` over the cap is refused before any byte is read; otherwise
        the body is abandoned as soon as the running count passes the cap.
        """
        try:
            async with self._client.stream(
                "GET", path, params=params, headers=self._headers(headers)
            ) as response:
                if response.status_code >= 400:
                    raise _error_for_status(response, self._base_url)
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise UpstreamError(f"{self._base_url} answer exceeds {max_bytes} bytes")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise UpstreamError(f"{self._base_url} answer exceeds {max_bytes} bytes")
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout(f"{self._base_url}{path} timed out") from exc
        except httpx.TransportError as exc:
            raise UpstreamError(f"{self._base_url}{path} unreachable") from exc
        return json.loads(body)

    async def get_json(self, path: str, **kwargs: Any) -> Any:
        return (await self.request("GET", path, **kwargs)).json()

    async def post_json(self, path: str, body: Any, **kwargs: Any) -> Any:
        return (await self.request("POST", path, json=body, **kwargs)).json()
