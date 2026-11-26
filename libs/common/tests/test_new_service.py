import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "new_service.py"


def _render(name: str, module: str, title: str, out: Path) -> Path:
    spec = importlib.util.spec_from_file_location("new_service", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["new_service"] = mod
    spec.loader.exec_module(mod)
    result: Path = mod.render(name, module, title, services_dir=out)
    return result


def test_generated_service_has_every_expected_file_with_names_filled_in(tmp_path: Path) -> None:
    root = _render("kb", "M6", "Knowledge Base", tmp_path)
    expected = [
        "Dockerfile",
        "README.md",
        "pyproject.toml",
        "src/svc_kb/main.py",
        "src/svc_kb/config.py",
        "src/svc_kb/api/routes.py",
        "src/svc_kb/services/__init__.py",
        "src/svc_kb/repositories/__init__.py",
        "src/svc_kb/clients/__init__.py",
        "src/svc_kb/models/__init__.py",
        "tests/unit/test_kb_probes.py",
    ]
    for relative in expected:
        assert (root / relative).is_file(), relative
    for path in root.rglob("*"):
        if path.is_file():
            assert "{{" not in path.read_text(), path


def test_dockerfile_is_non_root_multi_stage_and_pinned(tmp_path: Path) -> None:
    text = (_render("kb", "M6", "Knowledge Base", tmp_path) / "Dockerfile").read_text()
    assert text.count("FROM ") == 2
    assert "USER 10001" in text
    assert ":latest" not in text
    assert "svc_kb.main:app" in text


def test_refuses_to_overwrite_and_rejects_bad_names(tmp_path: Path) -> None:
    _render("kb", "M6", "Knowledge Base", tmp_path)
    with pytest.raises(SystemExit):
        _render("kb", "M6", "Knowledge Base", tmp_path)
    with pytest.raises(SystemExit):
        _render("Bad-Name", "M1", "x", tmp_path)
