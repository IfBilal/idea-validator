#!/usr/bin/env python3
"""Create or update the `app-secrets` Secret in the cluster (P1-T06). Never committed, never printed.

Random values (tokens, passwords) are generated once and then KEPT on re-runs, because regenerating the
Postgres password would lock out an existing database. URLs that embed a password are recomputed from the
current passwords. The provider key comes from the git-ignored `.env`. Values travel through a private
temp file and kubectl's stdin, never through a command line (visible in `ps`) or the terminal.
Run through scripts/create_secrets.sh, which pins the kubeconfig to the project's kind cluster first.
"""

import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "idea-validator"
SECRET = "app-secrets"  # noqa: S105 (the name of the Kubernetes Secret object, not a value)


def parse_dotenv(text: str) -> dict[str, str]:
    """KEY=VALUE lines; comments, blanks, lines without '=' ignored; one layer of quotes removed."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def build(
    existing: dict[str, str],
    dotenv: dict[str, str],
    rand: Callable[[int], str] = secrets.token_hex,
) -> dict[str, str]:
    v = dict(existing)

    def keep(key: str, make: Callable[[], str]) -> None:
        if not v.get(key):
            v[key] = make()

    keep("INTERNAL_TOKEN", lambda: rand(24))
    keep("JWT_SECRET", lambda: rand(32))
    keep("POSTGRES_USER", lambda: "idea")
    keep("POSTGRES_PASSWORD", lambda: rand(16))
    keep("POSTGRES_DB", lambda: "idea_validator")
    keep("REDIS_PASSWORD", lambda: rand(16))
    keep("TOOLS_REDIS_PASSWORD", lambda: rand(16))
    user, password, db = v["POSTGRES_USER"], v["POSTGRES_PASSWORD"], v["POSTGRES_DB"]
    v["DATABASE_URL"] = f"postgresql+asyncpg://{user}:{password}@postgres:5432/{db}"
    v["REDIS_URL"] = f"redis://:{v['REDIS_PASSWORD']}@redis:6379/0"
    v["TOOLS_REDIS_URL"] = f"redis://:{v['TOOLS_REDIS_PASSWORD']}@redis-tools:6379/0"
    # an empty key keeps the pod startable; /llm then answers 502 "not configured" instead of a crash loop
    v["GROQ_API_KEY"] = dotenv.get("GROQ_API_KEY") or v.get("GROQ_API_KEY", "")
    return v


def _existing() -> dict[str, str]:
    done = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "get", "secret", SECRET, "-o", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        return {}
    data = json.loads(done.stdout).get("data") or {}
    return {k: base64.b64decode(val).decode() for k, val in data.items()}


def main() -> int:
    subprocess.run(
        ["kubectl", "apply", "-f", str(ROOT / "k8s/base/namespace.yaml")],
        check=True,
        capture_output=True,
    )
    existing = _existing()
    env_file = ROOT / ".env"
    dotenv = parse_dotenv(env_file.read_text()) if env_file.exists() else {}
    values = build(existing, dotenv)
    if not values["GROQ_API_KEY"]:
        print(
            "warning: GROQ_API_KEY is empty (add it to .env): /llm will answer 502 until it is set"
        )
    fd, path = tempfile.mkstemp(prefix="app-secrets-")
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.writelines(f"{k}={v}\n" for k, v in values.items())
        manifest = subprocess.run(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "create",
                "secret",
                "generic",
                SECRET,
                f"--from-env-file={path}",
                "--dry-run=client",
                "-o",
                "yaml",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=manifest,
            text=True,
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(path)
    created = sorted(set(values) - set(existing))
    kept = sorted(set(values) & set(existing))
    print(
        f"{SECRET}: {len(created)} key(s) created, {len(kept)} kept (names only: {', '.join(sorted(values))})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
