"""Token bucket, cache, pinned fetcher and robots rules."""

import httpx
import pytest

from common.errors import RateLimited
from svc_tools.services.cache import MemoryCache
from svc_tools.services.fetcher import PinnedHttpxFetcher
from svc_tools.services.ratelimit import MemoryTokenBucket
from svc_tools.services.robots import RobotsChecker
from svc_tools.services.url_policy import CheckedUrl

UA = "IdeaValidatorBot/1.0 (+https://example.test; student project)"
CHECKED = CheckedUrl("api.github.com", "/search?q=x", ("140.82.112.5", "140.82.112.6"))


# ---- token bucket (boundary values)
async def test_a_full_bucket_allows_a_burst_equal_to_the_rate_then_blocks() -> None:
    bucket = MemoryTokenBucket(lambda: 1000.0)
    for _ in range(25):
        await bucket.acquire("api.github.com", 25)
    with pytest.raises(RateLimited) as blocked:
        await bucket.acquire("api.github.com", 25)
    assert blocked.value.details["retry_after"] >= 1


async def test_tokens_refill_continuously_at_the_configured_rate() -> None:
    clock = {"now": 0.0}
    bucket = MemoryTokenBucket(lambda: clock["now"])
    for _ in range(60):
        await bucket.acquire("h", 60)  # 60/min = 1 token per second
    with pytest.raises(RateLimited):
        await bucket.acquire("h", 60)
    clock["now"] = 0.99
    with pytest.raises(RateLimited):  # just under one token
        await bucket.acquire("h", 60)
    clock["now"] = 1.0
    await bucket.acquire("h", 60)
    with pytest.raises(RateLimited):
        await bucket.acquire("h", 60)


async def test_the_bucket_never_holds_more_than_its_capacity() -> None:
    clock = {"now": 0.0}
    bucket = MemoryTokenBucket(lambda: clock["now"])
    await bucket.acquire("h", 5)
    clock["now"] = 100_000.0  # a long idle time does not allow a bigger burst
    for _ in range(5):
        await bucket.acquire("h", 5)
    with pytest.raises(RateLimited):
        await bucket.acquire("h", 5)


async def test_domains_have_separate_buckets_and_a_zero_rate_blocks() -> None:
    bucket = MemoryTokenBucket(lambda: 1.0)
    await bucket.acquire("a", 1)
    await bucket.acquire("b", 1)
    with pytest.raises(RateLimited):
        await bucket.acquire("a", 1)
    with pytest.raises(RateLimited):
        await bucket.acquire("c", 0)


async def test_retry_after_matches_the_time_to_the_next_token() -> None:
    bucket = MemoryTokenBucket(lambda: 0.0)
    for _ in range(6):
        await bucket.acquire("h", 6)  # 6/min = one token per 10 s
    with pytest.raises(RateLimited) as blocked:
        await bucket.acquire("h", 6)
    assert blocked.value.details["retry_after"] == 10


# ---- cache
async def test_cache_hit_miss_and_ttl() -> None:
    clock = {"now": 0.0}
    cache = MemoryCache(lambda: clock["now"])
    assert await cache.get("k") is None
    await cache.set("k", {"a": 1}, ttl_s=10)
    assert await cache.get("k") == {"a": 1}
    clock["now"] = 9.99
    assert await cache.get("k") == {"a": 1}
    clock["now"] = 10.0
    assert await cache.get("k") is None


async def test_a_zero_or_negative_ttl_is_never_cached() -> None:
    cache = MemoryCache(lambda: 0.0)
    await cache.set("k", {"a": 1}, ttl_s=0)
    assert await cache.get("k") is None


# ---- pinned fetcher
def fetcher_with(handler) -> PinnedHttpxFetcher:  # type: ignore[no-untyped-def]
    return PinnedHttpxFetcher(UA, transport=httpx.MockTransport(handler))


async def test_the_request_goes_to_the_checked_ip_with_the_host_header_and_sni() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok", headers={"content-type": "text/plain"})

    raw = await fetcher_with(handler).get(CHECKED, timeout_s=5, max_bytes=100)
    request = seen[0]
    assert request.url.host == "140.82.112.5"
    assert request.headers["host"] == "api.github.com"
    assert request.extensions["sni_hostname"] == "api.github.com"
    assert request.headers["user-agent"] == UA
    assert (raw.status, raw.body, raw.truncated) == (200, b"ok", False)
    assert raw.headers["content-type"] == "text/plain"


