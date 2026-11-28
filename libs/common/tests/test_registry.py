"""Registry rules (docs/harness/12 section 4): versions are immutable, config changes need a bump."""

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest

from common import registry

ROOT = Path(__file__).resolve().parents[3]
VERDICT = """\
engine_version: "m11@v1"
weights_version: "w1"
thresholds_version: "t1"
rubric_set: "r1"
rubric_versions: {demand_pain: "demand_pain@v1"}
weights: {startup: {demand_pain: 100}}
thresholds: {T_go: 70, T_no: 50}
confidence: {min: 0.5}
"""


def build(root: Path) -> Path:
    (root / "prompts").mkdir(parents=True)
    (root / "rubrics").mkdir()
    (root / "config").mkdir()
    (root / "prompts" / "scorer@v1.md").write_text("score it\n")
    (root / "rubrics" / "demand_pain@v1.yaml").write_text("dimension: demand_pain\n")
    (root / "config" / "verdict.yaml").write_text(VERDICT)
    (root / "config" / "budgets.yaml").write_text('version: "b1"\nvalidate: {max_steps: 80}\n')
    (root / "config" / "prompts.yaml").write_text('version: "p1"\nactive: {scorer: "scorer@v1"}\n')
    (root / "config" / "llm.yaml").write_text(
        'version: "l1"\naliases:\n  fast: {provider: groq, model: m-1, api_key_env: K}\n'
    )
    return root


