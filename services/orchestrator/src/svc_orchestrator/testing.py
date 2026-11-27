"""Test doubles shared by the orchestrator's unit and component tests (not used at runtime)."""


class FakeRedis:
    """The async Redis commands the orchestrator uses (hashes, pub/sub publish), in memory."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []
        self.alive = True

    async def hincrby(self, name: str, key: str, amount: int) -> int:
        h = self.hashes.setdefault(name, {})
        h[key] = int(h.get(key, 0)) + amount
        return int(h[key])

    async def hincrbyfloat(self, name: str, key: str, amount: float) -> float:
        h = self.hashes.setdefault(name, {})
        h[key] = float(h.get(key, 0.0)) + amount
        return float(h[key])

    async def hgetall(self, name: str) -> dict[bytes, bytes]:
        return {k.encode(): str(v).encode() for k, v in self.hashes.get(name, {}).items()}

    async def expire(self, name: str, time: int) -> bool:
        self.ttls[name] = time
        return name in self.hashes

    async def ttl(self, name: str) -> int:
        return self.ttls.get(name, -1)

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def ping(self) -> bool:
        if not self.alive:
            raise ConnectionError("redis is down")
        return True
