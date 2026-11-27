"""Checks the configured model aliases against the provider's real model list (needs a key).

Skipped unless GROQ_API_KEY is set, so CI never calls out. Run it before a demo or a release:
    GROQ_API_KEY=... uv run pytest services/tools/tests/live -q -p no:warnings -o addopts=
A provider retiring a model turns every /llm call into a 502; mocked tests cannot see that.
"""

import os
from pathlib import Path

import httpx
import pytest

from svc_tools.config_llm import load_llm_config
from svc_tools.config_sources import load_sources

CONFIG = Path(__file__).resolve().parents[4] / "config"
KEY = os.environ.get("GROQ_API_KEY")

pytestmark = pytest.mark.skipif(not KEY, reason="GROQ_API_KEY not set")


def _groq_aliases() -> list[tuple[str, str]]:
    cfg = load_llm_config(load_sources(CONFIG).allow_list, CONFIG)
    return [(n, a.model) for n, a in cfg.aliases.items() if a.provider == "groq"]


@pytest.mark.parametrize(("alias", "model"), _groq_aliases())
def test_every_configured_groq_model_still_exists_and_is_active(alias: str, model: str) -> None:
    response = httpx.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=20,
    )
    response.raise_for_status()
    active = {m["id"] for m in response.json()["data"] if m.get("active", True)}
    assert model in active, f"alias {alias!r} points at {model!r}, which Groq no longer serves"
