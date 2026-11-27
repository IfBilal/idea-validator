"""Scoring: prompt -> model -> validate -> DimensionScore (or a safe abstention).

Hosted-only mode (no ``fallback_llm``): invalid output is retried once, then the result is an
abstention with ``fallback=True``; transport errors propagate (M9 turns them into abstentions).

Local-first mode (``llm`` = the fine-tuned GGUF, ``fallback_llm`` = hosted via M5, 11 §4): a local
reply that is invalid (bad JSON, schema, invented evidence id) is retried once, then the hosted
model is asked, with the fallback logged and counted; still invalid means abstain. A local
transport failure skips the retry and goes straight to hosted. The model tag on the result says
which model produced the score.

In every mode the model never produces a verdict: stray keys in its reply are discarded, and the
cited evidence ids must be a subset of the request's evidence.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from common.errors import DomainError
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


@dataclass(frozen=True)
class Outcome:
    kind: Literal["score", "abstain", "invalid", "unavailable"]
    parsed: ParsedReply | None = None
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""


class ScoringService:
    def __init__(
        self,
        llm: LlmPort,
        prompts: PromptBuilder,
        *,
        model_name: str,
        fallback_llm: LlmPort | None = None,
        fallback_model_name: str = "hosted-default",
        on_fallback: Callable[[str], None] | None = None,
    ) -> None:
        self._llm = llm
        self._prompts = prompts
        self._model = model_name
        self._fallback_llm = fallback_llm
        self._fallback_model = fallback_model_name
        self._on_fallback = on_fallback

    def _result(
        self,
        request: ScoreRequest,
        outcome: Outcome | None,
        *,
        model: str,
        fallback: bool,
        reason: str = "",
    ) -> DimensionScore:
        parsed = outcome.parsed if outcome is not None else None
        scored = outcome is not None and outcome.kind == "score" and parsed is not None
        score = parsed.score if scored and parsed is not None else None
        abstained = not scored or score is None
        dimension = cast(Dimension, dimension_of(request.rubric_id))
        return DimensionScore(
            dimension=dimension,
            score=None if abstained else score,
            confidence=0.0 if abstained or parsed is None else parsed.confidence,
            evidence_ids=[] if abstained or outcome is None else list(outcome.evidence_ids),
            features=request.features,
            reason=reason or (parsed.reason if parsed is not None else ""),
            rubric_version=request.rubric_id,
            model=model,
            abstained=abstained,
            fallback=fallback,
        )

    async def _attempts(
        self,
        llm: LlmPort,
        request: ScoreRequest,
        system: str,
        user: str,
        *,
        strict: bool,
        catch_transport: bool,
    ) -> Outcome:
        """Ask ``llm`` up to MAX_ATTEMPTS times; ``strict``: invented evidence ids are invalid."""
        provided = {e.evidence_id for e in request.evidence}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                text = await llm.complete(
                    run_id=request.run_id, system=system, user=user, max_tokens=MAX_TOKENS
                )
            except DomainError as exc:
                if not catch_transport:
                    raise
                log.warning("scorer_local_unavailable", run_id=request.run_id, problem=str(exc))
                return Outcome("unavailable")
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
                return Outcome("abstain", parsed, reason=parsed.reason or "no evidence")
            valid = resolve_evidence(parsed.evidence_ids, provided)
            if not valid:
                if strict:
                    log.warning(
                        "scorer_invalid_output",
                        run_id=request.run_id,
                        attempt=attempt,
                        problem="no valid evidence ids",
                    )
                    continue
                return Outcome("abstain", reason="no valid evidence ids")
            return Outcome("score", parsed, tuple(valid))
        return Outcome("invalid")

    async def score(self, request: ScoreRequest) -> DimensionScore:
        system, user = self._prompts.build(request)  # also validates the rubric id
        if not request.evidence:
            return self._result(
                request, None, model=self._model, fallback=False, reason="no evidence"
            )

        if self._fallback_llm is None:
            outcome = await self._attempts(
                self._llm, request, system, user, strict=False, catch_transport=False
            )
            return self._finish(request, outcome, model=self._model, count_invalid=True)

        local = await self._attempts(
            self._llm, request, system, user, strict=True, catch_transport=True
        )
        if local.kind in ("score", "abstain"):
            return self._finish(request, local, model=self._model, count_invalid=False)

        if self._on_fallback is not None:
            self._on_fallback("unavailable" if local.kind == "unavailable" else "invalid_output")
        log.warning("scorer_fallback_to_hosted", run_id=request.run_id, reason=local.kind)
        hosted = await self._attempts(
            self._fallback_llm, request, system, user, strict=False, catch_transport=False
        )
        if hosted.kind in ("score", "abstain"):
            return self._result(
                request,
                hosted,
                model=self._fallback_model if hosted.kind == "score" else self._model,
                fallback=True,
                reason=hosted.reason,
            )
        return self._result(
            request,
            None,
            model=self._model,
            fallback=True,
            reason="model output invalid after retry",
        )

    def _finish(
        self, request: ScoreRequest, outcome: Outcome, *, model: str, count_invalid: bool
    ) -> DimensionScore:
        if outcome.kind == "score":
            return self._result(request, outcome, model=model, fallback=False)
        if outcome.kind == "abstain":
            return self._result(request, None, model=model, fallback=False, reason=outcome.reason)
        if count_invalid and self._on_fallback is not None:
            self._on_fallback("invalid_output")
        return self._result(
            request, None, model=model, fallback=True, reason="model output invalid after retry"
        )
