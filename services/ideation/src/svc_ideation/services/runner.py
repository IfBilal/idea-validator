"""Runs one single-purpose agent: render its versioned prompt, call M5 /llm, parse JSON, retry once.

Only malformed model output is retried (CLAUDE.md rule 9: bounded loops). Transport, budget and
upstream errors propagate unchanged so the orchestrator can decide.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.logging import get_logger
from common.tracing import Tracer

from ..clients.llm import LlmClient
from .prompts import Prompt, extract_json_object, load_prompt, render_messages

RETRY_REMINDER = (
    "Your previous reply was not valid JSON matching the required schema. "
    "Reply again with a single JSON object only, no prose, no code fences."
)
ATTEMPTS = 2  # first try + one retry


class AgentRunner:
    def __init__(
        self,
        llm: LlmClient,
        prompts_dir: Path,
        *,
        max_tokens: int,
        tracer: Tracer | None = None,
    ) -> None:
        self._llm = llm
        self._dir = prompts_dir
        self._max_tokens = max_tokens
        self._tracer = tracer or Tracer("ideation")
        self._prompts: dict[str, Prompt] = {}
        self._log = get_logger("ideation.runner")

    def prompt(self, name: str) -> Prompt:
        if name not in self._prompts:
            self._prompts[name] = load_prompt(self._dir, name)
        return self._prompts[name]

    def prompt_version(self, name: str) -> str:
        prompt = self.prompt(name)
        return f"{prompt.id}@v{prompt.version}"

    async def run[T](
        self,
        name: str,
        run_id: str,
        values: dict[str, str],
        parse: Callable[[dict[str, Any]], T | None],
    ) -> T | None:
        """The parsed result, or None when the model never produced a valid reply."""
        prompt = self.prompt(name)
        messages = render_messages(prompt, values)
        version = f"{prompt.id}@v{prompt.version}"
        for attempt in range(ATTEMPTS):
            if attempt:
                messages = [*messages, {"role": "user", "content": RETRY_REMINDER}]
            with self._tracer.span(
                name, "llm_call", run_id=run_id, prompt_version=version, input={"attempt": attempt}
            ) as span:
                reply = await self._llm.complete(
                    run_id=run_id,
                    agent=name,
                    messages=messages,
                    temperature=prompt.temperature,
                    max_tokens=self._max_tokens,
                )
                span.model = reply.model
                span.retries = attempt
                span.set_usage(
                    tokens_in=reply.tokens_in,
                    tokens_out=reply.tokens_out,
                    cost_usd=reply.cost_usd,
                )
                data = extract_json_object(reply.content)
                result = parse(data) if data is not None else None
                span.set_output({"valid": result is not None})
            if result is not None:
                return result
            self._log.warning("model_output_invalid", agent=name, attempt=attempt, run_id=run_id)
        return None
