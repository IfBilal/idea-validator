"""Allow-list and SSRF guard (docs/harness/13 §2): exact hosts, https only, no private addresses."""

import pytest

from common.errors import Forbidden, UpstreamError
from svc_tools.services.url_policy import UrlPolicy

ALLOWED = ["api.github.com", "hacker-news.firebaseio.com", "hn.algolia.com"]
PUBLIC = "140.82.112.5"


class FakeResolver:
    def __init__(
        self, answers: dict[str, list[str]] | None = None, default: list[str] | None = None
    ):
        self.answers = answers or {}
        self.default = default if default is not None else [PUBLIC]
        self.calls: list[str] = []

    async def resolve(self, host: str) -> list[str]:
        self.calls.append(host)
        return self.answers.get(host, self.default)


def policy(resolver: FakeResolver | None = None, allowed: list[str] | None = None) -> UrlPolicy:
    return UrlPolicy(ALLOWED if allowed is None else allowed, resolver or FakeResolver())


async def reason(url: str, p: UrlPolicy | None = None) -> str:
    with pytest.raises(Forbidden) as caught:
        await (p or policy()).check(url)
    return str(caught.value.details["reason"])


# ---- accepted
async def test_an_allow_listed_https_url_is_accepted_and_its_addresses_returned() -> None:
    checked = await policy().check("https://api.github.com/search/repositories?q=saas&page=2")
    assert checked.host == "api.github.com"
    assert checked.addresses == (PUBLIC,)
    assert checked.path_and_query == "/search/repositories?q=saas&page=2"


@pytest.mark.parametrize(
    "url",
    [
        "https://API.GitHub.com/x",
        "https://api.github.com./x",
        "https://api.github.com:443/x",
        "https://api.github.com",
        "https://api.github.com/x#fragment",
    ],
)
async def test_harmless_variants_of_an_allowed_host_are_normalised(url: str) -> None:
    checked = await policy().check(url)
    assert checked.host == "api.github.com"
    assert "#" not in checked.path_and_query


# ---- scheme, userinfo, port, shape
@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com/x",
        "ftp://api.github.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain;base64,QUJD",
        "gopher://api.github.com/",
        "//api.github.com/x",
        "api.github.com/x",
        "",
        "   ",
    ],
)
async def test_only_https_urls_are_accepted(url: str) -> None:
    assert await reason(url) in {"INVALID_URL", "DOMAIN_NOT_ALLOWED"}


@pytest.mark.parametrize(
    "url",
    [
        "https://user@api.github.com/x",
        "https://user:pass@api.github.com/x",
        "https://:pass@api.github.com/x",
        "https://api.github.com@evil.example/x",
        "https://api.github.com:443@evil.example/x",
        "https://api.github.com\\@evil.example/x",
        "https://api.github.com%2f@evil.example/x",
    ],
)
async def test_credentials_in_the_url_and_userinfo_tricks_are_rejected(url: str) -> None:
    await reason(url)


@pytest.mark.parametrize("port", [80, 8080, 8443, 22, 6379, 5432, 1, 65535, 444])
async def test_only_port_443_is_allowed(port: int) -> None:
    assert await reason(f"https://api.github.com:{port}/x") == "INVALID_URL"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com:0/x",
        "https://api.github.com:99999/x",
        "https://api.github.com:abc/x",
    ],
)
async def test_malformed_ports_are_rejected(url: str) -> None:
    await reason(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/a b",
        "https://api.github.com/a\tb",
        "https://api.github.com/a\nb",
        "https://api.github.com/\x00",
        "https://api.github.com/a\x7fb",
    ],
)
async def test_whitespace_and_control_characters_in_the_url_are_rejected(url: str) -> None:
    assert await reason(url) == "INVALID_URL"


async def test_url_length_boundary_is_2048() -> None:
    base = "https://api.github.com/"
    ok = base + "a" * (2048 - len(base))
    assert (await policy().check(ok)).host == "api.github.com"
    assert await reason(ok + "a") == "INVALID_URL"


