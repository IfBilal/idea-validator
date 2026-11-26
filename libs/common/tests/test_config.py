from pathlib import Path

import pytest
from common.config import ConfigError, ServiceSettings, load_yaml_config


def test_loads_yaml_from_config_dir(tmp_path: Path) -> None:
    (tmp_path / "verdict.yaml").write_text("T_go: 70\nfloor: 1.5\n")
    assert load_yaml_config("verdict", config_dir=tmp_path) == {"T_go": 70, "floor": 1.5}


def test_env_overrides_top_level_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "verdict.yaml").write_text("T_go: 70\n")
    monkeypatch.setenv("VERDICT_T_GO", "72")
    cfg = load_yaml_config("verdict", config_dir=tmp_path, env_prefix="VERDICT_")
    assert cfg["T_go"] == 72


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_yaml_config("nope", config_dir=tmp_path)


def test_non_mapping_is_a_config_error(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("- a\n- b\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_yaml_config("bad", config_dir=tmp_path)


def test_settings_read_env_and_hide_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICE_NAME", "kb")
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    settings = ServiceSettings()
    assert settings.service_name == "kb"
    assert "s3cret" not in repr(settings)
    assert settings.internal_token is not None
    assert settings.internal_token.get_secret_value() == "s3cret"
