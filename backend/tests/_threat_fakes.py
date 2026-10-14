"""Shared in-memory Redis fake for Threat Lens hermetic tests.

Same philosophy as ``tests/test_rate_limit.py``'s own fake: enough surface to
exercise the code under test (get/set/delete, INCR for the rate window, and
the RPUSH/LTRIM/LRANGE trio for the anomaly window) without a live cache.
"""
import pytest

from app.services.cache_service import cache_service


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                deleted += 1
        return deleted

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        self.store.setdefault("_ttl", {})[key] = ttl
        return True

    async def rpush(self, key, value):
        self.store.setdefault(key, []).append(value)
        return len(self.store[key])

    async def ltrim(self, key, start, stop):
        values = self.store.get(key, [])
        n = len(values)
        start = max(0, min(n + start if start < 0 else start, n))
        stop = max(0, min(n + stop if stop < 0 else stop, n - 1))
        self.store[key] = values[start : stop + 1] if stop >= start else []
        return True

    async def lrange(self, key, start, stop):
        values = self.store.get(key, [])
        n = len(values)
        start = max(0, min(n + start if start < 0 else start, n))
        stop = max(0, min(n + stop if stop < 0 else stop, n - 1))
        return values[start : stop + 1] if stop >= start else []


@pytest.fixture
def threat_cache(monkeypatch):
    fake = FakeRedis()

    async def _get_client():
        return fake

    monkeypatch.setattr(cache_service, "_get_client", _get_client)
    return fake