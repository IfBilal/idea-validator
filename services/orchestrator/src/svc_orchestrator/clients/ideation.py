"""M8 Ideation client: ideator, gap analyst, skeptic, change suggester and rewriter."""

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..models.ideas import Change, Idea, Objection
from .base import BaseClient


@dataclass(frozen=True)
class IdeateResult:
    ideas: list[Idea]
    reason: str | None
    skipped: int = 0  # malformed ideas dropped (never trusted)
    dropped: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Rewrite:
    new_text: str
    change_note: str


def _parse_each(model: Any, items: list[Any]) -> tuple[list[Any], int]:
    good: list[Any] = []
    skipped = 0
    for item in items:
        try:
            good.append(model.model_validate(item))
        except ValidationError:
            skipped += 1
    return good, skipped


class IdeationClient(BaseClient):
    name = "M8 ideation"

    def _list(self, data: Any, key: str, what: str) -> list[Any]:
        items = self._object(data, what).get(key)
        if not isinstance(items, list):
            raise self.bad(f"M8 ideation returned a {what} without a {key} list")
        return items

    async def ideate(
        self,
        *,
        run_id: str,
        profile: dict[str, Any],
        niches: list[dict[str, Any]],
        gaps: list[dict[str, Any]],
        round_: int,
        previous_objections: list[dict[str, Any]],
        count: int,
    ) -> IdeateResult:
        body = {
            "run_id": run_id,
            "profile": profile,
            "niches": niches,
            "gaps": gaps,
            "round": round_,
            "previous_objections": previous_objections,
            "count": count,
        }
        data = await self._call("POST", "/ideate", body)
        ideas, skipped = _parse_each(Idea, self._list(data, "ideas", "ideate response"))
        reason = data.get("reason")
        dropped = data.get("dropped")
        return IdeateResult(
            ideas,
            reason if isinstance(reason, str) else None,
            skipped,
            dropped if isinstance(dropped, list) else [],
        )

    async def gap_analyst(self, run_id: str, ideas: list[dict[str, Any]]) -> list[Idea]:
        data = await self._call("POST", "/gap-analyst", {"run_id": run_id, "ideas": ideas})
        return _parse_each(Idea, self._list(data, "ideas", "gap-analyst response"))[0]

    async def skeptic(
        self,
        run_id: str,
        ideas: list[dict[str, Any]],
        *,
        profile: dict[str, Any] | None = None,
        scores: dict[str, dict[str, float]] | None = None,
    ) -> list[Objection]:
        body: dict[str, Any] = {"run_id": run_id, "ideas": ideas}
        if profile is not None:
            body["profile"] = profile
        if scores is not None:
            body["scores"] = scores
        data = await self._call("POST", "/skeptic", body)
        return _parse_each(Objection, self._list(data, "objections", "skeptic response"))[0]

    async def suggest_changes(
        self,
        *,
        run_id: str,
        idea_text: str,
        dimension: str,
        current: float,
        to: float,
        evidence: list[dict[str, Any]],
        profile: dict[str, Any] | None,
    ) -> list[Change]:
        body: dict[str, Any] = {
            "run_id": run_id,
            "idea_text": idea_text,
            "target_dimension": dimension,
            "current_score": current,
            "to_score": to,
            "evidence": evidence,
        }
        if profile is not None:
            body["profile"] = profile
        data = await self._call("POST", "/suggest-changes", body)
        return _parse_each(Change, self._list(data, "changes", "suggest-changes response"))[0]

    async def rewrite(self, run_id: str, idea_text: str, change_text: str) -> Rewrite:
        body = {"run_id": run_id, "idea_text": idea_text, "change_text": change_text}
        data = self._object(await self._call("POST", "/rewrite", body), "rewrite response")
        text, note = data.get("new_text"), data.get("change_note")
        if not isinstance(text, str) or not text.strip():
            raise self.bad("M8 ideation returned a rewrite without text")
        return Rewrite(text, note if isinstance(note, str) else "")
