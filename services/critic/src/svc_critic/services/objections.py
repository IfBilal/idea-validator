"""Keep only evidence-backed Skeptic objections (M12-05).

An objection is accepted under the same rules as a claim (evidence exists, competitor rule,
verbatim supporting quote). At most ``max_accepted`` are kept, in input order. M11 later subtracts
0.5 per accepted objection; nothing is scored here.
"""

from common.logging import get_logger
from common.schemas import Claim

from ..models.objections import (
    AcceptedObjection,
    ObjectionFilterRequest,
    ObjectionFilterResponse,
    RejectedObjection,
)
from .claims import ClaimChecker

REASON_OVER_CAP = "over objection cap"

log = get_logger("critic.objections")


class ObjectionFilter:
    def __init__(self, checker: ClaimChecker, *, max_accepted: int) -> None:
        if max_accepted < 1:
            raise ValueError("max_accepted must be at least 1")
        self._checker = checker
        self._max_accepted = max_accepted

    async def filter(self, request: ObjectionFilterRequest) -> ObjectionFilterResponse:
        claims = [
            Claim(
                claim_id=o.objection_id,
                text=o.text,
                evidence_ids=o.evidence_ids,
                dimension=o.dimension,
            )
            for o in request.objections
        ]
        results = await self._checker.check_claims(claims)
        accepted: list[AcceptedObjection] = []
        rejected: list[RejectedObjection] = []
        for objection, result in zip(request.objections, results, strict=True):
            if not result.supported or result.supporting_quote is None:
                rejected.append(
                    RejectedObjection(objection_id=objection.objection_id, reason=result.reason)
                )
            elif len(accepted) >= self._max_accepted:
                rejected.append(
                    RejectedObjection(objection_id=objection.objection_id, reason=REASON_OVER_CAP)
                )
            else:
                accepted.append(
                    AcceptedObjection(
                        objection_id=objection.objection_id,
                        dimension=objection.dimension,
                        text=objection.text,
                        evidence_ids=objection.evidence_ids,
                        supporting_quote=result.supporting_quote,
                    )
                )
        log.info("objections_filtered", accepted=len(accepted), rejected=len(rejected))
        return ObjectionFilterResponse(accepted=accepted, rejected=rejected)
