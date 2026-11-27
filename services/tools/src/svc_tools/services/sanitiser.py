"""Sanitiser for fetched text (docs/harness/10 §3, 13 §6).

Fetched text is **data**. This module removes what could hide or carry instructions aimed at a
model: scripts, hidden HTML, invisible characters, instruction-like phrases, exfiltration links
and encoded blobs. It is a heuristic layer, not a guarantee; defence in depth is that M5 wraps the
text as data, scorers never obey it, and the critic checks every claim.

Pure functions, no I/O.
"""

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

MARKER = "[removed: instruction-like text]"
DEFAULT_MAX_CHARS = 200_000

_TAIL = r"[^.!?\n]*[.!?]?"  # swallow the rest of the sentence the instruction lives in
_SENTENCE_START = r"(?:^|(?<=[.!?]))[ \t]{0,4}"
_ROLE = r"(?:system|assistant|developer)"
_TARGET = r"(?:idea|product|startup|app|project)"

INSTRUCTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE | re.MULTILINE)
    for p in (
        # review of PR 18: more verb/object variants
        rf"\b(?:ignore|disregard|forget)\s+(?:everything|anything|all)\s+(?:above|before|prior|previously)\b{_TAIL}",
        rf"\b(?:ignore|disregard)\s+(?:the\s+|all\s+|your\s+)*(?:instructions?|prompts?|rules?|directions?)\s+(?:above|before|given|provided)\b{_TAIL}",
        rf"\bforget\s+(?:what|everything)\s+you\s+(?:were|have\s+been|are)\s+(?:told|given|taught)\b{_TAIL}",
        r"\[\[\s{0,3}(?:system|inst|assistant)\s{0,3}\]\]",
        # "ignore all previous instructions", "disregard the above rules", ...
        rf"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)*"
        rf"(?:previous|prior|above|earlier|preceding|system|safety|scoring)\s+"
        rf"(?:instructions?|prompts?|rules?|context|messages?|guidelines?|directions?|text|directives?|orders?){_TAIL}",
        rf"\b(?:ignore|disregard)\s+(?:the\s+)?system\s+prompt{_TAIL}",
        # persona and role switches
        rf"\bfrom\s+now\s+on,?\s+you\s+(?:are|will|must|should)\b{_TAIL}",
        rf"\byou\s+are\s+now\s+(?:an?\s|in\s+\w+\s+mode\b){_TAIL}",
        rf"\bpretend\s+(?:to\s+be|you\s+are|that\s+you)\b{_TAIL}",
        rf"\bnew\s+instructions?\s*:{_TAIL}",
        # fake conversation roles at the start of a line or sentence
        rf"{_SENTENCE_START}{_ROLE}\s*:[^\n]*",
        # chat-template and delimiter tokens
        r"<\|[a-z_]+\|>",
        r"\[/?INST\]",
        r"<</?SYS>>",
        r"<\s{0,3}/?\s{0,3}(?:evidence|system|assistant|instructions?)\b[^>\n]{0,200}>",
        rf"###\s*instructions?\b{_TAIL}",
        # secrecy and manipulation
        rf"\bdo(?:\s+not|n['\u2019]t)\s+tell\s+the\s+user\b{_TAIL}",
        rf"\bwithout\s+telling\s+the\s+user\b{_TAIL}",
        rf"\bkeep\s+(?:this|it)\s+secret\s+from\s+the\s+user\b{_TAIL}",
        rf"{_SENTENCE_START}(?:please\s+)?(?:score|rate|rank|grade)\s+this\s+{_TARGET}?\s*"
        rf"(?:a\s+|as\s+)?(?:5|five|10|ten|perfect|maximum|highest)\b{_TAIL}",
        rf"\bgive\s+this\s+{_TARGET}?\s*a\s+(?:perfect|maximum|top|5|five|10|ten)\b{_TAIL}",
        # prompt extraction and jailbreaks
        rf"\b(?:reveal|print|show|output|repeat)\s+(?:me\s+)?your\s+(?:system\s+)?"
        rf"(?:prompt|instructions)\b{_TAIL}",
        rf"\brepeat\s+the\s+words\s+above\b{_TAIL}",
        rf"\b(?:enable|activate|enter|switch\s+to)\s+developer\s+mode\b{_TAIL}",
        rf"\bDAN\s+mode\b{_TAIL}",
        rf"\b(?:execute|run)\s+the\s+following\s+(?:command|code|script|shell)s?\b{_TAIL}",
        # markdown image used to exfiltrate data through a query string
        r"!\[[^\]\n]{0,200}\]\(\s{0,3}https?://[^)\s]{0,500}[?&=][^)\s]{0,500}\)",
    )
)
ENCODED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"data:[\w.+-]+/[\w.+-]+(?:;[\w=.+-]+)*;base64,[A-Za-z0-9+/=]+", re.IGNORECASE),
    re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),
)

