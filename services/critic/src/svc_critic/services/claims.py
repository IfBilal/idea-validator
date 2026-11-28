"""Claim checking (docs/harness/09 §3): existence, competitor rule, support + verbatim quote.

Deterministic checks run first; the entailment judge only runs when they pass. The checker only
marks supported / unsupported with a reason. It never edits a claim and never touches a verdict.
"""

import re
from collections.abc import Sequence
from typing import Protocol

from common.errors import DomainError, ValidationFailed
from common.logging import get_logger
from common.schemas import Claim, ClaimCheckRequest, ClaimCheckResponse, ClaimResult

from ..models.evidence import EvidenceRecord
from .budget import request_scope
from .competitors import matches_any, names_competitors
from .entailment import REASON_NO_SUPPORT, EntailmentJudge
from .entities import find_unsupported_entities

# exactly what the validators runner appends: " [model 4.0; prior unavailable: <why>]" or
# " [model 4.0, prior 3.0, alpha 0.5]"; any other bracket is claim content
_NUM = r"\d+(?:\.\d+)?"
_ANNOTATION = re.compile(
    rf"\s*\[model {_NUM}(?:; prior unavailable: [^\]]*|, prior {_NUM}, alpha {_NUM})\]\s*$"
)
REASON_MISSING = "evidence id missing"
REASON_NO_EVIDENCE = "no evidence cited"
REASON_COMPETITOR = "competitor not in knowledge base"
REASON_ENTITY = "name not in cited evidence"
REASON_JUDGE_FAILED = "support check failed"

log = get_logger("critic.claims")


class EvidenceSource(Protocol):
    async def fetch_evidence(self, ids: Sequence[str]) -> dict[str, EvidenceRecord]: ...


class ClaimChecker:
    def __init__(
        self,
        source: EvidenceSource,
        judge: EntailmentJudge,
        *,
        max_claims: int = 50,
        max_quote_chars: int = 200,
    ) -> None:
        self._source = source
        self._judge = judge
        self._max_claims = max_claims
        self._max_quote = max_quote_chars

    async def check(self, request: ClaimCheckRequest) -> ClaimCheckResponse:
        return ClaimCheckResponse(results=await self.check_claims(request.claims))

    async def check_claims(self, claims: Sequence[Claim]) -> list[ClaimResult]:
        if len(claims) > self._max_claims:
            raise ValidationFailed(f"at most {self._max_claims} claims per request")
        ids = [c.claim_id for c in claims]
        if len(set(ids)) != len(ids):
            raise ValidationFailed("claim_id values must be unique within a request")
        wanted = list(dict.fromkeys(e for c in claims for e in c.evidence_ids))
        found = await self._source.fetch_evidence(wanted) if wanted else {}
        with request_scope():  # one LLM-judge budget for the whole request, shared by every claim
            results = [await self._check_one(c, found) for c in claims]
        log.info(
            "claims_checked",
            total=len(results),
            supported=sum(r.supported for r in results),
            reasons=sorted({r.reason for r in results if not r.supported}),
        )
        return results

    async def _check_one(self, claim: Claim, found: dict[str, EvidenceRecord]) -> ClaimResult:
        cited = list(dict.fromkeys(claim.evidence_ids))

        def fail(reason: str) -> ClaimResult:
            return ClaimResult(
                claim_id=claim.claim_id,
                supported=False,
                reason=reason,
                supporting_quote=None,
                checked_evidence_ids=cited,
            )

        if not cited:
            return fail(REASON_NO_EVIDENCE)
        if any(e not in found for e in cited):
            return fail(REASON_MISSING)
        records = [found[e] for e in cited]

        text = _ANNOTATION.sub("", claim.text)  # check the claim, not the annotation M9 appended
        names = names_competitors(text, claim.dimension)
        kb_names = [r.card_name for r in records if r.card_name]
        unknown = [n for n in names if not matches_any(n, kb_names)]
        if unknown:
            return fail(f"{REASON_COMPETITOR}: {', '.join(unknown)}")

        unnamed = find_unsupported_entities(text, (r.body for r in records), kb_names)
        if unnamed:
            return fail(f"{REASON_ENTITY}: {', '.join(unnamed)}")

        try:
            verdict = await self._judge.judge(text, records)
        except DomainError:
            raise
        except Exception as exc:
            log.error("judge_failed", claim_id=claim.claim_id, error=type(exc).__name__)
            return fail(REASON_JUDGE_FAILED)
        if not verdict.supported:
            return fail(verdict.reason if verdict.reason != "ok" else REASON_NO_SUPPORT)
        quote = verdict.quote
        if not quote or len(quote) > self._max_quote or not any(quote in r.body for r in records):
            return fail(REASON_NO_SUPPORT)
        return ClaimResult(
            claim_id=claim.claim_id,
            supported=True,
            reason="ok",
            supporting_quote=quote,
            checked_evidence_ids=cited,
        )