# ---- host matching is exact
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.api.github.com/x",
        "https://api.github.com.evil.example/x",
        "https://xapi.github.com/x",
        "https://api.github.co/x",
        "https://github.com/x",
        "https://api-github.com/x",
        "https://api.github.com%2eevil.example/x",
        "https://api.github.com%00.evil.example/x",
        "https://localhost/x",
        "https://metadata.google.internal/x",
        "https://example.com/x",
    ],
)
async def test_hosts_must_match_the_allow_list_exactly(url: str) -> None:
    assert await reason(url) in {"DOMAIN_NOT_ALLOWED", "INVALID_URL"}


async def test_look_alike_unicode_hosts_are_rejected() -> None:
    cyrillic_a = "\u0430pi.github.com"
    assert await reason(f"https://{cyrillic_a}/x") in {"DOMAIN_NOT_ALLOWED", "INVALID_URL"}


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/x",
        "https://192.168.1.1/x",
        "https://2130706433/x",
        "https://0x7f.0.0.1/x",
        "https://017700000001/x",
        "https://0/x",
        "https://[::ffff:127.0.0.1]/x",
        "https://[fe80::1]/x",
    ],
)
async def test_ip_literals_in_any_notation_are_never_accepted(url: str) -> None:
    assert await reason(url) in {"DOMAIN_NOT_ALLOWED", "INVALID_URL"}


async def test_an_ip_literal_is_rejected_even_if_someone_adds_it_to_the_allow_list() -> None:
    p = policy(allowed=["127.0.0.1"])
    assert await reason("https://127.0.0.1/x", p) == "INVALID_URL"


async def test_an_empty_allow_list_denies_everything() -> None:
    assert await reason("https://api.github.com/x", policy(allowed=[])) == "DOMAIN_NOT_ALLOWED"


async def test_allow_list_entries_are_normalised() -> None:
    p = policy(allowed=["API.GitHub.com.", " hn.algolia.com "])
    assert (await p.check("https://api.github.com/x")).host == "api.github.com"
    assert (await p.check("https://hn.algolia.com/x")).host == "hn.algolia.com"


# ---- DNS answers pointing inside (rebinding / poisoned allow-listed names)
BLOCKED_ADDRESSES = [
    "127.0.0.1", "127.255.255.254", "10.0.0.5", "10.255.255.255", "172.16.0.1", "172.31.255.255",
    "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255",  # noqa: S104
    "240.0.0.1", "192.0.2.1", "198.51.100.7", "203.0.113.9",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1", "::ffff:127.0.0.1",
    "::ffff:10.0.0.1", "64:ff9b::7f00:1", "2001:db8::1",
]  # fmt: skip


@pytest.mark.parametrize("address", BLOCKED_ADDRESSES)
async def test_an_allowed_name_that_resolves_to_a_non_public_address_is_blocked(
    address: str,
) -> None:
    p = policy(FakeResolver({"api.github.com": [address]}))
    assert await reason("https://api.github.com/x", p) == "BLOCKED_ADDRESS"


async def test_one_private_answer_among_public_ones_blocks_the_request() -> None:
    p = policy(FakeResolver({"api.github.com": [PUBLIC, "10.1.2.3"]}))
    assert await reason("https://api.github.com/x", p) == "BLOCKED_ADDRESS"


async def test_public_ipv4_and_ipv6_answers_are_accepted() -> None:
    p = policy(FakeResolver({"api.github.com": [PUBLIC, "2606:50c0:8000::154"]}))
    assert len((await p.check("https://api.github.com/x")).addresses) == 2


async def test_unresolvable_hosts_are_an_upstream_error_not_a_pass() -> None:
    with pytest.raises(UpstreamError):
        await policy(FakeResolver({"api.github.com": []})).check("https://api.github.com/x")


async def test_garbage_addresses_from_the_resolver_are_blocked() -> None:
    p = policy(FakeResolver({"api.github.com": ["not-an-ip"]}))
    assert await reason("https://api.github.com/x", p) == "BLOCKED_ADDRESS"


async def test_disallowed_hosts_are_never_resolved() -> None:
    resolver = FakeResolver()
    await reason("https://evil.example/x", policy(resolver))
    assert resolver.calls == []
