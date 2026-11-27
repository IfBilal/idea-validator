"""POST /fetch pipeline (docs/harness/10 §3): the only way any module reads the internet.

validate -> allow-list + SSRF guard -> cache -> run budget -> robots.txt -> per-domain rate
limit -> GET (pinned addresses, 10 s timeout, size cap, 2 retries, redirects re-checked) ->
sanitise -> cache -> response.
"""

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urljoin

from common.errors import (
    BudgetExceeded,
    DomainError,
    Forbidden,
    RateLimited,
    UpstreamError,
    UpstreamTimeout,
)
from common.schemas import FetchRequest, FetchResponse, SanitiserReport

from ..config_sources import SourcesConfig, source_for_host
from .cache import KeyValueCache
from .fetcher import Fetcher, RawResponse
from .ratelimit import TokenBucket
from .robots import RobotsChecker
from .sanitiser import sanitise
from .url_policy import CheckedUrl, UrlPolicy

DEFAULT_MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
MAX_RETRIES = 2
TIMEOUT_S = 10.0
BACKOFF_S = 0.5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
RETRY_STATUSES = frozenset({500, 502, 503, 504})
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


class RunBudget(Protocol):
    def check(self, run_id: str) -> None:
        """Raise ``BudgetExceeded`` if the run may not fetch another page."""

    def charge(self, run_id: str, nbytes: int) -> None:
        """Record one fetched page; raise ``BudgetExceeded`` if that exceeds the budget."""


class MemoryRunBudget:
    """Per-run page and byte budget. The Redis-backed ledger replaces it in the cluster."""

    def __init__(self, max_pages: int, max_bytes: int) -> None:
        self._max_pages, self._max_bytes = max_pages, max_bytes
        self._used: dict[str, tuple[int, int]] = {}

    def check(self, run_id: str) -> None:
        pages, _ = self._used.get(run_id, (0, 0))
        if pages >= self._max_pages:
            raise BudgetExceeded(
                "page budget for this run is used up", {"max_pages": self._max_pages}
            )

    def charge(self, run_id: str, nbytes: int) -> None:
        pages, used = self._used.get(run_id, (0, 0))
        self._used[run_id] = (pages + 1, used + nbytes)
        if used + nbytes > self._max_bytes:
            raise BudgetExceeded(
                "byte budget for this run is used up", {"max_bytes": self._max_bytes}
            )


class FetchMetrics(Protocol):
    def injection(self, source: str, count: int) -> None: ...

    def outcome(self, source: str, outcome: str) -> None: ...


Sleep = Callable[[float], Awaitable[None]]


MAX_JSON_DEPTH = 50
MAX_JSON_STRINGS = 20_000  # strings plus keys; beyond this the body is sanitised as plain text
DEFAULT_DEADLINE_S = 30.0  # for the whole request: retries and redirects share it
_CHARSET_NAMES = """
utf-8 utf8 ascii us-ascii latin-1 latin1 iso-8859-1 iso-8859-2 iso-8859-15
windows-1250 windows-1251 windows-1252 utf-16 utf-16le utf-16be
"""
SAFE_CHARSETS = frozenset(re.split(r"\s+", _CHARSET_NAMES.strip()))


def _no_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name}")


def _finite_float(text: str) -> float:
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError("number out of range")
    return value


def _parse_json(decoded: str) -> Any:
    """``None`` when the body is not usable JSON (invalid, non-finite numbers, too deep)."""
    try:
        return json.loads(decoded, parse_constant=_no_constant, parse_float=_finite_float)
    except (ValueError, RecursionError):
        return None


_JSON_ESCAPE = re.compile(
    r"\\(?:u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})|u([0-9a-fA-F]{4})|([\"\\\\/bfnrt]))"
)
_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


def _decode_json_escapes(text: str) -> str:
    """Decode JSON string escapes in one pass (so ``\\\\u0069`` stays literal, as in real JSON).

    A JSON body that cannot be parsed into structure (truncated, or too many strings) is
    sanitised as text; without this, ``\\u0069gnore previous instructions`` would pass the pattern
    matcher and be decoded later by whatever reads it, a model included.
    """

    def decode(match: re.Match[str]) -> str:
        if match.group(1):
            high, low = int(match.group(1), 16), int(match.group(2), 16)
            return chr(0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00))
        if match.group(3):
            return chr(int(match.group(3), 16))
        return _SIMPLE_ESCAPES[match.group(4)]

    return _JSON_ESCAPE.sub(decode, text)


