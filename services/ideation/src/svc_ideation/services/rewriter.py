"""Idea Rewriter: apply exactly one chosen change and return the new idea text."""

from typing import Any

from ..config import Settings
from ..errors import ModelOutputRejected
from ..models.api import MAX_IDEA_TEXT, RewriteRequest, RewriteResponse
from .common import normalise
from .runner import AgentRunner

AGENT = "idea_rewriter"
MAX_NOTE_WORDS = 15


class RewriterService:
    def __init__(self, runner: AgentRunner, settings: Settings) -> None:
        self._runner = runner
        self._settings = settings

    async def rewrite(self, request: RewriteRequest) -> RewriteResponse:
        original = normalise(request.idea_text)

        def parse(data: dict[str, Any]) -> tuple[str, str] | None:
            text = data.get("new_text")
            if not isinstance(text, str):
                return None
            text = text.strip()
            if not text or len(text) > MAX_IDEA_TEXT or normalise(text) == original:
                return None
            note = data.get("change_note")
            words = note.split() if isinstance(note, str) else []
            return text, " ".join(words[:MAX_NOTE_WORDS])

        result = await self._runner.run(
            AGENT,
            request.run_id or "run_unknown",
            {"idea_text": request.idea_text, "change_text": request.change_text},
            parse,
        )
        if result is None:
            raise ModelOutputRejected("the rewriter did not produce a usable new idea text")
        return RewriteResponse(
            new_text=result[0],
            change_note=result[1],
            prompt_version=self._runner.prompt_version(AGENT),
        )
