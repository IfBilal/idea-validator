"""Layering by construction: api -> services -> clients/repositories; services stay web-free.

(The root import-linter config is owned by the coordinator; this test enforces the same rules.)
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "svc_analyst"
WEB = {"fastapi", "starlette"}
NETWORK = {"httpx", "requests", "aiohttp", "urllib3"}


def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(f"{'.' * node.level}{node.module or ''}")
    return found


def files(layer: str) -> list[Path]:
    return sorted((SRC / layer).glob("*.py"))


@pytest.mark.parametrize("layer", ["services", "clients", "models", "repositories"])
def test_lower_layers_do_not_import_the_web_framework(layer: str) -> None:
    for path in files(layer):
        roots = {i.split(".")[0] for i in imports_of(path) if not i.startswith(".")}
        assert not roots & WEB, f"{path.name} imports the web framework"


@pytest.mark.parametrize("layer", ["services", "clients", "models", "repositories"])
def test_lower_layers_never_import_api(layer: str) -> None:
    for path in files(layer):
        assert not any(i.startswith("..api") for i in imports_of(path)), path.name


def test_routes_do_not_reach_into_clients_or_repositories() -> None:
    for path in files("api"):
        bad = [i for i in imports_of(path) if i.startswith(("..clients", "..repositories"))]
        assert not bad, path.name


def test_models_import_nothing_from_services_clients_or_repositories() -> None:
    for path in files("models"):
        bad = [i for i in imports_of(path) if i.startswith(("..services", "..clients", ".."))]
        assert not [b for b in bad if not b.startswith("..models")], path.name


def test_pure_services_do_no_io_and_never_import_a_clock_or_randomness() -> None:
    for name in ("features", "clustering", "gaps", "text"):
        roots = {i.split(".")[0] for i in imports_of(SRC / "services" / f"{name}.py")}
        assert not roots & NETWORK, name
        assert not roots & {"random", "time", "datetime.now"}, name
        source = (SRC / "services" / f"{name}.py").read_text()
        assert "now(" not in source and "today(" not in source, name


def test_services_make_no_network_calls_directly() -> None:
    for path in files("services"):
        roots = {i.split(".")[0] for i in imports_of(path) if not i.startswith(".")}
        assert not roots & NETWORK, path.name
