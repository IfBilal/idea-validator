"""Versioned prompt files: loading, placeholder rendering, evidence spotlighting, JSON extraction.

Fetched text is data (CLAUDE.md rule 5): evidence is HTML-escaped and wrapped in ``<evidence>``
delimiters so it cannot close the tag or inject markup, and substituted values are never rescanned
for placeholders.
"""

import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.config import ConfigError
from common.schemas import EvidenceItem

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")
NO_EVIDENCE = "(no evidence found)"


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    temperature: float
    system: str
    user: str


def _front_matter_field(front: str, key: str, path: Path) -> str:
    match = re.search(rf"^{key}:\s*(.+?)\s*$", front, re.MULTILINE)
    if match is None:
        raise ConfigError(f"{path.name}: front matter has no '{key}'")
    return match.group(1)


def load_prompt(prompts_dir: Path, name: str, version: int = 1) -> Prompt:
    path = prompts_dir / f"{name}@v{version}.md"
    try:
        text = path.read_text()
    except FileNotFoundError as exc:
        raise ConfigError(f"prompt file not found: {path}") from exc
    parts = text.split("---\n", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ConfigError(f"{path.name}: missing front matter")
    front, body = parts[1], parts[2]
    try:
        temperature = float(_front_matter_field(front, "temperature", path))
    except ValueError as exc:
        raise ConfigError(f"{path.name}: temperature is not a number") from exc
    system_part, sep, user_part = body.partition("## USER")
    if not sep or "## SYSTEM" not in system_part:
        raise ConfigError(f"{path.name}: needs '## SYSTEM' and '## USER' sections")
    return Prompt(
        id=_front_matter_field(front, "id", path),
        version=int(_front_matter_field(front, "version", path)),
        temperature=temperature,
        system=system_part.split("## SYSTEM", 1)[1].strip(),
        user=user_part.strip(),
    )


def _fill(template: str, values: Mapping[str, str], where: str) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ConfigError(f"prompt {where} needs a value for '{key}'")
        return values[key]

    # one pass over the template only: values are inserted verbatim, never re-expanded
    return _PLACEHOLDER.sub(replace, template)


def render_messages(prompt: Prompt, values: Mapping[str, str]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _fill(prompt.system, values, f"{prompt.id} system")},
        {"role": "user", "content": _fill(prompt.user, values, f"{prompt.id} user")},
    ]


def _attr(value: str) -> str:
    return html.escape(value, quote=True)


def render_evidence(items: Sequence[EvidenceItem]) -> str:
    """Spotlighting block: each item in ``<evidence id=.. source=.. date=..>`` (docs/09 §1)."""
    if not items:
        return NO_EVIDENCE
    blocks: list[str] = []
    for item in items:
        date = item.published_at.isoformat() if item.published_at else "unknown"
        name = f' name="{_attr(item.card_name)}"' if item.card_name else ""
        blocks.append(
            f'<evidence id="{_attr(item.evidence_id)}" source="{_attr(item.source)}" '
            f'date="{date}"{name}>{html.escape(item.quote, quote=False)}</evidence>'
        )
    return "\n".join(blocks)


def safe_json(value: Any) -> str:
    """Compact JSON for prompts with ``< > &`` escaped, so embedded text cannot forge tags."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def extract_json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a model reply (tolerates code fences and chatter), else None."""
    stripped = _FENCE.sub("", text)
    start = stripped.find("{")
    if start == -1:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(stripped[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
