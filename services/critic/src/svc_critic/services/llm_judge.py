"""Second-stage support judge: a model check for paraphrased claims the lexical judge rejects.

The model only answers ``{supported, supporting_quote, reason}`` about one claim. It never sees
scores, verdicts or other claims. Its answer counts only if the quote is a verbatim, non-empty
substring of the (length-capped) evidence text it was shown; any failure means unsupported.
"""

import html
import json
import re
from collections.abc import Sequence
from contextvars import ContextVar
from pathlib import Path

from common.logging import get_logger

from ..clients.llm import LlmClient
from ..models.evidence import EvidenceRecord
from .entailment import REASON_NO_SUPPORT, EntailmentJudge, JudgeVerdict

REASON_UNAVAILABLE = "judge unavailable"
PROMPT_FILE = "critic@v1.md"
MAX_TOKENS = 600  # the hosted models spend part of this on reasoning tokens (config/llm.yaml)

log = get_logger("critic.llm_judge")
_PLACEHOLDER = re.compile(r"\{(claim_text|evidence_blocks)\}")
# LLM calls made in the current request. Each HTTP request runs in its own task whose context is a
# copy, so the counter never leaks between requests (shortcut: unit tests share one per test).
_calls: ContextVar[int] = ContextVar("critic_llm_judge_calls", default=0)


class LlmEntailmentJudge:
    def __init__(
        self,
        first: EntailmentJudge,
        client: LlmClient,
        prompts_dir: Path,
        *,
        max_claims: int = 8,
        max_evidence_chars: int = 1500,
        max_quote_chars: int = 200,
    ) -> None:
        self._first = first
        self._client = client
        self._max_claims = max_claims
        self._max_evidence = max_evidence_chars
        self._max_quote = max_quote_chars
        text = (prompts_dir / PROMPT_FILE).read_text()
        body = text.split("\n---\n", 1)[1] if text.startswith("---") else text
        system, _, user = body.partition("## USER")
        self._system = system.replace("## SYSTEM", "", 1).strip()
        self._user = user.strip()

    async def judge(self, claim: str, evidence: Sequence[EvidenceRecord]) -> JudgeVerdict:
        verdict = await self._first.judge(claim, evidence)
        used = _calls.get()
        if verdict.supported or used >= self._max_claims:
            return verdict
        _calls.set(used + 1)
        shown = [(r.evidence_id, r.body[: self._max_evidence]) for r in evidence]
        blocks = "\n".join(
            f'<evidence id="{eid}">{html.escape(text, quote=False)}</evidence>'
            for eid, text in shown
        )
        values = {"claim_text": " ".join(claim.split()), "evidence_blocks": blocks}
        user = _PLACEHOLDER.sub(lambda m: values[m.group(1)], self._user)
        try:
            raw = await self._client.complete(system=self._system, user=user, max_tokens=MAX_TOKENS)
            answer = json.loads(raw)
            supported, quote = answer["supported"], answer.get("supporting_quote")
            if not isinstance(supported, bool):
                raise ValueError("supported must be a boolean")
        except Exception as exc:  # any failure must degrade to unsupported, never raise
            log.warning("llm_judge_unavailable", error=type(exc).__name__)
            return JudgeVerdict(False, None, REASON_UNAVAILABLE)
        if not supported or not isinstance(quote, str):
            return JudgeVerdict(False, None, REASON_NO_SUPPORT)
        quote = quote.strip()
        if not quote or len(quote) > self._max_quote or not any(quote in t for _, t in shown):
            return JudgeVerdict(False, None, REASON_NO_SUPPORT)
        return JudgeVerdict(True, quote, "ok")
