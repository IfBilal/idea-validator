"""GET /registry: what versions of prompts, rubrics, config and models this build carries."""

import asyncio
from pathlib import Path

from common import registry
from common.errors import Unavailable

from ..models.registry import ActiveVersions, ModelInfo, RegistryEntry, RegistryView


def _versions(entries: dict[str, registry.Entry]) -> dict[str, str]:
    names = {
        "verdict.engine": "engine",
        "verdict.weights": "weights",
        "verdict.thresholds": "thresholds",
        "verdict.rubric_set": "rubric_set",
        "budgets": "budgets",
        "prompts": "prompts",
        "llm": "llm",
    }
    return {
        names[e.name]: e.version for e in entries.values() if e.kind == "config" and e.name in names
    }


def build_view(root: Path) -> RegistryView:
    if not root.is_dir():
        raise Unavailable("the registry directory is not mounted")
    found = registry.scan(root)
    lock_path = root / registry.LOCK_NAME
    problems = list(found.problems)
    try:
        problems += registry.compare(registry.load_lock(lock_path), found.entries)
    except ValueError as exc:
        problems.append(str(exc))
    entries = [
        RegistryEntry(kind=e.kind, name=e.name, version=e.version, sha256=e.sha256, path=e.path)
        for _, e in sorted(found.entries.items())
    ]
    return RegistryView(
        entries=entries,
        active=ActiveVersions(prompts=found.active_prompts, rubrics=found.active_rubrics),
        versions=_versions(found.entries),
        models=[
            ModelInfo(alias=m.alias, provider=m.provider, model=m.model, sha256=m.sha256)
            for m in found.models
        ],
        in_sync=not problems,
        problems=problems,
    )


class RegistryService:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def view(self) -> RegistryView:
        return await asyncio.to_thread(build_view, self._root)
