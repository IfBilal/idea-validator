"""Shared pytest fixtures for integration tests (loaded via the root ``conftest.py``).

Integration tests need Postgres/Redis.

Set ``TEST_DATABASE_URL`` / ``TEST_REDIS_URL`` to use an already running instance (handy locally),
otherwise testcontainers starts disposable ones (CI: needs a running Docker).
"""

import os
from collections.abc import Iterator

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
