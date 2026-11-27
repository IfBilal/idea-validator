import pytest
from redis.asyncio import Redis

from svc_gateway.clients.events import RedisEventBus

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(redis_url: str):  # type: ignore[no-untyped-def]
    client = Redis.from_url(redis_url)
    yield client
    await client.aclose()


async def test_a_published_event_reaches_only_the_subscriber_of_that_run(redis: Redis) -> None:
    bus = RedisEventBus(redis)
    mine = await bus.subscribe("run_a")
    other = await bus.subscribe("run_b")
    try:
        await redis.publish("run:run_a", '{"seq": 1}')
        assert await mine.next(2.0) == '{"seq": 1}'
        assert await other.next(0.2) is None
    finally:
        await mine.aclose()
        await other.aclose()
