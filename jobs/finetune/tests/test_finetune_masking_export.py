import json
from pathlib import Path

import pytest

from finetune.checksum import sha256_file, write_checksum
from finetune.masking import IGNORE, build_labels
from finetune.runs import RunRecord, write_run


def test_loss_only_on_assistant_tokens() -> None:
    ids, labels, mask = build_labels([1, 2, 3, 4], [1, 2, 3, 4, 9, 8, 7])
    assert ids == [1, 2, 3, 4, 9, 8, 7]
    assert labels[:4] == [IGNORE] * 4
    assert labels[4:] == [9, 8, 7]
    assert mask == [0, 0, 0, 0, 1, 1, 1]


def test_prompt_must_be_prefix() -> None:
    with pytest.raises(ValueError, match="prefix"):
        build_labels([1, 2, 3], [1, 9, 3, 4])


def test_empty_target_rejected() -> None:
    with pytest.raises(ValueError, match="target"):
        build_labels([1, 2], [1, 2])


def test_truncation_never_yields_all_masked() -> None:
    with pytest.raises(ValueError, match="truncat"):
        build_labels([1] * 10, [1] * 10 + [5, 6], max_len=10)


def test_sha256_and_sidecar(tmp_path: Path) -> None:
    f = tmp_path / "m.gguf"
    f.write_bytes(b"abc")
    assert sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    side = write_checksum(f)
    assert side.read_text().split()[0] == sha256_file(f)
    assert side.name == "m.gguf.sha256"


def test_run_record_roundtrip(tmp_path: Path) -> None:
    rec = RunRecord(
        run_id="r1",
        base_model="Qwen/Qwen2.5-1.5B-Instruct",
        settings={"r": 16},
        hardware={"gpu": "x"},
        versions={"torch": "2"},
        metrics={"final_val_loss": 0.1},
        wall_time_s=12.5,
        synthetic=True,
        loss_curve=[{"step": 1, "train": 1.0}],
    )
    p = write_run(rec, tmp_path)
    assert p.name == "r1.json"
    assert json.loads(p.read_text())["synthetic"] is True
    with pytest.raises(FileExistsError):
        write_run(rec, tmp_path)
