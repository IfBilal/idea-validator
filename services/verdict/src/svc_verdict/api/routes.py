"""HTTP layer only: parse, validate, call the pure engine, map errors. Internal endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from common.config import ConfigError
from common.errors import DomainError, ValidationFailed
from common.logging import get_logger
from common.schemas import Verdict
from common.service import require_internal_token

from ..config import Settings
from ..models.api import VerdictRequest, VoiRequest, VoiResponse, WhatIfRequest
from ..services.engine_config import EngineConfig, load_engine_config
from ..services.verdict_builder import build_verdict
from ..services.whatif import apply_change, evaluate_with_whatif, value_of_information

log = get_logger("verdict")


def get_rules(request: Request) -> EngineConfig:
    rules: EngineConfig | None = request.app.state.engine_config
    if rules is None:
        raise DomainError("verdict rules are not loaded")
    return rules


Rules = Annotated[EngineConfig, Depends(get_rules)]


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    @router.post("/verdict", response_model=Verdict, response_model_exclude_unset=True)
    def post_verdict(body: VerdictRequest, rules: Rules) -> Verdict:
        result = evaluate_with_whatif(body.to_engine_input(), rules)
        return build_verdict(body, rules, result)

    @router.post("/whatif", response_model=Verdict, response_model_exclude_unset=True)
    def post_whatif(body: WhatIfRequest, rules: Rules) -> Verdict:
        inp = body.input.to_engine_input()
        if body.change is not None:
            inp = apply_change(inp, body.change)
        result = evaluate_with_whatif(inp, rules)
        return build_verdict(_request_for(body.input, inp), rules, result)

    @router.post("/value-of-information", response_model=VoiResponse)
    def post_voi(body: VoiRequest, rules: Rules) -> VoiResponse:
        low, high = body.range
        return value_of_information(body.input.to_engine_input(), rules, body.unknown, low, high)

    @router.get("/config")
    def get_config(rules: Rules) -> dict[str, Any]:
        return {"rules": rules.model_dump(mode="json"), "versions": rules.versions}

    @router.post("/reload")
    def post_reload(request: Request) -> dict[str, Any]:
        """Re-read the mounted ConfigMap. Invalid rules are rejected and the old rules stay."""
        try:
            fresh = load_engine_config(settings.config_dir)
        except ValidationError as exc:
            problems = [
                {"loc": [str(p) for p in e["loc"]], "msg": e["msg"]}
                for e in exc.errors(include_input=False, include_url=False)
            ]
            log.warning("reload_rejected", reason="invalid rules", problems=problems)
            raise ValidationFailed("verdict rules are invalid", {"errors": problems}) from exc
        except ConfigError as exc:
            log.warning("reload_rejected", reason=str(exc))
            raise DomainError("verdict rules file is missing or malformed") from exc
        request.app.state.engine_config = fresh
        return {"versions": fresh.versions}

    return router


def _request_for(original: VerdictRequest, changed: Any) -> VerdictRequest:
    """Rebuild the request around a what-if input so the response reflects the changed values."""
    scores = [
        s.model_copy(update={"score": changed.score_of(s.dimension).score}) for s in original.scores
    ]
    return original.model_copy(
        update={
            "profile": changed.profile,
            "scores": scores,
            "gate_inputs": changed.gate_inputs,
        }
    )
