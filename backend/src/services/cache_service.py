"""
GlobeLens AI — CacheService
Wraps Redis operations for the CacheRepository pattern.
Used by EmbeddingService, SearchService, and AuthService (token blacklisting).
"""
from typing import Any, Optional

import redis.asyncio as aioredis

from src.core.config import settings


class CacheService:
    """
    Async Redis wrapper providing get / set / invalidate operations.
    Implements the CacheService interface from the blueprint.
    """

    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None

    async def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = await aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._client

    async def get(self, key: str) -> Optional[str]:
        """Retrieve a cached value by key."""
        client = await self._get_client()
        return await client.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> bool:
        """Store a value with an optional TTL (default: 1 hour)."""
        client = await self._get_client()
        return await client.set(key, value, ex=ttl_seconds)

    async def invalidate(self, key: str) -> int:
        """Delete a cache entry. Returns number of keys deleted."""
        client = await self._get_client()
        return await client.delete(key)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# Singleton instance for dependency injection
cache_service = CacheService()