def _too_many_strings(value: Any) -> bool:
    """Cheap, iterative count of strings and keys (no regex), so the limit costs microseconds."""
    seen = 0
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            seen += 1
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            seen += len(item)
            stack.extend(item.values())
        if seen > MAX_JSON_STRINGS:
            return True
    return False


def _sanitise_json(value: Any, depth: int = 0) -> tuple[Any, int]:
    """Sanitise every string value and key. Returns the new value and a removal count.

    Nesting deeper than ``MAX_JSON_DEPTH`` is cut off and counted as one removal.
    """
    if depth > MAX_JSON_DEPTH:
        return None, 1
    if isinstance(value, str):
        content_type = "text/html" if _HTML_TAG.search(value) else None
        result = sanitise(value, content_type)
        return result.text, result.removed_patterns
    if isinstance(value, list):
        total = 0
        items = []
        for item in value:
            cleaned, count = _sanitise_json(item, depth + 1)
            items.append(cleaned)
            total += count
        return items, total
    if isinstance(value, dict):
        total = 0
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            cleaned_key = sanitise(key, max_chars=200)
            name = cleaned_key.text
            if cleaned_key.flagged:
                total += cleaned_key.removed_patterns
                name = f"[removed key {index}]"
            if name in out:  # two keys sanitised to the same text: never overwrite a value
                name = f"{name}#{index}"
            out[name], count = _sanitise_json(item, depth + 1)
            total += count
        return out, total
    return value, 0


def _charset(content_type_header: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type_header, re.IGNORECASE)
    return match.group(1) if match else "utf-8"


def _is_textual(content_type: str) -> bool:
    return (
        content_type.startswith("text/")
        or content_type in {"application/json", "application/xml", "application/xhtml+xml"}
        or content_type.endswith("+json")
        or content_type.endswith("+xml")
    )


