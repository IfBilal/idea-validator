"""Typed client for M6 (knowledge base): evidence existence + text for claim checking."""

from collections.abc import Sequence

import httpx
from pydantic import TypeAdapter, ValidationError

from common.errors import UpstreamError
from common.http import ServiceClient

from ..models.evidence import EvidenceRecord

BATCH_SIZE = 100
_ITEMS = TypeAdapter(list[EvidenceRecord])


class KnowledgeBaseClient:
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
        self._http = ServiceClient(
            base_url,
            timeout_s=timeout_s,
            max_retries=max_retries,
            internal_token=internal_token,
            backoff_s=backoff_s,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch_evidence(self, ids: Sequence[str]) -> dict[str, EvidenceRecord]:
        """Existing evidence chunks by id; ids unknown to M6 are simply absent from the result."""
        unique = list(dict.fromkeys(ids))
        found: dict[str, EvidenceRecord] = {}
        for start in range(0, len(unique), BATCH_SIZE):
            batch = unique[start : start + BATCH_SIZE]
            for record in await self._fetch_batch(batch):
                if record.evidence_id in batch:
                    found[record.evidence_id] = record
        return found

    async def _fetch_batch(self, ids: list[str]) -> list[EvidenceRecord]:
        try:
            payload = await self._http.post_json("/evidence/batch", {"ids": ids})
        except ValueError as exc:  # body was not JSON
            raise UpstreamError("knowledge base returned a malformed response") from exc
        try:
            return _ITEMS.validate_python(payload["items"])
        except (KeyError, TypeError, ValidationError) as exc:
            raise UpstreamError("knowledge base returned a malformed response") from exc

    async def is_ready(self) -> bool:
        try:
            await self._http.request("GET", "/ready")
        except Exception:
            return False
        return True


class UnconfiguredKnowledgeBase:
    """Stand-in when ``KB_URL`` is not set: every call fails cleanly, readiness is false."""

    async def aclose(self) -> None:
        return None

    async def fetch_evidence(self, ids: Sequence[str]) -> dict[str, EvidenceRecord]:
        raise UpstreamError("knowledge base is not configured")

    async def is_ready(self) -> bool:
        return False
