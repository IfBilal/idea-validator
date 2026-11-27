"""Client for M5 ``POST /fetch``: the only way this service reaches the internet.

Never opens a connection to anything but ``TOOLS_URL``. M5 applies the allow-list, SSRF guard,
robots.txt, rate limits, cache and the sanitiser; we only translate its answers into scout errors.
"""

from typing import Any

import httpx
from pydantic import ValidationError

from common.context import run_id_var
from common.errors import (
    BudgetExceeded,
    DomainError,
    Forbidden,
    RateLimited,
    UpstreamError,
    UpstreamTimeout,
)
from common.http import ServiceClient
from common.logging import get_logger
from common.schemas import FetchRequest, FetchResponse
from svc_scouts.models.runs import ApiRequest, Page
from svc_scouts.services.errors import (
    BudgetExhausted,
    FetchFailed,
    MalformedPayload,
    SourceRateLimited,
    UnsupportedRequest,
)

log = get_logger("scouts")
MAX_BYTES = 2_000_000


class ToolsClient:
    def __init__(
        self,
        base_url: str,
        *,
        internal_token: str | None,
        timeout_s: float = 40.0,
        max_retries: int = 1,
        forward_auth: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = ServiceClient(
            base_url,
            timeout_s=timeout_s,
            max_retries=max_retries,
            internal_token=internal_token,
            transport=transport,
        )
        self._forward_auth = forward_auth
        self._warned = False

    async def aclose(self) -> None:
        await self._client.aclose()

    def _body(self, request: ApiRequest, source: str) -> dict[str, Any]:
        # No run_id in the body: M5 meters agent runs by run_id (60 pages), a scout meters itself
        # (max_requests_per_run). The id still travels in the X-Run-Id header for tracing.
        base = FetchRequest(
            url=request.url, source=source, purpose="scout", max_bytes=MAX_BYTES
        ).model_dump(mode="json", exclude_none=True)
        needs_forwarding = bool(request.headers) or request.method != "GET" or request.body
        if not needs_forwarding:
            return base
        if self._forward_auth:
            # contract proposed for M5; refused by today's schema (additionalProperties: false)
            return {
                **base,
                "method": request.method,
                "headers": request.headers,
                "body": request.body,
            }
        if request.auth_required or request.method != "GET":
            raise UnsupportedRequest(
                "this source needs POST/auth headers, which M5 /fetch cannot forward yet"
            )
        if not self._warned:  # optional credential (e.g. GitHub token): carry on keyless
            self._warned = True
            log.warning("scout_credential_not_forwarded", reason="M5 /fetch is GET-only")
        return base

    async def fetch(self, request: ApiRequest, *, source: str, run_id: str) -> Page:
        run_id_var.set(run_id)
        body = self._body(request, source)
        try:
            data = await self._client.post_json("/fetch", body)
        except RateLimited as exc:
            raise SourceRateLimited("M5 rate limit", _retry_after(exc)) from exc
        except BudgetExceeded as exc:
            raise BudgetExhausted("M5 run budget used up") from exc
        except Forbidden as exc:
            raise FetchFailed(
                "M5 refused the request (allow-list, robots or disabled)", 403
            ) from exc
        except (UpstreamTimeout, UpstreamError) as exc:
            raise FetchFailed(f"M5 unavailable: {type(exc).__name__}") from exc
        except DomainError as exc:
            raise FetchFailed(f"M5 error {exc.status_code}", exc.status_code) from exc
        try:
            response = FetchResponse.model_validate(data)
        except ValidationError as exc:
            raise MalformedPayload("M5 answered with an unexpected shape") from exc
        return Page(
            status=response.status,
            json_=response.json_,
            text=response.text,
            flagged=response.sanitiser.flagged,
        )


def _retry_after(exc: DomainError) -> int | None:
    value = exc.details.get("retry_after")
    return value if isinstance(value, int) else None
