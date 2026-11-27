"""HTTP layer only: parse, validate, call a service, map errors. Internal endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from common.schemas import ClaimCheckRequest, ClaimCheckResponse
from common.service import require_internal_token

from ..config import Settings
from ..models.approval import ApprovalDecision, ApprovalRequest
from ..models.objections import ObjectionFilterRequest, ObjectionFilterResponse
from ..models.rail import RailRequest, RailResponse
from ..services.approval import BudgetBook, evaluate
from ..services.claims import ClaimChecker
from ..services.objections import ObjectionFilter
from ..services.rail import InputRail


def checker(request: Request) -> ClaimChecker:
    service: ClaimChecker = request.app.state.checker
    return service


def objection_filter(request: Request) -> ObjectionFilter:
    service: ObjectionFilter = request.app.state.objection_filter
    return service


def input_rail(request: Request) -> InputRail:
    service: InputRail = request.app.state.rail
    return service


def budget_book(request: Request) -> BudgetBook:
    book: BudgetBook = request.app.state.budgets
    return book


Checker = Annotated[ClaimChecker, Depends(checker)]
Objections = Annotated[ObjectionFilter, Depends(objection_filter)]
Rail = Annotated[InputRail, Depends(input_rail)]
Book = Annotated[BudgetBook, Depends(budget_book)]


def make_router(settings: Settings) -> APIRouter:
    # the body size limit is enforced by common.limits.BodyLimitMiddleware (chunked-safe)
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    @router.post("/check", response_model=ClaimCheckResponse)
    async def check(body: ClaimCheckRequest, service: Checker) -> ClaimCheckResponse:
        return await service.check(body)

    @router.post("/objections/filter", response_model=ObjectionFilterResponse)
    async def filter_objections(
        body: ObjectionFilterRequest, service: Objections
    ) -> ObjectionFilterResponse:
        return await service.filter(body)

    @router.post("/rail/input", response_model=RailResponse)
    async def rail_input(body: RailRequest, service: Rail) -> RailResponse:
        return service.evaluate(body)

    @router.post("/approval/evaluate", response_model=ApprovalDecision)
    async def approval_evaluate(body: ApprovalRequest, book: Book) -> ApprovalDecision:
        return evaluate(book.get(), body, hard_cap_multiplier=book.multiplier)

    return router
