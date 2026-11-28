"""GET /registry: every prompt, rubric, config version and model alias with its content hash."""

import shutil
from pathlib import Path

import pytest

from common import registry
from svc_agentops.testing import Rig

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[4]


def make_registry(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    for name in ("prompts", "rubrics", "config"):
        shutil.copytree(ROOT / name, root / name)
    shutil.copy(ROOT / registry.LOCK_NAME, root / registry.LOCK_NAME)
    return root


async def test_registry_requires_the_internal_token(tmp_path: Path) -> None:
    rig = Rig(tmp_path, registry_dir=make_registry(tmp_path))
    async with rig.client() as client:
        client.headers.clear()
        assert (await client.get("/registry")).status_code == 401


async def test_registry_lists_every_version_with_hashes(tmp_path: Path) -> None:
    rig = Rig(tmp_path, registry_dir=make_registry(tmp_path))
    async with rig.client() as client:
        body = (await client.get("/registry")).json()
    keys = {f"{e['kind']}:{e['name']}@{e['version']}" for e in body["entries"]}
    assert {"prompt:scorer@v1", "rubric:risk@v1", "config:verdict.weights@w1"} <= keys
    scorer = next(e for e in body["entries"] if e["name"] == "scorer" and e["kind"] == "prompt")
    assert scorer["sha256"] == registry.sha256_bytes(
        (ROOT / "prompts" / "scorer@v1.md").read_bytes()
    )
    assert len(scorer["sha256"]) == 64
    assert body["active"]["prompts"]["scorer"] == "scorer@v1"
    assert body["active"]["rubrics"]["risk"] == "risk@v1"
    assert body["versions"] == {
        "engine": "m11@v1",
        "weights": "w1",
        "thresholds": "t1",
        "rubric_set": "r1",
        "budgets": "b1",
        "prompts": "p1",
        "llm": "l2",
    }
    assert {m["alias"] for m in body["models"]} >= {"hosted-default"}
    assert all(len(m["sha256"]) == 64 and "api_key" not in str(m) for m in body["models"])
    assert body["in_sync"] is True and body["problems"] == []


async def test_registry_reports_drift_from_the_lock(tmp_path: Path) -> None:
    root = make_registry(tmp_path)
    (root / "prompts" / "scorer@v1.md").write_text("edited in place\n")
    rig = Rig(tmp_path, registry_dir=root)
    async with rig.client() as client:
        body = (await client.get("/registry")).json()
    assert body["in_sync"] is False
    assert any("edited in place" in p and "scorer@v1" in p for p in body["problems"])


async def test_registry_without_a_lock_is_not_in_sync_but_still_lists(tmp_path: Path) -> None:
    root = make_registry(tmp_path)
    (root / registry.LOCK_NAME).unlink()
    rig = Rig(tmp_path, registry_dir=root)
    async with rig.client() as client:
        response = await client.get("/registry")
    assert response.status_code == 200
    assert response.json()["in_sync"] is False
    assert response.json()["entries"]


async def test_registry_with_a_missing_directory_is_a_typed_503(tmp_path: Path) -> None:
    rig = Rig(tmp_path, registry_dir=tmp_path / "nowhere")
    async with rig.client() as client:
        response = await client.get("/registry")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"


async def test_registry_never_returns_file_contents(tmp_path: Path) -> None:
    root = make_registry(tmp_path)
    rig = Rig(tmp_path, registry_dir=root)
    async with rig.client() as client:
        text = (await client.get("/registry")).text
    assert (ROOT / "prompts" / "scorer@v1.md").read_text()[:80] not in text
