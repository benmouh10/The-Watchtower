"""
GlobeLens AI — RateLimitService
================================
Redis-backed sliding-window rate limiting for the API surface.

A sorted-set per bucket holds the millisecond timestamps of recent requests;
each request atomically (via one Lua script) drops entries older than the
window, counts the survivors, records the request when under the limit, and
returns the number of milliseconds until the oldest entry ages out of the
window when the bucket is exhausted. That yields a *sliding* window (no burst
at the top of an hour) and an exact Retry-After.

Buckets are dimensionless strings built by the middleware, e.g.
  key:<sha256 of raw API key>   — an API key, identified by its digest only
  user:<uuid>                   — a logged-in JWT session
  ip:<address>                  — anonymous callers

Daily allowances are plan-derived. ENTERPRISE has no cap, so those requests
are not counted or rate-limited at all.
"""
import math
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as aioredis

from src.core.config import settings
from src.data.entities.models import PlanTier

# Sliding-window log for one daily bucket:
#   ZREMRANGEBYSCORE drops stale entries, ZCARD counts the survivors, and a
#   fresh entry is recorded only when under the limit. The member token must be
#   unique per request or two requests in the same millisecond would collapse
#   onto one ZSET slot.
_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = 0
  if oldest[2] then
    retry_ms = oldest[2] + window_ms - now
    if retry_ms < 0 then retry_ms = 0 end
  end
  return { 0, count, retry_ms }
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window_ms)
return { 1, count + 1, 0 }
"""

DAILY_WINDOW_MS = 86_400_000  # 24h sliding window


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_ms: float
    limit: int
    reset_at_ms: int

    @property
    def retry_after_seconds(self) -> int:
        return math.ceil(self.retry_after_ms / 1000)


def plan_daily_limit(plan: Optional[PlanTier]) -> Optional[int]:
    """Daily API allowance by plan; None means unlimited (not counted)."""
    if plan == PlanTier.ENTERPRISE:
        return None
    if plan == PlanTier.PRO:
        return settings.API_DAILY_LIMIT_PRO
    return settings.API_DAILY_LIMIT


def api_key_bucket(raw_key: str) -> str:
    """A key bucket from its SHA-256 digest so no raw secret ever sits in Redis."""
    import hashlib

    return "key:" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def user_bucket(user_id) -> str:
    return f"user:{user_id}"


def ip_bucket(address: str) -> str:
    return f"ip:{address}"


class RateLimitService:
    """
    Sliding-window limiter over Redis. A single shared client mirrors
    CacheService's lazy pattern so rate limiting works without touching the
    request's own database session.
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

    async def check(
        self,
        bucket: str,
        limit: Optional[int],
        member: Optional[str] = None,
    ) -> RateLimitResult:
        """Record one request in `bucket`. None limit bypasses counting entirely."""
        if limit is None or limit <= 0:
            return RateLimitResult(
                allowed=True, remaining=-1, retry_after_ms=0.0, limit=0, reset_at_ms=0
            )
        client = await self._get_client()
        now_ms = int(time.time() * 1000)
        member_token = member or f"{now_ms}-{uuid.uuid4().hex[:10]}"
        key = f"rl:{bucket}"
        allowed, count, retry_ms = await client.eval(
            _RATE_LIMIT_SCRIPT,
            1,
            key,
            str(now_ms),
            str(DAILY_WINDOW_MS),
            str(limit),
            member_token,
        )
        return RateLimitResult(
            allowed=bool(allowed),
            remaining=max(0, limit - int(count)),
            retry_after_ms=float(retry_ms),
            limit=limit,
            reset_at_ms=now_ms + max(int(retry_ms), 0),
        )

    async def reset(self, bucket: str) -> None:
        """Clear a bucket (test/diagnostic support)."""
        client = await self._get_client()
        await client.delete(f"rl:{bucket}")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# Singleton for dependency injection.
rate_limit_service = RateLimitService()


__all__ = [
    "DAILY_WINDOW_MS",
    "RateLimitResult",
    "RateLimitService",
    "api_key_bucket",
    "ip_bucket",
    "plan_daily_limit",
    "rate_limit_service",
    "user_bucket",
]