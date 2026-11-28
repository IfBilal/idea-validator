"""Second-stage support judge: a model check for paraphrased claims the lexical judge rejects.

The model only answers ``{supported, supporting_quote, reason}`` about one claim. It never sees
scores, verdicts or other claims. Its answer counts only if the quote is a verbatim, non-empty
substring of the (length-capped) evidence text it was shown; any failure means unsupported.
"""

import html
import json
import re
from collections.abc import Sequence
from pathlib import Path

from common.context import run_id_var
from common.injection import find_injections
from common.logging import get_logger

from ..clients.llm import LlmClient
from ..models.evidence import EvidenceRecord
from .budget import RequestBudget, current_budget
from .entailment import REASON_NO_SUPPORT, EntailmentJudge, JudgeVerdict, content_words

REASON_UNAVAILABLE = "judge unavailable"
PROMPT_FILE = "critic@v1.md"
MIN_QUOTE_CHARS = (
    15  # a one-word "quote" proves nothing; a paraphrase may share no word with its quote
)
MIN_QUOTE_WORDS = 3
MIN_SHARED_WORDS = (
    2  # content words the quote and the claim must share: the quote must be about the claim
)
JSON_HINT = (
    '\n\nReply with one JSON object only: {"supported": true or false, "supporting_quote": '
    '"a verbatim sentence from the evidence" or null}.'
)
MAX_TOKENS = 600  # the hosted models spend part of this on reasoning tokens (config/llm.yaml)

log = get_logger("critic.llm_judge")
_PLACEHOLDER = re.compile(r"\{(claim_text|evidence_blocks)\}")


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
        deadline_s: float = 40.0,
    ) -> None:
        self._deadline_s = deadline_s
        self._fallback_budget = RequestBudget()  # only used outside a request scope (tests)
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
        budget = current_budget() or self._fallback_budget
        if (
            verdict.supported or not run_id_var.get()
        ):  # no run id: no attributable M5 budget, stay lexical
            return verdict
        if not budget.allow(self._max_claims, self._deadline_s):
            return verdict
        shown = [(r.evidence_id, r.body[: self._max_evidence]) for r in evidence]
        blocks = "\n".join(
            f'<evidence id="{html.escape(eid, quote=True)}">'
            f"{html.escape(text, quote=False)}</evidence>"
            for eid, text in shown
        )
        values = {
            "claim_text": html.escape(" ".join(claim.split()), quote=False),
            "evidence_blocks": blocks,
        }
        user = _PLACEHOLDER.sub(lambda m: values[m.group(1)], self._user) + JSON_HINT
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
        too_thin = len(quote) < MIN_QUOTE_CHARS or len(quote.split()) < MIN_QUOTE_WORDS
        if too_thin or len(quote) > self._max_quote or not any(quote in t for _, t in shown):
            return JudgeVerdict(False, None, REASON_NO_SUPPORT)
        unrelated = len(content_words(claim) & content_words(quote)) < MIN_SHARED_WORDS
        if unrelated or find_injections(quote):
            # a real sentence that has nothing to do with the claim, or one that is itself an
            # instruction, is what a poisoned card would offer as its "quote"
            return JudgeVerdict(False, None, REASON_NO_SUPPORT)
        return JudgeVerdict(True, quote, "ok")
