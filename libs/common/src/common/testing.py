"""Shared pytest fixtures for integration tests (loaded with ``-p common.testing``).

Integration tests need Postgres/Redis.

Set ``TEST_DATABASE_URL`` / ``TEST_REDIS_URL`` to use an already running instance (handy locally),
otherwise testcontainers starts disposable ones (CI: needs a running Docker).
"""

import os
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    if url := os.environ.get("TEST_DATABASE_URL"):
        yield url
        return
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.4-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    if url := os.environ.get("TEST_REDIS_URL"):
        yield url
        return
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7.4-alpine") as redis:
        host, port = redis.get_container_host_ip(), redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


class EmulatedRedis:
    """In-memory stand-in for the Redis calls the budget ledger makes, including the reserve script.

    ``eval`` only accepts ``RESERVE_SCRIPT`` and mirrors it step for step: libs/common/tests/
    test_budget_reserve.py runs the same scenarios against this class and a real Redis, so the two
    cannot drift apart. Single event loop, no awaits inside ``eval``: atomic like the real script.
    """

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}
        self.values: dict[str, str] = {}

    async def hincrby(self, name: str, key: str, amount: int) -> int:
        h = self.hashes.setdefault(name, {})
        h[key] = int(h.get(key, 0)) + int(amount)
        return int(h[key])

    async def hincrbyfloat(self, name: str, key: str, amount: float) -> float:
        h = self.hashes.setdefault(name, {})
        h[key] = float(h.get(key, 0.0)) + float(amount)
        return float(h[key])

    async def set(
        self, name: str, value: str, *, ex: int | None = None, get: bool = False
    ) -> bytes | bool | None:
        previous = self.values.get(name)
        self.values[name] = value
        if ex is not None:
            self.ttls[name] = int(ex)
        if get:
            return None if previous is None else previous.encode()
        return True

    async def delete(self, *names: str) -> int:
        removed = [n for n in names if self.values.pop(n, None) is not None]
        return len(removed)

    async def get(self, name: str) -> bytes | None:
        value = self.values.get(name)
        return None if value is None else value.encode()

    async def incr(self, name: str) -> int:
        self.values[name] = str(int(self.values.get(name, "0")) + 1)
        return int(self.values[name])

    async def hgetall(self, name: str) -> dict[bytes, bytes]:
        return {k.encode(): str(v).encode() for k, v in self.hashes.get(name, {}).items()}

    async def expire(self, name: str, seconds: int) -> bool:
        self.ttls[name] = int(seconds)
        return name in self.hashes

    async def ttl(self, name: str) -> int:
        return self.ttls.get(name, -1)

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        from .budget import RESERVE_SCRIPT, RESTORE_SCRIPT

        if script == RESTORE_SCRIPT and numkeys == 1:
            name, tokens, pages, steps, cost, bonus, ttl = keys_and_args
            if name in self.hashes:
                return 0
            self.hashes[str(name)] = {
                "tokens": int(tokens),
                "pages": int(pages),
                "steps": int(steps),
                "cost_usd": float(cost),
                "cost_bonus_usd": float(bonus),
            }
            self.ttls[str(name)] = int(ttl)
            return 1
        if script != RESERVE_SCRIPT or numkeys != 1:
            raise NotImplementedError("EmulatedRedis only knows the budget scripts")
        key, *argv = keys_and_args
        e_tokens, e_pages, e_steps, e_cost, m_tokens, m_pages, m_steps, m_cost, tol, ttl = (
            float(a) for a in argv
        )
        h = self.hashes.setdefault(str(key), {})

        def n(field: str) -> float:
            return float(h.get(field, 0))

        over: list[bytes] = []
        if n("tokens") + e_tokens > m_tokens:
            over.append(b"tokens")
        if n("pages") + e_pages > m_pages:
            over.append(b"pages")
        if n("steps") + e_steps > m_steps:
            over.append(b"steps")
        if n("cost_usd") + e_cost > m_cost + n("cost_bonus_usd") + tol:
            over.append(b"cost_usd")
        if over:
            return over
        if e_steps > 0:
            h["steps"] = n("steps") + e_steps
        self.ttls[str(key)] = int(ttl)
        return over


# Shared redaction corpus: (text, fragment that must not survive). Secret-shaped strings are built
# from parts so that secret scanners do not flag this file.
def _j(*parts: str) -> str:
    return "".join(parts)


REDACTION_POSITIVE: list[tuple[str, str]] = [
    ("Authorization: Bearer abc.def-123_x", "abc.def-123_x"),
    ("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpwYXNzd29yZDEyMw"),
    (_j("key sk", "-abcdefghijklmnopqrstuvwx ok"), "abcdefghijklmnopqrstuvwx"),
    (_j("stripe sk", "_live_", "4eC39HqLyjWDarjtT1zdp7dc"), "4eC39HqLyjWDarjtT1zdp7dc"),
    (_j("stripe sk", "_test_", "4eC39HqLyjWDarjtT1zdp7dc"), "4eC39HqLyjWDarjtT1zdp7dc"),
    (_j("gh", "p_", "a" * 36), "a" * 36),
    (_j("github", "_pat_", "11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz"), "11ABCDEFG0abcdefghijkl"),
    (_j("aws ", "AKIA", "IOSFODNN7EXAMPLE", " here"), "IOSFODNN7EXAMPLE"),
    (_j("slack ", "xox", "b-123456789012-abcdefghijkl"), "123456789012-abcdefghijkl"),
    (_j("google ", "AIza", "SyA1234567890abcdefghijklmnopqrstuv"), "SyA1234567890abcdef"),
    ("postgres://admin:hunter2pw@db.internal:5432/app", "hunter2pw"),
    ("https://user:p4ssw0rd@example.com/x", "p4ssw0rd"),
    (_j("api", "_key=", "abcd1234efgh5678 failed"), "abcd1234efgh5678"),
    (_j("retry with api", "key: ", "zzzz9999yyyy"), "zzzz9999yyyy"),
    (_j("pass", "word=", "correct-horse-battery"), "correct-horse-battery"),
    (_j("tok", "en = ", "9f8e7d6c5b4a"), "9f8e7d6c5b4a"),
]
REDACTION_NEGATIVE: list[str] = [
    "a student idea about invoices",
    "max_tokens=100 and token_count: 5",
    "the keyboard key was pressed",
    "see https://example.com/path?x=1",
    "skill-based sk-short",
    "email-like text without at sign",
    "cost 0.0123 usd over 12 steps",
    "card_aaaaaaaa#c3 is evidence",
]
