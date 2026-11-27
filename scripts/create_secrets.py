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


OPTIONAL_KEYS = ("GITHUB_TOKEN", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "PRODUCTHUNT_TOKEN")


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
    # optional scout credentials: empty means "source off" (docs/data-sources.md); never generated
    for key in OPTIONAL_KEYS:
        v[key] = dotenv.get(key) or v.get(key, "")
    return v


def ensure_dev_env(path: Path, rand: Callable[[int], str] = secrets.token_hex) -> list[str]:
    """Add the keys docker compose needs to the git-ignored ``.env`` if they are missing.

    Only missing keys are written (an existing password is never replaced: the Postgres volume holds
    it). URLs point at ``localhost`` because they are for tools run on the host (alembic, pytest);
    inside the cluster the URLs come from ``app-secrets``. Returns the key names that were added.
    """
    current = parse_dotenv(path.read_text()) if path.exists() else {}
    wanted = dict(current)
    wanted.setdefault("POSTGRES_USER", "idea")
    wanted.setdefault("POSTGRES_PASSWORD", rand(16))
    wanted.setdefault("POSTGRES_DB", "idea_validator")
    user, password, db = wanted["POSTGRES_USER"], wanted["POSTGRES_PASSWORD"], wanted["POSTGRES_DB"]
    wanted.setdefault("DATABASE_URL", f"postgresql+asyncpg://{user}:{password}@localhost:5432/{db}")
    wanted.setdefault("REDIS_URL", "redis://localhost:6379/0")
    added = [k for k in wanted if k not in current]
    if added:
        needs_newline = (
            path.exists() and path.stat().st_size > 0 and not path.read_text().endswith("\n")
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as handle:
            if needs_newline:
                handle.write("\n")
            handle.writelines(f"{k}={wanted[k]}\n" for k in added)
        os.chmod(path, 0o600)
    return added


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
    if "--dev-env" in sys.argv:  # no cluster involved: prepare .env for `make up`
        added = ensure_dev_env(ROOT / ".env")
        print(f".env: {len(added)} key(s) added" + (f" ({', '.join(added)})" if added else ""))
        return 0
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
