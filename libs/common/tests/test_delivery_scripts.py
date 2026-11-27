"""Delivery scripts used by Jenkins: push check, rollback of what a build changed, guarded kubeconfig."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
GUARD = SCRIPTS / "lib" / "kube_guard.sh"


def _shim(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)  # noqa: S603


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    shims = tmp_path / "bin"
    shims.mkdir(exist_ok=True)
    return {
        **os.environ,
        "PATH": f"{shims}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "PROJECT_KUBECONFIG": str(tmp_path / "project.kubeconfig"),
        **extra,
    }


@pytest.mark.parametrize("name", ["push_images.sh", "rollback_changed.sh"])
def test_the_scripts_exist_parse_and_are_executable(name: str) -> None:
    path = SCRIPTS / name
    assert path.is_file() and os.access(path, os.X_OK)
    assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0  # noqa: S603, S607


# --- push_images.sh: every service image must be in the registry at the tag -----------------


def _registry_shim(tmp_path: Path, missing: str = "") -> dict[str, str]:
    env = _env(tmp_path)
    _shim(
        tmp_path / "bin",
        "curl",
        # the script asks <registry>/v2/<name>/tags/list; answer with the tag unless the name is "missing"
        f'url="${{@: -1}}"; name="$(echo "$url" | sed -E "s#.*/v2/([^/]+)/tags/list#\\1#")"\n'
        f'if [ "$name" = "{missing}" ]; then echo \'{{"name":"x","tags":["old"]}}\'; '
        'else echo "{\\"name\\":\\"$name\\",\\"tags\\":[\\"abc1234\\"]}"; fi\n',
    )
    return env


def test_push_check_passes_when_every_image_has_the_tag(tmp_path: Path) -> None:
    done = _run([str(SCRIPTS / "push_images.sh"), "abc1234"], _registry_shim(tmp_path))
    assert done.returncode == 0, done.stderr
    assert "gateway" in done.stdout and "kb" in done.stdout


def test_push_check_names_the_image_that_is_missing(tmp_path: Path) -> None:
    done = _run([str(SCRIPTS / "push_images.sh"), "abc1234"], _registry_shim(tmp_path, "kb"))
    assert done.returncode != 0
    assert "kb" in done.stderr and "abc1234" in done.stderr


@pytest.mark.parametrize("tag", ["latest", "", "bad tag", "a/b"])
def test_push_check_refuses_latest_and_malformed_tags(tmp_path: Path, tag: str) -> None:
    done = _run([str(SCRIPTS / "push_images.sh"), tag], _registry_shim(tmp_path))
    assert done.returncode != 0


# --- rollback_changed.sh: undo only what this build deployed -------------------------------


def _kubectl_log_shim(tmp_path: Path) -> tuple[dict[str, str], Path]:
    log = tmp_path / "kubectl.log"
    env = _env(tmp_path)
    _shim(tmp_path / "bin", "kind", "echo 'current-context: kind-idea-validator'\n")
    _shim(
        tmp_path / "bin",
        "kubectl",
        f'echo "$*" >> {log}\n'
        'case "$*" in\n'
        '  "config current-context") echo kind-idea-validator ;;\n'
        # name image revisions: gateway and kb run the new tag; critic runs an older one; scorer has one revision
        '  *"get deploy"*) printf "gateway idea-validator/gateway:newtag 3\\nkb idea-validator/kb:newtag 2\\n'
        'critic idea-validator/critic:oldtag 5\\nscorer idea-validator/scorer:newtag 1\\n" ;;\n'
        "esac\n",
    )
    return env, log


def test_rollback_undoes_only_deployments_running_the_new_tag_with_history(tmp_path: Path) -> None:
    env, log = _kubectl_log_shim(tmp_path)
    done = _run([str(SCRIPTS / "rollback_changed.sh"), "newtag"], env)
    assert done.returncode == 0, done.stderr
    calls = log.read_text()
    assert "rollout undo deployment/gateway" in calls
    assert "rollout undo deployment/kb" in calls
    assert "deployment/critic" not in calls, "an older tag was not changed by this build"
    assert "deployment/scorer" not in calls, "a first revision has nothing to roll back to"
    assert "rollout status deployment/gateway" in calls


def test_rollback_refuses_to_run_without_a_tag(tmp_path: Path) -> None:
    env, _ = _kubectl_log_shim(tmp_path)
    assert _run([str(SCRIPTS / "rollback_changed.sh")], env).returncode != 0


# --- the guard accepts a kubeconfig handed over by CI, but only for the project cluster -----


def _guard_with_file(tmp_path: Path, context: str) -> subprocess.CompletedProcess[str]:
    env = _env(tmp_path)
    given = tmp_path / "ci.kubeconfig"
    given.write_text(f"apiVersion: v1\nkind: Config\ncurrent-context: {context}\n")
    env["CI_KUBECONFIG"] = str(given)
    _shim(
        tmp_path / "bin", "kubectl", "grep '^current-context:' \"$KUBECONFIG\" | awk '{print $2}'\n"
    )
    # no `kind` binary on purpose: inside Jenkins there is none
    _shim(tmp_path / "bin", "kind", "exit 1\n")
    script = f'source "{GUARD}"; use_project_cluster && echo "KUBECONFIG=$KUBECONFIG"'
    return _run(["bash", "-c", script], env)


def test_the_guard_uses_the_ci_kubeconfig_for_the_project_cluster(tmp_path: Path) -> None:
    done = _guard_with_file(tmp_path, "kind-idea-validator")
    assert done.returncode == 0, done.stderr
    assert str(tmp_path / "project.kubeconfig") in done.stdout


@pytest.mark.parametrize("wrong", ["default", "k3s", "kind-other"])
def test_the_guard_still_refuses_a_foreign_ci_kubeconfig(tmp_path: Path, wrong: str) -> None:
    done = _guard_with_file(tmp_path, wrong)
    assert done.returncode != 0 and "refusing" in done.stderr.lower()
