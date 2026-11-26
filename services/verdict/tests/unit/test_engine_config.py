from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import CONFIG_FILE
from pydantic import ValidationError

from svc_verdict.services.engine_config import (
    EngineConfig,
    load_engine_config,
    parse_engine_config,
)


def raw() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CONFIG_FILE.read_text())
    return data


def test_committed_config_is_valid(cfg: EngineConfig) -> None:
    assert cfg.thresholds.T_go == 70
    assert cfg.versions == {"engine": "m11@v1", "rubrics": "r1", "profile_weights": "w1"}


def test_weights_must_add_to_100() -> None:
    data = raw()
    data["weights"]["startup"]["demand_pain"] = 30
    with pytest.raises(ValidationError, match="must add to 100"):
        parse_engine_config(data)


def test_unknown_goal_is_rejected() -> None:
    data = raw()
    data["weights"]["hobby"] = data["weights"]["startup"]
    with pytest.raises(ValidationError, match="Input should be"):
        parse_engine_config(data)


def test_missing_dimension_is_rejected() -> None:
    data = raw()
    del data["weights"]["portfolio"]["learning"]
    with pytest.raises(ValidationError):
        parse_engine_config(data)


def test_thresholds_must_be_ordered() -> None:
    data = raw()
    data["thresholds"]["T_no"] = 80
    with pytest.raises(ValidationError, match="T_no <= T_go"):
        parse_engine_config(data)


def test_unknown_keys_are_rejected_so_typos_do_not_pass_silently() -> None:
    data = raw()
    data["thresholds"]["t_go"] = 60
    with pytest.raises(ValidationError):
        parse_engine_config(data)


def test_config_is_read_from_the_mounted_directory(tmp_path: Path) -> None:
    data = raw()
    data["thresholds"]["T_go"] = 60
    (tmp_path / "verdict.yaml").write_text(yaml.safe_dump(data))
    assert load_engine_config(tmp_path).thresholds.T_go == 60
