import httpx

from svc_kb.main import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_identifies_the_service() -> None:
    async with _client() as client:
        body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["service"] == "kb"
    assert body["module"] == "M6"


async def test_ready_is_503_until_storage_is_configured() -> None:
    async with _client() as client:
        assert (await client.get("/ready")).status_code == 503


async def test_metrics_are_exposed() -> None:
    async with _client() as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_the_models_are_copied_from_the_read_only_seed_into_the_writable_cache(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from svc_kb.services.models import FastEmbedModels

    seed, cache = tmp_path / "seed", tmp_path / "cache"
    (seed / "models--x").mkdir(parents=True)
    (seed / "models--x" / "weights.onnx").write_text("w")
    models = FastEmbedModels(str(cache), str(seed))
    models._prepare_cache()
    assert (cache / "models--x" / "weights.onnx").read_text() == "w"
    (cache / "models--x" / "weights.onnx").write_text("changed")
    models._prepare_cache()
    assert (cache / "models--x" / "weights.onnx").read_text() == "changed"
    FastEmbedModels(None, str(seed))._prepare_cache()  # no cache dir: nothing to do, no error
