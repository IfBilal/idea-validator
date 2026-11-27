import json
from pathlib import Path

import jsonschema
import pytest

from finetune.bench import server_command, summarise
from finetune.schema import SCORE_JSON_SCHEMA


@pytest.mark.parametrize(
    "doc",
    [
        {"score": 3.5, "confidence": 0.7, "evidence_ids": ["card_8f21aa01#c2"], "reason": "ok"},
        {"score": None, "confidence": 0, "evidence_ids": [], "reason": "no evidence"},
    ],
)
def test_constrained_schema_accepts_valid_replies(doc: dict[str, object]) -> None:
    jsonschema.validate(doc, SCORE_JSON_SCHEMA)


@pytest.mark.parametrize(
    "doc",
    [
        {"score": 6, "confidence": 0.7, "evidence_ids": [], "reason": "x"},
        {"score": 3, "confidence": 2, "evidence_ids": [], "reason": "x"},
        {"score": 3, "confidence": 0.5, "evidence_ids": [], "reason": "x", "verdict": "GO"},
        {"score": 3, "confidence": 0.5, "evidence_ids": [1], "reason": "x"},
        {"confidence": 0.5, "evidence_ids": [], "reason": "x"},
    ],
)
def test_constrained_schema_rejects_bad_replies(doc: dict[str, object]) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCORE_JSON_SCHEMA)


def test_schema_has_no_verdict_property_and_is_json() -> None:
    assert "verdict" not in SCORE_JSON_SCHEMA["properties"]
    json.dumps(SCORE_JSON_SCHEMA)


def test_server_command_pins_threads_ctx_and_single_slot() -> None:
    cmd = server_command(Path("llama-server.exe"), Path("m.gguf"), port=8089, threads=4, ctx=4096)
    joined = " ".join(cmd)
    assert "-m m.gguf" in joined
    assert "--port 8089" in joined and "-t 4" in joined and "-c 4096" in joined
    assert "-np 1" in joined
    assert "--host 127.0.0.1" in joined


def test_summarise_reports_percentiles_and_rss() -> None:
    s = summarise(
        [1.0, 2.0, 3.0, 4.0], rss_mb=[900.0, 1100.0], prompt_tokens=[1000, 1200], threads=4
    )
    assert s["latency_s"]["p50"] == pytest.approx(2.5)
    assert s["rss_mb_max"] == 1100.0
    assert s["prompt_tokens_mean"] == 1100
    assert s["threads"] == 4
