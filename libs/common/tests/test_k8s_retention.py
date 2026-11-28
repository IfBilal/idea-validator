"""The retention CronJob gets only what it needs: Postgres, the trace volume, no shared token."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]


def load() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(
        (ROOT / "k8s/base/cronjobs/agentops-retention.yaml").read_text()
    )
    return doc


def test_retention_has_no_internal_token_and_no_other_service_urls() -> None:
    pod = load()["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    names = {e["name"] for e in container["env"]}
    assert "INTERNAL_TOKEN" not in names
    assert "DATABASE_URL" in names
    assert "TRACE_DIR" in names
    assert not {n for n in names if n.endswith("_URL")} - {"DATABASE_URL"}


def test_retention_mounts_the_trace_volume_beside_the_agentops_pod() -> None:
    pod = load()["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    claim = {"name": "data", "persistentVolumeClaim": {"claimName": "agentops-traces"}}
    assert claim in pod["volumes"]
    terms = pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
    assert terms[0]["labelSelector"]["matchLabels"] == {"app": "agentops"}
    assert pod["containers"][0]["command"][:3] == ["python", "-m", "agentops.retention"]
