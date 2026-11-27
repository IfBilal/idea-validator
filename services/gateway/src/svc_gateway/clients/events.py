"""Live run events from Redis pub/sub (``run:{run_id}``), published by M3.

Subscriptions are per SSE connection and always closed by the caller. Messages are returned as raw
text: the stream validates them against ``RunEvent`` before anything reaches a browser.
"""

from typing import Any, Protocol

from common.logging import get_logger

log = get_logger("gateway.events")


def channel_for(run_id: str) -> str:
    return f"run:{run_id}"


class Subscription(Protocol):
    async def next(self, timeout_s: float) -> str | None:
        """The next message, or ``None`` if none arrived within ``timeout_s``."""

    async def aclose(self) -> None: ...


class EventBus(Protocol):
    async def subscribe(self, run_id: str) -> Subscription: ...


class RedisSubscription:
    def __init__(self, pubsub: Any, channel: str) -> None:
        self._pubsub = pubsub
        self._channel = channel

    async def next(self, timeout_s: float) -> str | None:
        message = await self._pubsub.get_message(
            ignore_subscribe_messages=True, timeout=max(0.0, timeout_s)
        )
        if not isinstance(message, dict) or message.get("type") != "message":
            return None
        data = message.get("data")
        if isinstance(data, bytes):
            try:
                return data.decode()
            except UnicodeDecodeError:
                return None
        return data if isinstance(data, str) else None

    async def aclose(self) -> None:
        try:
            await self._pubsub.unsubscribe(self._channel)
        except Exception as exc:  # closing must never raise into the response teardown
            log.warning("unsubscribe_failed", error=type(exc).__name__)
        finally:
            await self._pubsub.aclose()


class RedisEventBus:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def subscribe(self, run_id: str) -> Subscription:
        pubsub = self._redis.pubsub()
        channel = channel_for(run_id)
        try:
            await pubsub.subscribe(channel)
        except BaseException:
            await pubsub.aclose()
            raise
        return RedisSubscription(pubsub, channel)
