"""scripts/create_secrets.py builds the app-secrets values: idempotent, consistent, never on a command line."""

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _mod() -> Any:
    spec = importlib.util.spec_from_file_location(
        "create_secrets", ROOT / "scripts" / "create_secrets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _counter() -> Any:
    state = {"n": 0}

    def rand(nbytes: int) -> str:
        state["n"] += 1
        return f"{state['n']:0{nbytes * 2}x}"

    return rand


def test_every_key_the_manifests_reference_is_produced() -> None:
    needed = set()
    for path in (ROOT / "k8s" / "base").rglob("*.yaml"):
        needed |= set(re.findall(r"key: ([A-Z_]+)", path.read_text()))
    values = _mod().build(existing={}, dotenv={"GROQ_API_KEY": "gsk_x"}, rand=_counter())
    assert needed <= set(values), f"missing: {sorted(needed - set(values))}"


def test_urls_are_consistent_with_the_passwords_they_embed() -> None:
    v = _mod().build(existing={}, dotenv={}, rand=_counter())
    assert v["DATABASE_URL"] == (
        f"postgresql+asyncpg://{v['POSTGRES_USER']}:{v['POSTGRES_PASSWORD']}@postgres:5432/{v['POSTGRES_DB']}"
    )
    assert v["REDIS_URL"] == f"redis://:{v['REDIS_PASSWORD']}@redis:6379/0"
    assert v["TOOLS_REDIS_URL"] == f"redis://:{v['TOOLS_REDIS_PASSWORD']}@redis-tools:6379/0"
    assert v["REDIS_PASSWORD"] != v["TOOLS_REDIS_PASSWORD"], (
        "the two redis instances must not share a password"
    )


def test_secrets_are_long_random_and_distinct() -> None:
    v = _mod().build(existing={}, dotenv={}, rand=_counter())
    for key in (
        "INTERNAL_TOKEN",
        "JWT_SECRET",
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "TOOLS_REDIS_PASSWORD",
    ):
        assert len(v[key]) >= 32, key
    assert len({v[k] for k in ("INTERNAL_TOKEN", "JWT_SECRET", "POSTGRES_PASSWORD")}) == 3


def test_a_rerun_keeps_what_exists_so_running_databases_keep_working() -> None:
    first = _mod().build(existing={}, dotenv={}, rand=_counter())
    second = _mod().build(existing=first, dotenv={}, rand=_counter())
    assert second == first, (
        "regenerating the postgres password would lock the existing database out"
    )


def test_the_provider_key_comes_from_dotenv_and_updates_on_rerun() -> None:
    first = _mod().build(existing={}, dotenv={"GROQ_API_KEY": "gsk_old"}, rand=_counter())
    second = _mod().build(existing=first, dotenv={"GROQ_API_KEY": "gsk_new"}, rand=_counter())
    assert first["GROQ_API_KEY"] == "gsk_old" and second["GROQ_API_KEY"] == "gsk_new"
    assert second["POSTGRES_PASSWORD"] == first["POSTGRES_PASSWORD"]


def test_a_missing_provider_key_becomes_an_empty_value_not_a_crash_loop() -> None:
    v = _mod().build(existing={}, dotenv={}, rand=_counter())
    assert v["GROQ_API_KEY"] == ""  # tools starts, /llm answers 502 "not configured"


def test_dotenv_parsing_ignores_comments_and_quotes_and_never_needs_the_shell() -> None:
    text = "# c\nGROQ_API_KEY='gsk_q'\nEMPTY=\nOTHER=\"a b\"\n\nNOEQUALS\n"
    assert _mod().parse_dotenv(text) == {"GROQ_API_KEY": "gsk_q", "EMPTY": "", "OTHER": "a b"}


def test_the_evaluation_account_gets_a_random_password_and_keeps_it_on_rerun() -> None:
    first = _mod().build(existing={}, dotenv={}, rand=_counter())
    assert "@" in first["EVAL_USER"] and len(first["EVAL_PASSWORD"]) >= 32
    assert first["EVAL_PASSWORD"] != first["JWT_SECRET"]
    second = _mod().build(existing=first, dotenv={}, rand=_counter())
    assert second["EVAL_PASSWORD"] == first["EVAL_PASSWORD"]
    assert first["EVAL_USER"].split("@")[0] not in first["EVAL_PASSWORD"]  # gateway password rule


@pytest.mark.parametrize("flag", ["--from-literal", "echo $", "set -x"])
def test_the_wrapper_never_puts_secrets_on_a_command_line_or_in_a_trace(flag: str) -> None:
    for name in ("create_secrets.sh", "create_secrets.py"):
        assert flag not in (ROOT / "scripts" / name).read_text(), f"{name} contains {flag!r}"


# ---- dev stack (docker compose): scripts/create_secrets.py --dev-env ------------------------------
def test_dev_env_creates_only_the_missing_keys_and_keeps_what_exists(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("GROQ_API_KEY=gsk_keep\nPOSTGRES_USER=custom\n")
    mod = _mod()
    added = mod.ensure_dev_env(env, rand=_counter())
    text = env.read_text()
    assert "GROQ_API_KEY=gsk_keep" in text and "POSTGRES_USER=custom" in text
    assert "POSTGRES_USER" not in added and {"POSTGRES_PASSWORD", "POSTGRES_DB"} <= set(added)
    again = mod.ensure_dev_env(env, rand=_counter())
    assert again == [], "a second run must not change anything"
    assert env.read_text() == text


def test_dev_env_points_the_urls_at_the_published_compose_ports(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _mod().ensure_dev_env(env, rand=_counter())
    values = _mod().parse_dotenv(env.read_text())
    assert values["DATABASE_URL"] == (
        f"postgresql+asyncpg://{values['POSTGRES_USER']}:{values['POSTGRES_PASSWORD']}"
        f"@localhost:5432/{values['POSTGRES_DB']}"
    )
    assert values["REDIS_URL"] == "redis://localhost:6379/0"
    assert len(values["POSTGRES_PASSWORD"]) >= 32


def test_dev_env_file_is_private_and_is_created_when_missing(tmp_path: Path) -> None:
    env = tmp_path / "sub" / ".env"
    env.parent.mkdir()
    _mod().ensure_dev_env(env, rand=_counter())
    assert env.stat().st_mode & 0o077 == 0


def test_the_compose_file_is_pinned_healthy_and_holds_no_secret() -> None:
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert set(compose["services"]) == {"postgres", "redis", "qdrant"}
    for name, svc in compose["services"].items():
        image = svc["image"]
        assert ":" in image and not image.endswith(":latest") and "PIN" not in image, name
        assert "healthcheck" in svc, f"{name} needs a healthcheck so `make up` can wait for it"
        for port in svc["ports"]:
            assert str(port).startswith("${"), f"{name} host port must be overridable: {port}"
    code = "\n".join(
        ln
        for ln in (ROOT / "docker-compose.yml").read_text().splitlines()
        if not ln.strip().startswith("#")
    )
    assert "${POSTGRES_PASSWORD" in code, "the password must come from the environment"
    assert "password" not in code.replace("POSTGRES_PASSWORD", "").lower(), (
        "no password value in the file"
    )
