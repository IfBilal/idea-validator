"""Delivery scripts used by Jenkins: push check, rollback of what a build changed, guarded kubeconfig."""

import os
import re
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


@pytest.mark.parametrize(
    "name",
    ["push_images.sh", "rollback_changed.sh", "run_eval.sh", "jenkins_up.sh", "check_eval.sh"],
)
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
    assert f"refusing image tag '{tag}'" in done.stderr


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
    done = _run([str(SCRIPTS / "rollback_changed.sh")], env)
    assert done.returncode != 0
    assert "refusing image tag ''" in done.stderr


# --- the guard accepts a kubeconfig handed over by CI, but only for the project cluster -----


def _guard_with_file(
    tmp_path: Path,
    context: str,
    server: str = "https://127.0.0.1:41234",
    extra: str = "",
    previous: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = _env(tmp_path)
    if previous is not None:
        (tmp_path / "project.kubeconfig").write_text(previous)
    given = tmp_path / "ci.kubeconfig"
    given.write_text(
        f"apiVersion: v1\nkind: Config\ncurrent-context: {context}\n"
        f"clusters:\n- cluster:\n    server: {server}\n  name: c\n{extra}"
    )
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


def test_the_guard_never_overwrites_the_project_kubeconfig_when_it_refuses(tmp_path: Path) -> None:
    done = _guard_with_file(tmp_path, "k3s", previous="KEEP-ME\n")
    assert done.returncode != 0
    assert (tmp_path / "project.kubeconfig").read_text() == "KEEP-ME\n"


def _leftovers(tmp_path: Path) -> list[str]:
    return [p.name for p in tmp_path.glob("project.kubeconfig.*")]


@pytest.mark.parametrize(
    "server",
    ["https://10.0.0.5:6443", "https://example.com", "https://127.0.0.1.evil.com:6443"],
)
def test_the_guard_refuses_a_ci_kubeconfig_pointing_at_a_non_local_server(
    tmp_path: Path, server: str
) -> None:
    done = _guard_with_file(tmp_path, "kind-idea-validator", server=server, previous="KEEP\n")
    assert done.returncode != 0
    assert "server" in done.stderr.lower()
    assert (tmp_path / "project.kubeconfig").read_text() == "KEEP\n"
    assert _leftovers(tmp_path) == []


@pytest.mark.parametrize(
    "server",
    [
        "https://127.0.0.1:41234",
        "https://localhost:6443",
        "https://idea-validator-control-plane:6443",
    ],
)
def test_the_guard_accepts_the_local_servers_of_a_kind_cluster(tmp_path: Path, server: str) -> None:
    assert _guard_with_file(tmp_path, "kind-idea-validator", server=server).returncode == 0


@pytest.mark.parametrize(
    "extra",
    [
        "users:\n- name: u\n  user:\n    exec:\n      command: /bin/evil\n",
        "users:\n- name: u\n  user:\n    auth-provider:\n      name: gcp\n",
    ],
)
def test_the_guard_refuses_a_kubeconfig_with_a_credential_plugin(
    tmp_path: Path, extra: str
) -> None:
    done = _guard_with_file(tmp_path, "kind-idea-validator", extra=extra, previous="KEEP\n")
    assert done.returncode != 0
    assert "plugin" in done.stderr.lower()
    assert (tmp_path / "project.kubeconfig").read_text() == "KEEP\n"
    assert _leftovers(tmp_path) == []


def test_the_guard_leaves_no_temp_file_after_a_wrong_context(tmp_path: Path) -> None:
    assert _guard_with_file(tmp_path, "default").returncode != 0
    assert _leftovers(tmp_path) == []


def test_rollback_refuses_a_foreign_ci_kubeconfig(tmp_path: Path) -> None:
    env, log = _kubectl_log_shim(tmp_path)
    given = tmp_path / "ci.kubeconfig"
    given.write_text(
        "current-context: kind-other\nclusters:\n- cluster:\n    server: https://127.0.0.1:1\n"
    )
    env["CI_KUBECONFIG"] = str(given)
    _shim(
        tmp_path / "bin",
        "kubectl",
        f'echo "$*" >> {log}\ngrep "^current-context:" "$KUBECONFIG" | cut -d" " -f2\n',
    )
    done = _run([str(SCRIPTS / "rollback_changed.sh"), "newtag"], env)
    assert done.returncode != 0 and "refusing" in done.stderr.lower()
    assert "rollout undo" not in log.read_text()


# --- Jenkinsfile, plugins, image pins: static checks -------------------------------------------

JENKINSFILE = (ROOT / "Jenkinsfile").read_text()
JENKINS = ROOT / "jenkins"


def _sh_steps() -> list[str]:
    return re.findall(r"sh\s+'([^']*)'", JENKINSFILE)


def test_no_jenkinsfile_step_runs_kubectl_directly() -> None:
    assert "kubectl" not in "\n".join(_sh_steps()), "go through a script that sources kube_guard"


def test_every_script_a_jenkins_step_runs_is_guarded_when_it_uses_kubectl() -> None:
    scripts = {m for step in _sh_steps() for m in re.findall(r"\./scripts/([\w.]+\.sh)", step)}
    assert {"deploy.sh", "smoke_test.sh", "rollback_changed.sh", "run_eval.sh"} <= scripts
    for name in scripts:
        text = (SCRIPTS / name).read_text()
        if "kubectl" in text or name == "run_eval.sh":
            assert "lib/kube_guard.sh" in text and "use_project_cluster" in text, name


def test_every_kubernetes_stage_gets_the_ci_kubeconfig() -> None:
    for stage in ("Deploy", "Smoke", "Eval"):
        body = JENKINSFILE.split(f"stage('{stage}')", 1)[1].split("stage(", 1)[0]
        assert "variable: 'CI_KUBECONFIG'" in body, stage
    post = JENKINSFILE.split("post {", 1)[1]
    assert "CI_KUBECONFIG" in post and "rollback_changed.sh" in post


def test_the_rollback_in_post_failure_can_fail_visibly() -> None:
    post = JENKINSFILE.split("post {", 1)[1]
    assert "|| true" not in post
    assert "catchError" in post


def test_the_tag_comes_from_git_after_checkout_not_from_git_commit() -> None:
    assert "GIT_COMMIT.take" not in JENKINSFILE
    assert "git rev-parse --short=7 HEAD" in JENKINSFILE


def test_every_plugin_is_pinned_to_a_version() -> None:
    lines = [ln.strip() for ln in (JENKINS / "plugins.txt").read_text().splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    assert lines
    for ln in lines:
        name, sep, version = ln.partition(":")
        assert sep == ":" and name and version and version != "latest", f"unpinned plugin: {ln}"


def test_the_base_images_are_pinned_by_digest() -> None:
    lines = [ln.strip().upper() for ln in (JENKINS / "Dockerfile").read_text().splitlines()]
    raw = [ln.strip() for ln in (JENKINS / "Dockerfile").read_text().splitlines()]
    froms = [r for r, u in zip(raw, lines, strict=True) if u.startswith(("FROM", "COPY --FROM"))]
    assert len(froms) >= 2
    for ln in froms:
        assert "@sha256:" in ln, f"not pinned by digest: {ln}"


def test_the_docker_tarball_is_checked_against_a_pinned_sha256() -> None:
    text = (JENKINS / "Dockerfile").read_text()
    assert re.search(r"ARG DOCKER_SHA256=[0-9a-f]{64}", text)
    assert "sha256sum --check --strict" in text.split("docker.tgz", 1)[1]


def test_casc_reads_the_password_from_a_file_and_runs_one_executor() -> None:
    casc = (JENKINS / "casc.yaml").read_text()
    assert "${JENKINS_ADMIN_PASSWORD}" not in casc
    assert "readFile:/run/secrets/admin-password" in casc
    assert "numExecutors: 1" in casc


def test_make_images_builds_every_image_push_images_checks() -> None:
    makefile = (ROOT / "Makefile").read_text()
    images = makefile.split("\nimages:", 1)[1].split("\nk8s-up:", 1)[0]
    push = (SCRIPTS / "push_images.sh").read_text()
    for extra in ("ui", "ingestion"):
        if f'services="$services {extra}"' in push:
            assert f"localhost:5001/{extra}:" in images, f"push check expects {extra}"


# --- run_eval.sh and check_eval.sh go through the guard ------------------------------------


def _eval_env(tmp_path: Path, context: str) -> tuple[dict[str, str], Path]:
    log = tmp_path / "calls.log"
    env = _env(tmp_path, EVAL_MANIFEST=str(ROOT / "Makefile"), BASELINE=str(ROOT / "Makefile"))
    _shim(tmp_path / "bin", "kind", f"echo 'current-context: {context}'\n")
    _shim(
        tmp_path / "bin",
        "kubectl",
        f'echo "$*" >> {log}\ngrep "^current-context:" "$KUBECONFIG" | cut -d" " -f2\n',
    )
    _shim(tmp_path / "bin", "uv", f'echo "uv $*" >> {log}\n')
    return env, log


def test_run_eval_applies_the_job_and_gates_through_the_guard(tmp_path: Path) -> None:
    env, log = _eval_env(tmp_path, "kind-idea-validator")
    done = _run([str(SCRIPTS / "run_eval.sh"), str(ROOT / "Makefile")], env)
    assert done.returncode == 0, done.stderr
    calls = log.read_text()
    assert "apply -f" in calls and "-n idea-validator" in calls


def test_run_eval_refuses_a_foreign_cluster_before_applying(tmp_path: Path) -> None:
    env, log = _eval_env(tmp_path, "k3s")
    done = _run([str(SCRIPTS / "run_eval.sh"), str(ROOT / "Makefile")], env)
    assert done.returncode != 0 and "refusing" in done.stderr.lower()
    assert "apply" not in (log.read_text() if log.exists() else "")


def test_check_eval_sources_the_guard() -> None:
    text = (SCRIPTS / "check_eval.sh").read_text()
    assert "lib/kube_guard.sh" in text and "use_project_cluster" in text


# --- jenkins_up.sh with fake docker/kind/kubectl/curl ----------------------------------------


def _jenkins_env(
    tmp_path: Path, context: str = "kind-idea-validator", **extra: str
) -> tuple[dict[str, str], Path]:
    log = tmp_path / "docker.log"
    env = _env(
        tmp_path,
        XDG_CONFIG_HOME=str(tmp_path / "cfg"),
        DOCKER_SOCK=str(tmp_path / "docker.sock"),
        **extra,
    )
    (tmp_path / "docker.sock").write_text("")
    _shim(tmp_path / "bin", "docker", f'echo "$*" >> {log}\n')
    _shim(
        tmp_path / "bin",
        "kind",
        f"printf 'current-context: {context}\\nclusters:\\n- cluster:\\n"
        "    server: https://127.0.0.1:1\\n'\n",
    )
    _shim(tmp_path / "bin", "kubectl", 'grep "^current-context:" "$KUBECONFIG" | cut -d" " -f2\n')
    _shim(tmp_path / "bin", "curl", "echo 200\n")
    return env, log


def test_jenkins_up_refuses_a_foreign_cluster_and_starts_nothing(tmp_path: Path) -> None:
    env, log = _jenkins_env(tmp_path, context="k3s")
    done = _run([str(SCRIPTS / "jenkins_up.sh")], env)
    assert done.returncode != 0 and "refusing" in done.stderr.lower()
    assert not log.exists()


def test_jenkins_up_keeps_the_password_in_a_private_file_not_in_the_environment(
    tmp_path: Path,
) -> None:
    env, log = _jenkins_env(tmp_path)
    done = _run([str(SCRIPTS / "jenkins_up.sh")], env)
    assert done.returncode == 0, done.stderr
    state = tmp_path / "cfg" / "idea-validator-jenkins"
    password = state / "admin-password"
    assert stat.S_IMODE(password.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    run_line = next(ln for ln in log.read_text().splitlines() if ln.startswith("run "))
    assert password.read_text().strip() not in run_line
    assert "JENKINS_ADMIN_PASSWORD" not in run_line
    assert f"{password}:/run/secrets/admin-password:ro" in run_line


@pytest.mark.parametrize(
    "branch", ["main", "*/main'); evil(); ('", "*/a b", "*/$(id)", "*/", "*/a;b", "*/../x"]
)
def test_jenkins_up_rejects_a_malformed_branch_before_touching_docker(
    tmp_path: Path, branch: str
) -> None:
    env, log = _jenkins_env(tmp_path, JENKINS_BRANCH=branch)
    done = _run([str(SCRIPTS / "jenkins_up.sh")], env)
    assert done.returncode != 0 and "JENKINS_BRANCH" in done.stderr
    assert not log.exists()


@pytest.mark.parametrize("branch", ["*/main", "*/feat/jenkins-pipeline", "*/release_1.2"])
def test_jenkins_up_accepts_a_normal_branch(tmp_path: Path, branch: str) -> None:
    env, log = _jenkins_env(tmp_path, JENKINS_BRANCH=branch)
    done = _run([str(SCRIPTS / "jenkins_up.sh")], env)
    assert done.returncode == 0, done.stderr
    assert f"JENKINS_BRANCH={branch}" in log.read_text()


# --- the kb pod has no internet: its models must ship inside the image ------------------------


def test_the_kb_image_bakes_the_models_in_and_runs_offline() -> None:
    text = (ROOT / "services" / "kb" / "Dockerfile").read_text()
    assert "FastEmbedModels('/models')" in text, "download the three models at build time"
    assert "MODEL_CACHE_DIR=/models" in text and "HF_HUB_OFFLINE=1" in text


def test_the_kb_deployment_asks_for_enough_memory_to_hold_three_onnx_models() -> None:
    text = (ROOT / "k8s" / "base" / "services" / "kb.yaml").read_text()
    assert "memory: 2Gi" in text and "memory: 768Mi" in text
