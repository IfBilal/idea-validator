"""The seed Job is generated (scripts/gen_k8s.py) and must stay hardened and off the CI base."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_the_seed_job_is_hardened_calls_only_m6_and_is_not_in_the_base_kustomization() -> None:
    job = yaml.safe_load((ROOT / "k8s" / "jobs" / "seed.yaml").read_text().split("\n", 1)[1])
    pod = job["spec"]["template"]["spec"]
    assert (
        pod["securityContext"]["runAsUser"] == 10001
        and pod["automountServiceAccountToken"] is False
    )
    (container,) = pod["containers"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["image"].endswith(":unset") and "latest" not in container["image"]
    assert container["command"][:3] == ["python", "-m", "ingestion"]
    env = {e["name"]: e for e in container["env"]}
    assert env["KB_URL"]["value"] == "http://kb:8000"
    for name in ("INTERNAL_TOKEN", "INGEST_TOKEN"):
        assert "secretKeyRef" in env[name]["valueFrom"]  # tokens are never literals
    base = (ROOT / "k8s" / "base" / "kustomization.yaml").read_text()
    assert "jobs/seed.yaml" not in base


def test_the_dockerfile_follows_the_conventions() -> None:
    text = (ROOT / "jobs" / "ingestion" / "Dockerfile").read_text()
    assert "USER 10001" in text and text.count("FROM ") == 2
    assert ":latest" not in text and "python:3.12.16-slim-bookworm" in text
    assert "--frozen" in text
