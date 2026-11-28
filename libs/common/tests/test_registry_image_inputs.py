"""What the agentops image copies for GET /registry holds only versioned files, no local secrets."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_dockerignore_excludes_local_env_and_secret_config() -> None:
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    for pattern in ("**/.env", "**/.env.*", "config/*.local.*", "config/secrets*"):
        assert pattern in ignore, pattern


def test_the_copied_directories_hold_only_yaml_and_markdown() -> None:
    for name in ("config", "prompts", "rubrics"):
        for path in (ROOT / name).rglob("*"):
            if path.is_file():
                assert path.suffix in {".yaml", ".md"}, path
                assert ".local." not in path.name and not path.name.startswith("secrets"), path