async def test_no_cookies_or_credentials_are_ever_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"", headers={"set-cookie": "a=b"})

    fetcher = fetcher_with(handler)
    await fetcher.get(CHECKED, timeout_s=5, max_bytes=10)
    await fetcher.get(CHECKED, timeout_s=5, max_bytes=10)
    assert all("cookie" not in r.headers and "authorization" not in r.headers for r in seen)


@pytest.mark.parametrize(
    ("size", "truncated", "length"), [(99, False, 99), (100, False, 100), (101, True, 100)]
)
async def test_response_size_cap_boundary(size: int, truncated: bool, length: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * size)

    raw = await fetcher_with(handler).get(CHECKED, timeout_s=5, max_bytes=100)
    assert (raw.truncated, len(raw.body)) == (truncated, length)


async def test_the_next_address_is_tried_when_the_first_fails() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "140.82.112.5":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, content=b"ok")

    raw = await fetcher_with(handler).get(CHECKED, timeout_s=5, max_bytes=10)
    assert raw.status == 200
    assert hosts == ["140.82.112.5", "140.82.112.6"]


async def test_timeouts_and_total_connection_failure_raise_upstream_errors() -> None:
    from common.errors import UpstreamError, UpstreamTimeout

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(UpstreamTimeout):
        await fetcher_with(slow).get(CHECKED, timeout_s=1, max_bytes=10)
    with pytest.raises(UpstreamError):
        await fetcher_with(dead).get(CHECKED, timeout_s=1, max_bytes=10)


async def test_redirects_are_returned_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/"})

    raw = await fetcher_with(handler).get(CHECKED, timeout_s=5, max_bytes=10)
    assert raw.status == 302
    assert raw.headers["location"] == "https://evil.example/"


# ---- robots
class FakeRobotsSource:
    def __init__(self, status: int, body: str = "", error: Exception | None = None) -> None:
        self.status, self.body, self.error, self.calls = status, body, error, 0

    async def fetch_robots(self, host: str) -> tuple[int, str]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.status, self.body


def checker(source: FakeRobotsSource, exempt: set[str] | None = None) -> RobotsChecker:
    return RobotsChecker(source, MemoryCache(lambda: 0.0), UA, exempt or set(), ttl_s=86400)


async def test_a_disallowed_path_is_refused_and_an_allowed_one_is_not() -> None:
    robots = "User-agent: *\nDisallow: /private\n"
    c = checker(FakeRobotsSource(200, robots))
    assert await c.allowed("www.example.test", "/public/page") is True
    assert await c.allowed("www.example.test", "/private/data") is False


async def test_a_rule_for_our_bot_beats_the_wildcard() -> None:
    robots = "User-agent: *\nDisallow: /\n\nUser-agent: IdeaValidatorBot\nAllow: /\n"
    assert await checker(FakeRobotsSource(200, robots)).allowed("h", "/x") is True
    robots = "User-agent: IdeaValidatorBot\nDisallow: /\n"
    assert await checker(FakeRobotsSource(200, robots)).allowed("h", "/x") is False


@pytest.mark.parametrize("status", [404, 410, 401, 403])
async def test_a_missing_or_client_error_robots_file_means_allowed(status: int) -> None:
    assert await checker(FakeRobotsSource(status)).allowed("h", "/x") is True


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_an_unreachable_robots_file_means_disallowed(status: int) -> None:
    assert await checker(FakeRobotsSource(status)).allowed("h", "/x") is False


async def test_a_network_error_while_reading_robots_means_disallowed() -> None:
    c = checker(FakeRobotsSource(0, error=httpx.ConnectError("down")))
    assert await c.allowed("h", "/x") is False


async def test_robots_are_cached_per_host() -> None:
    source = FakeRobotsSource(200, "User-agent: *\nDisallow:\n")
    c = checker(source)
    for _ in range(5):
        await c.allowed("h", "/x")
    assert source.calls == 1
    await c.allowed("other", "/x")
    assert source.calls == 2


async def test_api_hosts_exempt_from_robots_are_never_fetched() -> None:
    source = FakeRobotsSource(200, "User-agent: *\nDisallow: /\n")
    c = checker(source, exempt={"api.github.com"})
    assert await c.allowed("api.github.com", "/anything") is True
    assert source.calls == 0


async def test_an_empty_robots_file_allows_everything() -> None:
    assert await checker(FakeRobotsSource(200, "")).allowed("h", "/x") is True
