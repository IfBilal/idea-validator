"""Alpha resolution (env > config/verdict.yaml > 0.6) and the scorer client."""

from pathlib import Path

import httpx
import pytest

from common.config import ConfigError
from common.errors import UpstreamError, UpstreamTimeout
from common.schemas import ScoreEvidence, ScoreRequest
from svc_validators.clients.scorer import ScorerClient
from svc_validators.config import DEFAULT_ALPHA, Settings, resolve_alpha

ROOT = Path(__file__).resolve().parents[4]


def _settings(tmp_path: Path, yaml_text: str | None, **over: object) -> Settings:
    if yaml_text is not None:
        (tmp_path / "verdict.yaml").write_text(yaml_text)
    return Settings(config_dir=tmp_path, **over)  # type: ignore[arg-type]


def test_alpha_comes_from_the_repo_verdict_config() -> None:
    assert resolve_alpha(Settings(config_dir=ROOT / "config")) == 0.6


def test_alpha_defaults_when_config_file_is_absent(tmp_path: Path) -> None:
    assert resolve_alpha(_settings(tmp_path, None)) == DEFAULT_ALPHA == 0.6


def test_alpha_defaults_when_the_key_is_absent(tmp_path: Path) -> None:
    assert resolve_alpha(_settings(tmp_path, "t_go: 70\n")) == 0.6


def test_alpha_read_from_yaml(tmp_path: Path) -> None:
    assert resolve_alpha(_settings(tmp_path, "scoring_blend:\n  alpha_model: 0.35\n")) == 0.35


def test_env_override_beats_yaml(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "scoring_blend:\n  alpha_model: 0.35\n", alpha_model=0.9)
    assert resolve_alpha(settings) == 0.9


@pytest.mark.parametrize("value", ["0", "1", "0.0", "1.0"])
def test_alpha_boundaries_are_valid(tmp_path: Path, value: str) -> None:
    got = resolve_alpha(_settings(tmp_path, f"scoring_blend:\n  alpha_model: {value}\n"))
    assert got == float(value)


@pytest.mark.parametrize("value", ["-0.1", "1.1", "'high'", "true", "null", "[0.5]"])
def test_invalid_alpha_in_yaml_is_a_config_error(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigError):
        resolve_alpha(_settings(tmp_path, f"scoring_blend:\n  alpha_model: {value}\n"))


def test_scoring_blend_must_be_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        resolve_alpha(_settings(tmp_path, "scoring_blend: 0.5\n"))


def test_invalid_env_alpha_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        resolve_alpha(_settings(tmp_path, None, alpha_model=1.5))


def test_default_pins_are_all_v1() -> None:
    assert set(Settings().rubric_versions.values()) == {1}
    assert len(Settings().rubric_versions) == 6


# ---- scorer client ---------------------------------------------------------------------


def _request() -> ScoreRequest:
    return ScoreRequest(
        rubric_id="risk@v1",
        idea="x",
        evidence=[ScoreEvidence(evidence_id="card_8f21a3b4#c2", quote="q")],
        features={"risk_flags": []},
        run_id="run_1",
    )


def _client(handler: object) -> ScorerClient:
    return ScorerClient(
        "http://scorer.test",
        timeout_s=1,
        max_retries=0,
        internal_token="tok",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


_GOOD = {
    "dimension": "risk",
    "score": 3,
    "confidence": 0.5,
    "evidence_ids": ["card_8f21a3b4#c2"],
    "reason": "r",
    "rubric_version": "risk@v1",
    "model": "m",
}


async def test_client_posts_the_request_and_parses_the_score() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_GOOD)

    score = await _client(handler).score(_request())
    assert score.score == 3
    assert seen[0].url.path == "/score"
    assert seen[0].headers["x-internal-token"] == "tok"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={**_GOOD, "score": 9}),
        httpx.Response(200, json={**_GOOD, "verdict": "GO"}),
        httpx.Response(200, json={"score": 1}),
        httpx.Response(200, json=[1]),
        httpx.Response(200, text="not json"),
    ],
)
async def test_client_rejects_invalid_scores(response: httpx.Response) -> None:
    with pytest.raises(UpstreamError):
        await _client(lambda _: response).score(_request())


async def test_client_maps_timeouts() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(UpstreamTimeout):
        await _client(handler).score(_request())


async def test_ping_uses_the_ready_endpoint() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={})

    assert await _client(handler).ping() is True
    assert paths == ["/ready"]
    assert await _client(lambda _: httpx.Response(503)).ping() is False
