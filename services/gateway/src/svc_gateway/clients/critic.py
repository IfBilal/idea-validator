"""Typed client for M12's input rail (``POST /rail/input``): every user text goes through it first.

docs/harness/13 section 4: "/rail/input on every user text before it reaches any model". The rail
answers ``{allowed, reasons[], sanitized_text}``. A refusal becomes a 422 that carries M12's short
reason codes (fixed vocabulary, safe for a browser); the text that was sent is never echoed back.
When M12 cannot be reached the request is refused (fail closed): an unchecked text must not run.
"""

from typing import Literal

from common.errors import UpstreamError, ValidationFailed
from common.http import ServiceClient

from .upstream import UNEXPECTED, guarded

RailKind = Literal["idea", "answer", "niche"]
MAX_REASONS = 5
MAX_REASON_CHARS = 60


class InputRail:
    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    async def check(self, kind: RailKind, text: str) -> str:
        """The text to use (M12's sanitised copy), or a ``ValidationFailed`` when M12 refuses it."""

        async def call() -> dict[str, object]:
            response = await self._client.request(
                "POST", "/rail/input", json={"text": text, "kind": kind}
            )
            body = response.json()
            if not isinstance(body, dict):
                raise UpstreamError(UNEXPECTED)
            return body

        body = await guarded(call, "input check")
        allowed, clean, reasons = (
            body.get("allowed"),
            body.get("sanitized_text"),
            body.get("reasons"),
        )
        if not isinstance(allowed, bool) or not isinstance(reasons, list):
            raise UpstreamError(UNEXPECTED)
        if not allowed:
            codes = [r[:MAX_REASON_CHARS] for r in reasons if isinstance(r, str)][:MAX_REASONS]
            raise ValidationFailed("this text was refused by the input check", {"reasons": codes})
        if not isinstance(clean, str) or not clean.strip():
            raise UpstreamError(UNEXPECTED)
        return clean
