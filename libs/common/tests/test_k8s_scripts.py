"""scripts/k8s_up.sh must never touch a cluster that is not the project's own kind cluster.

A first run used kubectl's default context, which on a developer laptop was an unrelated k3s
cluster: the Calico operator was installed there. These tests pin the guard.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "k8s_up.sh"
GUARD = ROOT / "scripts" / "lib" / "kube_guard.sh"


def _shim(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _kubeconfig(context: str) -> str:
    return f"apiVersion: v1\nkind: Config\ncurrent-context: {context}\ncontexts: []\n"


def _run_guard(tmp_path: Path, kind_context: str) -> subprocess.CompletedProcess[str]:
    shims = tmp_path / "bin"
    shims.mkdir()
    # fake `kind get kubeconfig` prints a kubeconfig whose current-context we control
    _shim(shims, "kind", f"cat <<'YAML'\n{_kubeconfig(kind_context)}YAML\n")
    # fake `kubectl config current-context` reads the file KUBECONFIG points at
    _shim(shims, "kubectl", "grep '^current-context:' \"$KUBECONFIG\" | awk '{print $2}'\n")
    project = tmp_path / "project.kubeconfig"
    env = {
        **os.environ,
        "PATH": f"{shims}:{os.environ['PATH']}",
        "PROJECT_KUBECONFIG": str(project),
        "HOME": str(tmp_path),
    }
    script = f'source "{GUARD}"; use_project_cluster && echo "KUBECONFIG=$KUBECONFIG"'
    return subprocess.run(  # noqa: S603 (fixed bash path, script built from repo paths)
        ["bash", "-c", script],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_the_guard_exists_and_the_script_parses() -> None:
    assert GUARD.is_file()
    for path in (SCRIPT, GUARD):
        syntax = subprocess.run(["bash", "-n", str(path)], check=False)  # noqa: S603, S607
        assert syntax.returncode == 0


def test_the_guard_points_kubectl_at_a_dedicated_file_for_the_project_cluster(
    tmp_path: Path,
) -> None:
    done = _run_guard(tmp_path, "kind-idea-validator")
    assert done.returncode == 0, done.stderr
    assert f"KUBECONFIG={tmp_path / 'project.kubeconfig'}" in done.stdout
    assert (tmp_path / "project.kubeconfig").stat().st_mode & 0o077 == 0, "must be private"


@pytest.mark.parametrize("wrong", ["default", "k3s", "kind-other", "minikube", ""])
def test_the_guard_refuses_any_context_that_is_not_the_project_cluster(
    tmp_path: Path, wrong: str
) -> None:
    done = _run_guard(tmp_path, wrong)
    assert done.returncode != 0
    assert "refusing" in done.stderr.lower()
    assert "KUBECONFIG=" not in done.stdout


def test_the_script_fixes_kubeconfig_before_its_first_kubectl_and_uses_the_guard() -> None:
    lines = SCRIPT.read_text().splitlines()
    code = [(i, ln) for i, ln in enumerate(lines) if ln.strip() and not ln.strip().startswith("#")]
    first_kubectl = next(i for i, ln in code if "kubectl" in ln.split("#")[0])
    first_export = next(i for i, ln in code if "export KUBECONFIG" in ln or "kube_guard.sh" in ln)
    assert first_export < first_kubectl, "kubectl must never run before KUBECONFIG is pinned"
    text = SCRIPT.read_text()
    assert "use_project_cluster" in text
    assert "|| exit 1" in text.split("use_project_cluster", 1)[1].splitlines()[0]


def test_the_script_never_applies_to_the_default_context_by_name() -> None:
    text = SCRIPT.read_text()
    assert "--context" not in text or "kind-" in text
    assert "kubectl config use-context" not in text, "never change the user's current context"


def test_the_script_has_no_unbounded_wait_loops() -> None:
    text = SCRIPT.read_text()
    assert "until kubectl" not in text and "while true" not in text, "every wait needs a deadline"


def test_every_cluster_script_sources_the_guard_before_using_kubectl() -> None:
    for name in (
        "k8s_up.sh",
        "verify_cluster.sh",
        "create_secrets.sh",
        "deploy.sh",
        "smoke_test.sh",
        "netpol_test.sh",
    ):
        text = (ROOT / "scripts" / name).read_text()
        assert "lib/kube_guard.sh" in text, f"{name} must source the guard"
        body = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        guard = next(i for i, ln in enumerate(body) if "kube_guard.sh" in ln)
        kubectl_lines = [i for i, ln in enumerate(body) if ln.lstrip().startswith("kubectl")]
        assert not kubectl_lines or guard < kubectl_lines[0]