def script() -> ModuleType:
    path = ROOT / "scripts" / "check_registry.py"
    spec = importlib.util.spec_from_file_location("check_registry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(root: Path, *args: str) -> int:
    return int(script().main(["--root", str(root), *args]))


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = build(tmp_path / "repo")
    assert run(root, "--update") == 0
    return root


# ---- scan --------------------------------------------------------------------------------------
def test_scan_lists_prompts_rubrics_configs_and_models(tmp_path: Path) -> None:
    result = registry.scan(build(tmp_path))
    assert result.problems == []
    assert {
        "prompt:scorer@v1",
        "rubric:demand_pain@v1",
        "config:verdict.engine@m11@v1",
        "config:verdict.weights@w1",
        "config:verdict.thresholds@t1",
        "config:verdict.rubric_set@r1",
        "config:budgets@b1",
        "config:prompts@p1",
        "config:llm@l1",
    } == set(result.entries)
    prompt = result.entries["prompt:scorer@v1"]
    assert prompt.sha256 == registry.sha256_bytes(b"score it\n")
    assert prompt.path == "prompts/scorer@v1.md"
    assert [(m.alias, m.model) for m in result.models] == [("fast", "m-1")]
    assert result.active_prompts == {"scorer": "scorer@v1"}


def test_comments_in_config_do_not_change_its_hash(tmp_path: Path) -> None:
    root = build(tmp_path)
    before = registry.scan(root).entries["config:verdict.weights@w1"].sha256
    text = (root / "config/verdict.yaml").read_text().replace("weights:", "# note\nweights:")
    (root / "config/verdict.yaml").write_text(text)
    assert registry.scan(root).entries["config:verdict.weights@w1"].sha256 == before


def test_an_empty_tree_scans_to_nothing(tmp_path: Path) -> None:
    result = registry.scan(tmp_path)
    assert result.entries == {} and result.problems == []


@pytest.mark.parametrize(
    "name", ["scorer.md", "scorer@v0.md", "Scorer@v1.md", "scorer@v1.txt", "@v1.md"]
)
def test_unversioned_prompt_files_are_reported(tmp_path: Path, name: str) -> None:
    root = build(tmp_path)
    (root / "prompts" / name).write_text("x")
    assert any(name in p for p in registry.scan(root).problems)


def test_a_symlinked_prompt_is_not_followed(tmp_path: Path) -> None:
    root = build(tmp_path)
    (root / "prompts" / "evil@v1.md").symlink_to("/etc/hostname")
    assert any("evil@v1.md" in p for p in registry.scan(root).problems)


def test_config_pointing_at_a_missing_version_is_reported(tmp_path: Path) -> None:
    root = build(tmp_path)
    (root / "config/prompts.yaml").write_text('version: "p1"\nactive: {scorer: "scorer@v9"}\n')
    assert any("scorer@v9" in p for p in registry.scan(root).problems)


def test_invalid_yaml_and_missing_version_key_are_problems_not_crashes(tmp_path: Path) -> None:
    root = build(tmp_path)
    (root / "config/budgets.yaml").write_text("version: [unclosed\n")
    (root / "config/llm.yaml").write_text("aliases: {}\n")
    problems = registry.scan(root).problems
    assert any("budgets.yaml" in p for p in problems)
    assert any("llm" in p and "version" in p for p in problems)


# ---- compare / CLI -----------------------------------------------------------------------------
def test_an_untouched_tree_passes(tree: Path) -> None:
    assert run(tree) == 0


def test_editing_a_prompt_in_place_fails_and_names_the_file(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "prompts/scorer@v1.md").write_text("score it differently\n")
    assert run(tree) == 1
    err = capsys.readouterr().err
    assert "edited in place" in err and "prompt:scorer@v1" in err and "next version" in err


def test_editing_a_rubric_in_place_fails(tree: Path) -> None:
    (tree / "rubrics/demand_pain@v1.yaml").write_text("dimension: demand_pain\nweight: 2\n")
    assert run(tree) == 1


def test_a_new_version_passes_once_registered_and_the_old_one_is_untouched(tree: Path) -> None:
    shutil.copy(tree / "prompts/scorer@v1.md", tree / "prompts/scorer@v2.md")
    (tree / "prompts/scorer@v2.md").write_text("score it better\n")
    assert run(tree) == 1  # not registered yet: the lock must be updated in the same PR
    assert run(tree, "--update") == 0
    assert run(tree) == 0
    lock = json.loads((tree / "registry.lock.json").read_text())["entries"]
    assert "prompt:scorer@v1" in lock and "prompt:scorer@v2" in lock


def test_deleting_a_version_fails_even_when_a_newer_one_exists(tree: Path) -> None:
    shutil.copy(tree / "prompts/scorer@v1.md", tree / "prompts/scorer@v2.md")
    assert run(tree, "--update") == 0
    (tree / "prompts/scorer@v1.md").unlink()
    assert run(tree) == 1


def test_renaming_a_version_counts_as_a_deletion(tree: Path) -> None:
    (tree / "prompts/scorer@v1.md").rename(tree / "prompts/scorer@v3.md")
    assert run(tree) == 1


def test_changing_config_content_without_a_bump_fails(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "config/verdict.yaml").write_text(VERDICT.replace("T_go: 70", "T_go: 75"))
    assert run(tree) == 1
    err = capsys.readouterr().err
    assert "changed without a version bump" in err and "verdict.thresholds@t1" in err


def test_changing_weights_with_a_bump_passes_after_update(tree: Path) -> None:
    changed = VERDICT.replace("demand_pain: 100", "demand_pain: 99").replace('"w1"', '"w2"')
    (tree / "config/verdict.yaml").write_text(changed)
    assert run(tree, "--update") == 0
    assert run(tree) == 0  # the superseded w1 entry stays in the lock as history


def test_changing_budgets_without_a_bump_fails(tree: Path) -> None:
    (tree / "config/budgets.yaml").write_text('version: "b1"\nvalidate: {max_steps: 90}\n')
    assert run(tree) == 1


def test_update_refuses_to_launder_an_in_place_edit(tree: Path) -> None:
    (tree / "prompts/scorer@v1.md").write_text("sneaky\n")
    before = (tree / "registry.lock.json").read_text()
    assert run(tree, "--update") == 1
    assert (tree / "registry.lock.json").read_text() == before


def test_update_refuses_after_a_deletion(tree: Path) -> None:
    (tree / "rubrics/demand_pain@v1.yaml").unlink()
    assert run(tree, "--update") == 1


def test_update_is_idempotent_and_deterministic(tree: Path) -> None:
    first = (tree / "registry.lock.json").read_text()
    assert run(tree, "--update") == 0
    assert (tree / "registry.lock.json").read_text() == first
    keys = list(json.loads(first)["entries"])
    assert first.endswith("\n") and keys == sorted(keys)


def test_a_missing_lock_fails_check_and_is_created_by_update(tmp_path: Path) -> None:
    root = build(tmp_path)
    assert run(root) == 1
    assert run(root, "--update") == 0
    assert (root / "registry.lock.json").exists()


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"schema": 2, "entries": {}}', '{"schema": 1, "entries": {"a": 1}}'],
)
def test_a_corrupt_lock_is_exit_2(tree: Path, content: str) -> None:
    (tree / "registry.lock.json").write_text(content)
    assert run(tree) == 2


def test_a_malformed_tree_fails_even_with_update(tmp_path: Path) -> None:
    root = build(tmp_path)
    (root / "prompts" / "loose.md").write_text("x")
    assert run(root, "--update") == 1
    assert not (root / "registry.lock.json").exists()


def test_stress_hundreds_of_versions_scan_and_compare(tmp_path: Path) -> None:
    root = build(tmp_path)
    for n in range(2, 402):
        (root / "prompts" / f"scorer@v{n}.md").write_text(f"v{n}\n")
    assert run(root, "--update") == 0
    assert run(root) == 0
    (root / "prompts/scorer@v250.md").write_text("tampered\n")
    assert run(root) == 1


# ---- the real repository -----------------------------------------------------------------------
def test_the_committed_lock_matches_the_repository() -> None:
    found = registry.scan(ROOT)
    assert found.problems == []
    assert registry.compare(registry.load_lock(ROOT / registry.LOCK_NAME), found.entries) == []
    assert {"prompt:scorer@v1", "rubric:risk@v1"} <= set(found.entries)
