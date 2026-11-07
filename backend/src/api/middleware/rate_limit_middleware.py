"""
GlobeLens AI — RateLimitMiddleware
==================================
Sliding-window daily API rate limiting, applied to every /api/v1 request when
ENFORCE_RATE_LIMITS is true.

The bucket is chosen from the credential, never from a request-supplied id:

  - a `glb_` API key buckets on the SHA-256 digest (the same value the
    database stores; a raw secret is never written to Redis)
  - a valid access-token JWT buckets on the token's sub (the user)
  - anything else — missing, malformed, or undecodable — buckets on the client
    IP as an anonymous caller

Plan resolution (FREE/PRO daily allowances, ENTERPRISE unlimited) is looked up
from the database on first sighting of a bucket and cached in Redis for five
minutes, so the steady state costs one evaluation per request with no DB hit.
The cache is invalidated immediately on an admin plan assignment, so a fresh
tier is never misbucketed for the rest of the TTL.

Enforcement OFF (the default) short-circuits before any Redis or DB work, so
existing behaviour is byte-for-byte unchanged until an operator flips it on.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from fastapi import Request
from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from src.core.config import settings
from src.data.entities.models import PlanTier

_META_TTL_SECONDS = 300


def _unauthorized_plan_cache_key(bucket_kind: str, identity: str) -> str:
    return f"rlmeta:{bucket_kind}:{identity}"


async def _cached_plan(cache_key: str, fetch) -> Optional[PlanTier]:
    from src.services.rate_limit_service import rate_limit_service

    client = await rate_limit_service._get_client()
    cached = await client.get(cache_key)
    if cached is not None:
        return PlanTier(cached)
    plan = await fetch()
    if plan is not None:
        await client.set(cache_key, plan.value, ex=_META_TTL_SECONDS)
    return plan


async def _lookup_key_owner_plan(key_hash: str) -> Optional[PlanTier]:
    from sqlalchemy import select

    from src.core.database import AsyncSessionFactory
    from src.data.entities.models import ApiKey, User

    async with AsyncSessionFactory() as db:
        row = (
            await db.execute(
                select(User.plan).join(ApiKey, ApiKey.user_id == User.id)
                .where(ApiKey.key_hash == key_hash)
            )
        ).scalars().first()
        return row


async def _lookup_user_plan(user_id: str) -> Optional[PlanTier]:
    from sqlalchemy import select

    from src.core.database import AsyncSessionFactory
    from src.data.entities.models import User

    async with AsyncSessionFactory() as db:
        row = (await db.execute(
            select(User.plan).where(User.id == user_id)
        )).scalars().first()
        return row


async def invalidate_user_plan_cache(user_id: str) -> None:
    """Drop the Redis plan cache for a user and their API keys.

    The plan cache lives for _META_TTL_SECONDS, so without this a freshly
    assigned plan keeps shaping rate limits for up to five minutes. Only called
    when ENFORCE_RATE_LIMITS is on — enforcement off never writes these keys.
    """
    from sqlalchemy import select

    from src.core.database import AsyncSessionFactory
    from src.data.entities.models import ApiKey
    from src.services.rate_limit_service import rate_limit_service

    client = await rate_limit_service._get_client()
    keys_to_delete = [f"rlmeta:user:{user_id}"]
    async with AsyncSessionFactory() as db:
        key_hashes = (
            await db.execute(
                select(ApiKey.key_hash).where(ApiKey.user_id == user_id)
            )
        ).scalars().all()
    keys_to_delete.extend(f"rlmeta:key:{h}" for h in key_hashes)
    if keys_to_delete:
        await client.delete(*keys_to_delete)


async def resolve_bucket( request: Request) -> Tuple[str, Optional[PlanTier]]:
    """
    Map this request to (bucket, plan). Buckets are cheap string keys; plan
    drives the daily allowance and is cached in Redis.
    """
    from src.services.rate_limit_service import api_key_bucket, ip_bucket, user_bucket

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token.startswith("glb_"):
            bucket = api_key_bucket(token)
            plan = await _cached_plan(
                _unauthorized_plan_cache_key("key", bucket.split(":", 1)[1]),
                lambda: _lookup_key_owner_plan(bucket.split(":", 1)[1]),
            )
            return bucket, plan
        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )
        except Exception:
            payload = None
        if payload and payload.get("sub"):
            sub = str(payload["sub"])
            plan = await _cached_plan(
                _unauthorized_plan_cache_key("user", sub),
                lambda: _lookup_user_plan(sub),
            )
            return user_bucket(sub), plan

    ip = request.client.host if request.client is not None else "unknown"
    return ip_bucket(ip), PlanTier.FREE


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce the daily API allowance per principal (off by default)."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        from src.services.rate_limit_service import (
            plan_daily_limit,
            rate_limit_service,
        )

        if not settings.ENFORCE_RATE_LIMITS:
            return await call_next(request)
        if not request.url.path.startswith("/api/v1"):
            return await call_next(request)

        bucket, plan = await resolve_bucket(request)
        limit = plan_daily_limit(plan)
        if limit is None:
            return await call_next(request)

        result = await rate_limit_service.check(bucket, limit)
        reset_at = int(result.reset_at_ms / 1000)
        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(result.remaining),
            "X-RateLimit-Reset": str(reset_at),
        }
        if not result.allowed:
            retry_after = result.retry_after_seconds
            headers["Retry-After"] = str(retry_after)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded. This principal is allowed "
                        f"{limit} requests per day; retry after "
                        f"{retry_after}s."
                    )
                },
                headers=headers,
            )

        response = await call_next(request)
        for name, value in headers.items():
            response.headers.setdefault(name, value)
        return response


__all__ = ["RateLimitMiddleware", "invalidate_user_plan_cache"]