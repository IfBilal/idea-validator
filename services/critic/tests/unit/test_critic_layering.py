"""Layering by construction: api -> services -> clients; services stay free of the web framework.

(The root import-linter config is owned by the coordinator; this test enforces the same rules.)
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "svc_critic"
WEB = {"fastapi", "starlette"}


def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            found.add(f"{prefix}{node.module or ''}")
    return found


def files(layer: str) -> list[Path]:
    return sorted((SRC / layer).glob("*.py"))


@pytest.mark.parametrize("layer", ["services", "clients", "models"])
def test_lower_layers_do_not_import_the_web_framework(layer: str) -> None:
    for path in files(layer):
        roots = {i.split(".")[0] for i in imports_of(path) if not i.startswith(".")}
        assert not roots & WEB, f"{path.name} imports the web framework"


def test_routes_do_not_reach_into_clients() -> None:
    for path in files("api"):
        assert not any(i.startswith("..clients") for i in imports_of(path)), path.name


@pytest.mark.parametrize("layer", ["services", "clients", "models"])
def test_lower_layers_never_import_api(layer: str) -> None:
    for path in files(layer):
        assert not any(i.startswith("..api") for i in imports_of(path)), path.name


def test_models_import_nothing_from_services_or_clients() -> None:
    for path in files("models"):
        bad = [i for i in imports_of(path) if i.startswith(("..services", "..clients"))]
        assert not bad, path.name


def test_services_do_not_make_network_calls_directly() -> None:
    for path in files("services"):
        roots = {i.split(".")[0] for i in imports_of(path) if not i.startswith(".")}
        assert not roots & {"httpx", "requests", "aiohttp", "urllib3"}, path.name
