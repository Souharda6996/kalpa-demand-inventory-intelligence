"""Worker heartbeat test with a fake redis."""

from kalpa_worker.main import HEARTBEAT_TTL_SECONDS, WORKER_HEARTBEAT_KEY, heartbeat


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def set(self, key: str, value: str, ex: int) -> None:
        self.calls.append((key, value, ex))


async def test_heartbeat_sets_key_with_ttl() -> None:
    r = FakeRedis()
    await heartbeat({"redis": r})
    assert r.calls == [(WORKER_HEARTBEAT_KEY, "1", HEARTBEAT_TTL_SECONDS)]
