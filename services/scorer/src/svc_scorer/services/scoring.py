"""Hosted-mode scoring: prompt -> M5 /llm -> validate -> DimensionScore (or a safe abstention).

Flow (docs/harness/11 §4): invalid output is retried once, then the result is an abstention with
``fallback=True``. Cited evidence ids must be a subset of the request's evidence. The model never
produces a verdict; stray keys in its reply are discarded.
"""

from collections.abc import Callable
from typing import Protocol, cast

from common.logging import get_logger
from common.schemas import DimensionScore, ScoreRequest
from common.schemas.types import Dimension

from .prompt import PromptBuilder, dimension_of
from .replies import ParsedReply, ReplyError, parse_reply, resolve_evidence

MAX_ATTEMPTS = 2  # first try + one retry
MAX_TOKENS = 800
log = get_logger("scorer")


class LlmPort(Protocol):
    async def complete(self, *, run_id: str, system: str, user: str, max_tokens: int) -> str: ...


class ScoringService:
    def __init__(
        self,
        llm: LlmPort,
        prompts: PromptBuilder,
        *,
        model_name: str,
        on_fallback: Callable[[str], None] | None = None,
    ) -> None:
        self._llm = llm
        self._prompts = prompts
        self._model = model_name
        self._on_fallback = on_fallback

    def _result(
        self,
        request: ScoreRequest,
        parsed: ParsedReply | None,
        evidence_ids: list[str],
        *,
        reason: str,
        fallback: bool,
    ) -> DimensionScore:
        abstained = parsed is None or parsed.score is None or not evidence_ids
        dimension = cast(Dimension, dimension_of(request.rubric_id))
        return DimensionScore(
            dimension=dimension,
            score=None if abstained or parsed is None else parsed.score,
            confidence=0.0 if abstained or parsed is None else parsed.confidence,
            evidence_ids=[] if abstained else evidence_ids,
            features=request.features,
            reason=reason,
            rubric_version=request.rubric_id,
            model=self._model,
            abstained=abstained,
            fallback=fallback,
        )

    async def score(self, request: ScoreRequest) -> DimensionScore:
        system, user = self._prompts.build(request)  # also validates the rubric id
        if not request.evidence:
            return self._result(request, None, [], reason="no evidence", fallback=False)

        provided = {e.evidence_id for e in request.evidence}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            text = await self._llm.complete(
                run_id=request.run_id, system=system, user=user, max_tokens=MAX_TOKENS
            )
            try:
                parsed = parse_reply(text)
            except ReplyError as exc:
                log.warning(
                    "scorer_invalid_output",
                    run_id=request.run_id,
                    rubric=request.rubric_id,
                    attempt=attempt,
                    problem=str(exc),
                )
                continue
            if parsed.score is None:
                return self._result(
                    request, None, [], reason=parsed.reason or "no evidence", fallback=False
                )
            valid = resolve_evidence(parsed.evidence_ids, provided)
            if not valid:
                return self._result(
                    request, None, [], reason="no valid evidence ids", fallback=False
                )
            return self._result(request, parsed, valid, reason=parsed.reason, fallback=False)

        if self._on_fallback is not None:
            self._on_fallback("invalid_output")
        return self._result(
            request, None, [], reason="model output invalid after retry", fallback=True
        )
