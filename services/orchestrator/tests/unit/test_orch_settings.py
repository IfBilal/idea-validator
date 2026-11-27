from pydantic import SecretStr

from svc_orchestrator.config import Settings
from svc_orchestrator.main import build_app, service_urls


def test_service_urls_come_from_settings_not_code() -> None:
    s = Settings(kb_url="http://kb:8000", profile_url="http://m2:8000")
    urls = service_urls(s)
    assert urls["kb"] == "http://kb:8000"
    assert urls["profile"] == "http://m2:8000"
    assert set(urls) == {"profile", "kb", "analyst", "scouts", "validators", "verdict", "critic"}


def test_no_defaults_point_at_localhost() -> None:
    for value in service_urls(Settings()).values():
        assert "localhost" not in value
        assert "127.0.0.1" not in value


def test_redis_url_is_optional_and_secret() -> None:
    assert Settings().redis_url is None
    assert Settings(redis_url=SecretStr("redis://r:6379/0")).redis_url is not None


def test_app_builds_without_any_backing_service() -> None:
    assert build_app(Settings()).title.startswith("M3")