_EXTRA_INVISIBLE = frozenset("\u3164\u115f\u1160\u2800\u180e\u034f\u00ad")
_STRIPPED_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cn", "Cs"})
_KEEP = frozenset("\n\t")
MAX_INPUT_FACTOR = 4  # input is cut to max_chars * 4 before any pattern runs
NORMALISE_CHUNK = 4096
_ASCII_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
MAX_HTML_CHARS = 1_000_000
MAX_HTML_DEPTH = 200
# Cyrillic and Greek look-alikes folded to Latin on a detection copy only (the text itself is kept)
_CONFUSABLES = str.maketrans(
    "\u0430\u0435\u043e\u0440\u0441\u0445\u0443\u0456\u0455\u0458\u04bb\u0501\u051b\u0455"
    "\u03bf\u03b1\u03b5\u03b9\u03ba\u03bd\u03c1\u03c5\u0391\u0392\u0395\u0397\u0399\u039a\u039c\u039d"
    "\u039f\u03a1\u03a4\u03a7\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425",
    "aeopcxyisjhdqsoaeiknpyABEHIKMNOPTXABEKMHOPCTX",
)
_SPACED_RUN = re.compile(r"(?<![^\W_])(?:[^\W_] ){2,}[^\W_](?![^\W_])")
_HTML_TYPES = ("text/html", "application/xhtml")
_SKIP_TAGS = frozenset(
    {"script", "style", "iframe", "noscript", "template", "head", "object", "embed"}
)
_VOID_TAGS = frozenset(
    {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "source", "track", "wbr"}
)
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
     "section", "article", "header", "footer", "blockquote", "pre", "hr"}
)  # fmt: skip
_HIDDEN_STYLE = re.compile(
    r"display:none|visibility:hidden|font-size:0(?:px|pt|em|rem|%)?(?:;|$)|opacity:0(?:;|$)"
)


@dataclass(frozen=True)
class SanitisedText:
    text: str
    removed_patterns: int
    flagged: bool


class _VisibleText(HTMLParser):
    """Collect only what a person would see: no scripts, comments, or hidden elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, bool]] = []  # (tag, hides its content)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS and not self._hidden_depth:
            self.parts.append("\n")
        if tag in _VOID_TAGS:
            return
        if len(self._stack) >= MAX_HTML_DEPTH:
            return  # pathological nesting: keep the text, ignore the structure
        attributes = {name: (value or "") for name, value in attrs}
        style = attributes.get("style", "").lower().replace(" ", "")
        hides = (
            tag in _SKIP_TAGS
            or "hidden" in attributes
            or attributes.get("aria-hidden", "").lower() == "true"
            or bool(_HIDDEN_STYLE.search(style))
        )
        self._stack.append((tag, hides))
        if hides:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                for _, hides in self._stack[index:]:
                    if hides:
                        self._hidden_depth -= 1
                del self._stack[index:]
                break
        if tag in _BLOCK_TAGS and not self._hidden_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


def _html_to_text(markup: str) -> str:
    markup = markup[:MAX_HTML_CHARS]
    parser = _VisibleText()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: S110  broken markup must never break a fetch
        pass
    return "".join(parser.parts)


def _find_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in (*INSTRUCTION_PATTERNS, *ENCODED_PATTERNS):
        for match in pattern.finditer(text):
            start = match.start() + (len(match.group()) - len(match.group().lstrip()))
            if start < match.end():
                spans.append((start, match.end()))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalise_bounded(text: str, limit: int) -> str:
    """NFKC in chunks, stopping at ``limit`` output characters.

    One character can expand to 18 (U+FDFA), so capping the *input* is not enough: normalising a
    capped but hostile text first and cutting afterwards cost 11 s for 0.9 MB.
    """
    out: list[str] = []
    total = 0
    for start in range(0, len(text), NORMALISE_CHUNK):
        chunk = unicodedata.normalize("NFKC", text[start : start + NORMALISE_CHUNK])
        out.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
    return "".join(out)[:limit]


def _strip_invisible(text: str) -> str:
    if text.isascii():  # fast path: only control characters can be invisible in ASCII
        return _ASCII_CONTROLS.sub("", text)
    return "".join(
        ch
        for ch in text
        if ch in _KEEP
        or (
            ch not in _EXTRA_INVISIBLE
            and unicodedata.category(ch) not in _STRIPPED_CATEGORIES
            and not (0xFE00 <= ord(ch) <= 0xFE0F or 0xE0100 <= ord(ch) <= 0xE01EF)
        )
    )


def _despace(text: str) -> str:
    """Join runs of single letters spaced apart ("i g n o r e"); words stay separated."""

    def join(match: re.Match[str]) -> str:
        return match.group(0).replace(" ", "")

    return _SPACED_RUN.sub(join, text)


def _replace(text: str, spans: list[tuple[int, int]]) -> str:
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(text[cursor:start])
        out.append(MARKER)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def sanitise(
    text: str, content_type: str | None = None, max_chars: int = DEFAULT_MAX_CHARS
) -> SanitisedText:
    """Return visible, normalised text with instruction-like content replaced by ``MARKER``.

    Every stage is linear: the input is capped first, invisible characters are removed, runs of
    whitespace are collapsed before any pattern runs, and the patterns themselves are bounded.
    """
    if content_type and content_type.lower().startswith(_HTML_TYPES):
        text = _html_to_text(text)
    limit = max_chars * MAX_INPUT_FACTOR
    raw = _strip_invisible(_normalise_bounded(text[:limit], limit))
    text = _tidy(raw)
    spans = _find_spans(text.translate(_CONFUSABLES))  # 1:1 fold, so offsets still line up
    if not spans:
        spaced = _tidy(_despace(raw))  # despace before collapsing: wide gaps separate the words
        if spaced != text:
            spaced_spans = _find_spans(spaced.translate(_CONFUSABLES))
            if spaced_spans:
                text, spans = spaced, spaced_spans
    cleaned = _tidy(_replace(text, spans))
    return SanitisedText(text=cleaned[:max_chars], removed_patterns=len(spans), flagged=bool(spans))
