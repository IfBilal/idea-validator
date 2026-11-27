"""M6 Knowledge Base client (docs/harness/05 M6).

Assumed shapes:
- ``POST /search`` ``{query, k, filters{kinds?}, mode:"hybrid", rerank:true, run_id}`` ->
  ``{items:[EvidenceItem], index_version}``. For ``kinds:["product"]`` the item ``quote`` holds the
  product's feature text and ``card_name`` its name (used by the anti-copy check).
- ``POST /evidence/batch`` ``{ids[]}`` -> ``{items:[EvidenceItem], missing:[id]}``.
- ``GET /evidence/{evidence_id}`` -> EvidenceItem, 404 when unknown.
"""

from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from common.errors import NotFound, UpstreamError
from common.http import ServiceClient
from common.schemas import EvidenceItem


def _items(payload: Any) -> list[EvidenceItem]:
    try:
        return [EvidenceItem.model_validate(raw) for raw in payload["items"]]
    except (KeyError, TypeError, ValidationError) as exc:
        raise UpstreamError("M6 returned a malformed evidence payload") from exc


class KnowledgeBaseClient:
    def __init__(self, http: ServiceClient) -> None:
        self._http = http

    async def search(
        self, query: str, *, k: int, run_id: str, kinds: list[str] | None = None
    ) -> list[EvidenceItem]:
        filters: dict[str, Any] = {}
        if kinds:
            filters["kinds"] = kinds
        body = {
            "query": query,
            "k": k,
            "filters": filters,
            "mode": "hybrid",
            "rerank": True,
            "run_id": run_id,
        }
        return _items(await self._http.post_json("/search", body))

    async def existing_evidence(self, ids: list[str]) -> dict[str, EvidenceItem]:
        """Which of ``ids`` exist in M6, with their text. No call when there is nothing to ask."""
        unique = list(dict.fromkeys(ids))
        if not unique:
            return {}
        payload = await self._http.post_json("/evidence/batch", {"ids": unique})
        return {item.evidence_id: item for item in _items(payload)}

    async def get_evidence(self, evidence_id: str) -> EvidenceItem | None:
        try:
            payload = await self._http.get_json(f"/evidence/{quote(evidence_id, safe='')}")
        except NotFound:
            return None
        try:
            return EvidenceItem.model_validate(payload)
        except ValidationError as exc:
            raise UpstreamError("M6 returned a malformed evidence item") from exc
