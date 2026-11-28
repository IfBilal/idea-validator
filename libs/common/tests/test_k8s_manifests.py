"""The Kubernetes base is generated from one table (scripts/gen_k8s.py) and must stay hardened.

These tests encode docs/harness/16_KUBERNETES.md: pod hardening, no :latest, measured-later resources,
probes, one NetworkPolicy pair per call edge, and "only M5 reaches the internet".
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "k8s" / "base"


def _gen() -> Any:
    spec = importlib.util.spec_from_file_location("gen_k8s", ROOT / "scripts" / "gen_k8s.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _docs(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _all_objects() -> list[dict[str, Any]]:
    return [d for p in sorted((ROOT / "k8s").rglob("*.yaml")) for d in _docs(p) if "kind" in d]


def _deployments() -> list[dict[str, Any]]:
    return [o for o in _all_objects() if o["kind"] == "Deployment"]


def test_generated_files_are_up_to_date() -> None:
    gen = _gen()
    for rel, text in gen.render().items():
        on_disk = (ROOT / rel).read_text() if (ROOT / rel).exists() else None
        assert on_disk == text, f"{rel} is stale: run `python scripts/gen_k8s.py`"


def test_every_service_directory_has_manifests() -> None:
    names = {p.name for p in (ROOT / "services").iterdir() if (p / "Dockerfile").exists()}
    deployed = {o["metadata"]["name"] for o in _deployments()}
    assert names <= deployed, f"no Deployment for: {sorted(names - deployed)}"


def test_the_namespace_enforces_the_restricted_pod_security_standard() -> None:
    (ns,) = [o for o in _all_objects() if o["kind"] == "Namespace"]
    labels = ns["metadata"]["labels"]
    assert ns["metadata"]["name"] == "idea-validator"
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"


@pytest.mark.parametrize("dep", _deployments(), ids=lambda d: d["metadata"]["name"])
def test_every_deployment_is_hardened(dep: dict[str, Any]) -> None:
    spec = dep["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["serviceAccountName"] == "app"
    is_ui = dep["metadata"]["name"] == "ui"  # nginx-unprivileged: its own uid, /healthz, no /ready
    pod = spec["securityContext"]
    assert pod["runAsNonRoot"] is True and pod["runAsUser"] == (101 if is_ui else 10001)
    assert pod["seccompProfile"]["type"] == "RuntimeDefault"
    if any("persistentVolumeClaim" in v for v in spec["volumes"]):
        # a ReadWriteOnce volume cannot be mounted by a surge pod on another node: hand it over
        assert dep["spec"]["strategy"]["type"] == "Recreate"
        assert dep["spec"]["replicas"] == 1
    else:
        strategy = dep["spec"]["strategy"]["rollingUpdate"]
        assert (strategy["maxUnavailable"], strategy["maxSurge"]) == (0, 1)
    (container,) = spec["containers"]
    sc = container["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False and sc["readOnlyRootFilesystem"] is True
    assert sc["capabilities"]["drop"] == ["ALL"]
    assert not container["image"].endswith(":latest") and ":" in container["image"]
    for key in ("requests", "limits"):
        assert {"cpu", "memory"} <= set(container["resources"][key])
    assert container["livenessProbe"]["httpGet"]["path"] == ("/healthz" if is_ui else "/health")
    assert container["readinessProbe"]["httpGet"]["path"] == ("/healthz" if is_ui else "/ready")
    assert any(v["name"] == "tmp" for v in spec["volumes"]), "read-only root needs a /tmp volume"


def test_no_secret_values_and_no_latest_tags_are_committed() -> None:
    for obj in _all_objects():
        assert obj["kind"] != "Secret", (
            "secrets are created by scripts/create_secrets.sh, never in git"
        )
        text = yaml.safe_dump(obj)
        assert ":latest" not in text


def test_datastores_are_pinned_stateful_and_persistent() -> None:
    sets = {o["metadata"]["name"]: o for o in _all_objects() if o["kind"] == "StatefulSet"}
    assert set(sets) == {"postgres", "redis", "redis-tools", "qdrant"}
    for name, s in sets.items():
        image = s["spec"]["template"]["spec"]["containers"][0]["image"]
        assert image.count(":") == 1 and not image.endswith((":latest", ":16", ":7")), name
        assert s["spec"]["volumeClaimTemplates"], f"{name} needs a PVC"


def _policies() -> dict[str, dict[str, Any]]:
    return {o["metadata"]["name"]: o for o in _all_objects() if o["kind"] == "NetworkPolicy"}


def test_every_call_edge_is_allowed_on_both_sides_and_nothing_else() -> None:
    gen = _gen()
    policies = _policies()
    for caller, callees in gen.edges().items():
        egress = policies[f"np-{caller}"]["spec"]["egress"]
        allowed_out = {
            r["podSelector"]["matchLabels"]["app"]
            for rule in egress
            for r in rule.get("to", [])
            if "podSelector" in r
        }
        for callee in callees:
            assert callee in allowed_out, f"{caller} cannot reach {callee}"
            ingress = policies[f"np-{callee}"]["spec"]["ingress"]
            allowed_in = {
                r["podSelector"]["matchLabels"]["app"]
                for rule in ingress
                for r in rule.get("from", [])
                if "podSelector" in r
            }
            assert caller in allowed_in, f"{callee} does not accept {caller}"
        assert allowed_out == set(callees), f"{caller} may reach more than it calls"


def test_only_the_tool_layer_can_reach_the_internet() -> None:
    open_egress = []
    for name, pol in _policies().items():
        for rule in pol["spec"].get("egress", []):
            for dest in rule.get("to", []):
                if "ipBlock" in dest:
                    open_egress.append(name)
    assert open_egress == ["tools-internet-egress"]
    spec = _policies()["tools-internet-egress"]["spec"]
    assert spec["podSelector"]["matchLabels"] == {"app": "tools"}
    cidr = spec["egress"][0]["to"][0]["ipBlock"]
    assert cidr["cidr"] == "0.0.0.0/0" and "10.0.0.0/8" in cidr["except"]
    assert [p["port"] for p in spec["egress"][0]["ports"]] == [443]


def test_the_ingress_controller_reaches_only_the_gateway_and_the_ui() -> None:
    policies = _policies()
    sources = [
        name
        for name, pol in policies.items()
        for rule in pol["spec"].get("ingress", [])
        for src in rule.get("from", [])
        if "namespaceSelector" in src
    ]
    assert sorted(set(sources)) == ["np-gateway", "np-ui"]


def test_the_ui_is_reachable_only_from_the_ingress_controller_and_calls_nothing() -> None:
    spec = _policies()["np-ui"]["spec"]
    assert spec["policyTypes"] == ["Ingress", "Egress"] and spec["egress"] == []
    (rule,) = spec["ingress"]
    assert rule["ports"] == [{"protocol": "TCP", "port": 8080}]
    (src,) = rule["from"]
    assert src["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "ingress-nginx"
    }
    for name, pol in _policies().items():  # nobody else may call the UI (or be told it exists)
        if name != "np-ui":
            assert all(
                p.get("podSelector", {}).get("matchLabels", {}).get("app") != "ui"
                for rule in pol["spec"].get("egress", [])
                for p in rule["to"]
            ), name


def test_the_ui_ingress_serves_root_without_the_api_rewrite() -> None:
    (ing,) = [
        o
        for o in _all_objects()
        if o["kind"] == "Ingress" and o["metadata"]["name"] == "idea-validator-ui"
    ]
    assert "annotations" not in ing["metadata"], "rewrite-target would turn / into a rewritten path"
    (path,) = [p for r in ing["spec"]["rules"] for p in r["http"]["paths"]]
    assert path["path"] == "/" and path["pathType"] == "Prefix"
    assert path["backend"]["service"] == {"name": "ui", "port": {"name": "http"}}


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="needs kubectl (kustomize built in)")
def test_the_dev_overlay_builds() -> None:
    done = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "kubectl",
            "kustomize",
            "--load-restrictor=LoadRestrictionsNone",
            str(ROOT / "k8s" / "overlays" / "dev"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "KUBECONFIG": "/dev/null"},
    )
    assert done.returncode == 0, done.stderr[-400:]
    kinds = {d["kind"] for d in yaml.safe_load_all(done.stdout) if d}
    assert {
        "Namespace",
        "Deployment",
        "StatefulSet",
        "Service",
        "NetworkPolicy",
        "Ingress",
        "Job",
    } <= kinds


def test_the_internet_facing_tool_layer_has_its_own_redis() -> None:
    """tools handles hostile content: a compromise must not reach the verdict cache or run budgets."""
    gen = _gen()
    assert "redis-tools" in gen.edges()["tools"] and "redis" not in gen.edges()["tools"]
    policies = _policies()

    def allowed_callers(name: str) -> set[str]:
        return {
            r["podSelector"]["matchLabels"]["app"]
            for rule in policies[f"np-{name}"]["spec"]["ingress"]
            for r in rule["from"]
        }

    assert allowed_callers("redis-tools") == {"tools"}
    assert "tools" not in allowed_callers("redis")


def test_every_redis_requires_a_password_taken_from_a_secret() -> None:
    sets = [
        o
        for o in _all_objects()
        if o["kind"] == "StatefulSet" and o["metadata"]["name"].startswith("redis")
    ]
    assert len(sets) == 2
    for sts in sets:
        container = sts["spec"]["template"]["spec"]["containers"][0]
        assert "--requirepass" in container["args"]
        env = {e["name"]: e for e in container["env"]}
        for var in ("REDIS_PASSWORD", "REDISCLI_AUTH"):  # the second one authenticates the probes
            assert "secretKeyRef" in env[var]["valueFrom"], var
        assert "value" not in env["REDIS_PASSWORD"]


def test_redis_urls_carry_the_password_so_they_come_from_a_secret_never_a_literal() -> None:
    for dep in _deployments():
        for env in dep["spec"]["template"]["spec"]["containers"][0]["env"]:
            if env["name"] == "REDIS_URL":
                assert "secretKeyRef" in env["valueFrom"], dep["metadata"]["name"]
    tools = next(d for d in _deployments() if d["metadata"]["name"] == "tools")
    env = {e["name"]: e for e in tools["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["REDIS_URL"]["valueFrom"]["secretKeyRef"]["key"] == "TOOLS_REDIS_URL"


def test_the_public_ingress_does_not_expose_ops_endpoints() -> None:
    import re

    (ing,) = [
        o
        for o in _all_objects()
        if o["kind"] == "Ingress" and o["metadata"]["name"] == "idea-validator"
    ]
    paths = [p["path"] for rule in ing["spec"]["rules"] for p in rule["http"]["paths"]]
    assert all(p.startswith("/api") for p in paths)
    pattern = re.compile(paths[0])
    for allowed in ("/api/auth/login", "/api/profile", "/api/health", "/api/validate/x"):
        assert pattern.match(allowed), allowed
    for hidden in ("/api/metrics", "/api/ready", "/api/internal/anything", "/api/", "/api"):
        assert not pattern.match(hidden), hidden


def test_the_profile_image_carries_its_migrations() -> None:
    text = (ROOT / "services" / "profile" / "Dockerfile").read_text()
    assert "alembic.ini" in text and "migrations" in text, "the migrate Job runs from this image"


def test_the_migrate_job_runs_alembic_from_the_profile_image_and_may_only_reach_postgres() -> None:
    (job,) = [
        o for o in _all_objects() if o["kind"] == "Job" and o["metadata"]["name"] == "migrate"
    ]
    spec = job["spec"]["template"]["spec"]
    container = spec["containers"][0]
    assert container["image"].startswith("idea-validator/profile:")
    assert container["command"][:2] == ["alembic", "-c"] and container["command"][-2:] == [
        "upgrade",
        "head",
    ]
    assert spec["restartPolicy"] == "OnFailure" and job["spec"]["ttlSecondsAfterFinished"] > 0
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    names = {e["name"] for e in container["env"]}
    assert "DATABASE_URL" in names and "GROQ_API_KEY" not in names and "INTERNAL_TOKEN" not in names
    policy = _policies()["np-migrate"]["spec"]
    reach = {
        r["podSelector"]["matchLabels"]["app"] for rule in policy["egress"] for r in rule["to"]
    }
    assert reach == {"postgres"}
    pg_in = {
        r["podSelector"]["matchLabels"]["app"]
        for rule in _policies()["np-postgres"]["spec"]["ingress"]
        for r in rule["from"]
    }
    assert "migrate" in pg_in