class FetchService:
    def __init__(
        self,
        *,
        policy: UrlPolicy,
        robots: RobotsChecker,
        bucket: TokenBucket,
        cache: KeyValueCache,
        fetcher: Fetcher,
        budget: RunBudget,
        sources: SourcesConfig,
        metrics: FetchMetrics,
        sleep: Sleep,
        clock: Callable[[], float],
        deadline_s: float = DEFAULT_DEADLINE_S,
    ) -> None:
        self._deadline_s = deadline_s
        self._policy, self._robots, self._bucket = policy, robots, bucket
        self._cache, self._fetcher, self._budget = cache, fetcher, budget
        self._sources, self._metrics, self._sleep, self._clock = sources, metrics, sleep, clock

    async def fetch(self, req: FetchRequest) -> FetchResponse:
        label = "other"  # metrics label: derived from the host, never from caller input
        try:
            checked = await self._policy.check(req.url)
            label, entry = source_for_host(self._sources, checked.host)
            if not entry.enabled:
                raise Forbidden("this source is disabled", {"reason": "SOURCE_DISABLED"})
            try:
                async with asyncio.timeout(self._deadline_s):
                    response, outcome = await self._fetch(
                        req, checked, entry.rate_limit_per_min, entry.cache_ttl_s, label
                    )
            except TimeoutError as exc:
                raise UpstreamTimeout(
                    "the fetch exceeded its overall deadline", {"host": checked.host}
                ) from exc
        except Forbidden:
            self._metrics.outcome(label, "blocked")
            raise
        except RateLimited:
            self._metrics.outcome(label, "rate_limited")
            raise
        except BudgetExceeded:
            self._metrics.outcome(label, "budget")
            raise
        except DomainError:
            self._metrics.outcome(label, "error")
            raise
        self._metrics.outcome(label, outcome)
        return response

    async def _fetch(
        self, req: FetchRequest, checked: CheckedUrl, rate: int, ttl_s: int, label: str
    ) -> tuple[FetchResponse, str]:
        normalised = f"https://{checked.host}{checked.path_and_query}"
        key = "fetch:" + hashlib.sha256(normalised.encode()).hexdigest()
        cached = await self._cache.get(key)
        if cached is not None:
            hit = FetchResponse.model_validate(cached).model_copy(
                update={"cache_hit": True, "url": req.url}
            )
            return hit, "cache_hit"
        if req.run_id:
            self._budget.check(req.run_id)
        if not await self._robots.allowed(checked.host, checked.path_and_query):
            raise Forbidden("robots.txt disallows this path", {"reason": "ROBOTS_DISALLOWED"})
        await self._bucket.acquire(checked.host, rate)

        max_bytes = req.max_bytes or DEFAULT_MAX_BYTES
        current = checked
        for hop in range(MAX_REDIRECTS + 1):
            raw = await self._get_with_retries(current, max_bytes)
            if raw.status not in REDIRECT_STATUSES:
                break
            if hop == MAX_REDIRECTS:
                raise UpstreamError("too many redirects", {"host": current.host})
            location = raw.headers.get("location")
            if not location:
                raise UpstreamError("redirect without a location", {"host": current.host})
            target = urljoin(f"https://{current.host}{current.path_and_query}", location)
            moved = await self._policy.check(target)  # allow-list + SSRF checks on every hop
            _, moved_entry = source_for_host(self._sources, moved.host)
            if not moved_entry.enabled:  # a redirect must not reach a source we switched off
                raise Forbidden("this source is disabled", {"reason": "SOURCE_DISABLED"})
            if not await self._robots.allowed(moved.host, moved.path_and_query):
                raise Forbidden("robots.txt disallows this path", {"reason": "ROBOTS_DISALLOWED"})
            if moved.host != current.host:
                await self._bucket.acquire(moved.host, moved_entry.rate_limit_per_min)
            current = moved
        final = f"https://{current.host}{current.path_and_query}"

        if raw.status == 429:
            retry_after = raw.headers.get("retry-after", "")
            seconds = int(retry_after) if retry_after.isdigit() else 60
            raise RateLimited("the upstream is rate limiting us", {"retry_after": seconds})
        # CPU-bound and up to about a second for a big page: keep it off the event loop
        response = await asyncio.to_thread(self._build, req.url, final, raw)
        if req.run_id:
            self._budget.charge(req.run_id, len(raw.body))
        if response.sanitiser.removed_patterns:
            self._metrics.injection(label, response.sanitiser.removed_patterns)
        if 200 <= raw.status < 300 and not raw.truncated:  # a cut-off body must not be reused
            await self._cache.set(key, response.model_dump(mode="json", by_alias=True), ttl_s)
        return response, "fetched"

    async def _get_with_retries(self, checked: CheckedUrl, max_bytes: int) -> RawResponse:
        for attempt in range(MAX_RETRIES + 1):
            last = attempt == MAX_RETRIES
            try:
                raw = await self._fetcher.get(checked, TIMEOUT_S, max_bytes)
            except (UpstreamTimeout, UpstreamError):
                if last:
                    raise
                await self._sleep(BACKOFF_S * 2**attempt)
                continue
            if raw.status in RETRY_STATUSES:
                if last:
                    raise UpstreamError("the upstream keeps failing", {"status": raw.status})
                await self._sleep(BACKOFF_S * 2**attempt)
                continue
            return raw
        raise UpstreamError("the upstream could not be reached")  # pragma: no cover

    def _build(self, url: str, final_url: str, raw: RawResponse) -> FetchResponse:
        header = raw.headers.get("content-type", "")
        content_type = header.split(";")[0].strip().lower()
        text: str | None = None
        data: Any = None
        removed = 0
        if _is_textual(content_type):
            decoded = raw.body.decode(_safe_charset(header), errors="replace")
            parsed: Any = None
            if content_type.endswith("json") and not raw.truncated:
                parsed = _parse_json(decoded)
                if parsed is not None and _too_many_strings(parsed):
                    parsed = None  # sanitised as text below, which is capped
            if parsed is not None:
                data, removed = _sanitise_json(parsed)
            else:
                source = _decode_json_escapes(decoded) if content_type.endswith("json") else decoded
                result = sanitise(source, content_type)
                text, removed = result.text, result.removed_patterns
        return FetchResponse(
            url=url,
            final_url=final_url,
            status=raw.status,
            content_type=header or None,
            text=text,
            json_=data,
            cache_hit=False,
            sanitiser=SanitiserReport(removed_patterns=removed, flagged=removed > 0),
            fetched_at=datetime.fromtimestamp(self._clock(), UTC),
        )


def _safe_charset(header: str) -> str:
    name = _charset(header).lower()
    return name if name in SAFE_CHARSETS else "utf-8"
