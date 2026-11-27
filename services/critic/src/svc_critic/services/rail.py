"""Input rail (docs/harness/13 §4): runs on every user text before it reaches any model.

Rejects (never silently repairs): empty or over-long text, control and invisible/bidi characters,
prompt-injection phrases (shared list in ``common.injection``) and clearly harmful requests.
Reasons are stable machine-readable codes. The input is never echoed back in a reason or a log.
"""

import re
import unicodedata
from dataclasses import dataclass

from common.injection import find_injections, normalize_for_matching
from common.logging import get_logger

from ..models.rail import RailRequest, RailResponse

log = get_logger("critic.rail")

_ALLOWED_CONTROLS = frozenset("\n\t\r")
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Zl", "Zp", "Co", "Cs"})
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")

_VERBS_MAKE = r"(?:create|build|write|develop|make|code|generate|design|deploy|launch)"
_GAP = r"(?:\w+ ){0,3}"
_HARMFUL: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(expr))
    for name, expr in (
        (
            "malware",
            rf"\b{_VERBS_MAKE} {_GAP}(?:malware|ransomware|keylogger|botnet|trojan|spyware"
            r"|computer virus|rootkit|exploit kit|ddos tool|phishing (?:page|site|kit|email))",
        ),
        (
            "weapons",
            rf"\b(?:build|make|manufacture|synthesi[sz]e|assemble|produce) {_GAP}"
            r"(?:bomb|explosives?|nerve agent|bioweapon|chemical weapon|ghost gun|untraceable gun)",
        ),
        (
            "fraud",
            rf"\b(?:sell|buy|trade|generate|harvest|steal) {_GAP}"
            r"(?:stolen (?:credit )?cards?|credit card numbers|fake ids?|counterfeit \w+)"
            r"|\blaunder(?:ing)? (?:\w+ ){0,2}money\b",
        ),
        (
            "stalking",
            r"\b(?:stalk|stalking|dox|doxx|doxxing|doxing) (?:\w+ ){0,3}"
            r"(?:ex|girlfriend|boyfriend|partner|person|someone|people|neighbou?r|roommate)\b",
        ),
        (
            "child_safety",
            rf"\b(?:generate|create|produce|distribute|sell|make|find|share) {_GAP}"
            r"(?:child porn(?:ography)?|csam|child sexual abuse material)",
        ),
    )
)


@dataclass(frozen=True, slots=True)
class RailLimits:
    idea: int
    answer: int
    niche: int

    def __post_init__(self) -> None:
        for name in ("idea", "answer", "niche"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} limit must be positive")

    def for_kind(self, kind: str) -> int:
        return {"idea": self.idea, "answer": self.answer, "niche": self.niche}[kind]


def _clean(text: str) -> str:
    folded = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = (_HORIZONTAL_SPACE.sub(" ", line).strip() for line in folded.split("\n"))
    return "\n".join(line for line in lines if line)


class InputRail:
    def __init__(self, limits: RailLimits) -> None:
        self._limits = limits

    def evaluate(self, request: RailRequest) -> RailResponse:
        text = request.text
        reasons: list[str] = []
        cleaned = _clean(text)

        if not cleaned:
            reasons.append("empty")
        else:
            if len(cleaned) > self._limits.for_kind(request.kind):
                reasons.append("too_long")
            has_text = any(
                ch.isalnum() if request.kind == "answer" else ch.isalpha() for ch in cleaned
            )
            if not has_text:
                reasons.append("no_text")

        categories = {unicodedata.category(ch) for ch in set(text)}
        if any(
            unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROLS for ch in set(text)
        ):
            reasons.append("control_characters")
        if categories & _INVISIBLE_CATEGORIES:
            reasons.append("invisible_characters")

        seen: set[str] = set()
        for hit in find_injections(text):
            if hit.category not in seen:
                seen.add(hit.category)
                reasons.append(f"prompt_injection:{hit.category}")

        flat = normalize_for_matching(text)
        reasons.extend(f"harmful_request:{name}" for name, rx in _HARMFUL if rx.search(flat))

        allowed = not reasons
        log.info("input_rail", kind=request.kind, allowed=allowed, reasons=reasons, chars=len(text))
        return RailResponse(
            allowed=allowed, reasons=reasons, sanitized_text=cleaned if allowed else ""
        )
