"""
GlobeLens AI — IngestFeedController (ADMIN)

    GET    /api/v1/admin/ingest/feeds            list registered feeds
    POST   /api/v1/admin/ingest/feeds            register a feed
    GET    /api/v1/admin/ingest/feeds/{id}       one feed + recent health
    DELETE /api/v1/admin/ingest/feeds/{id}       deactivate a feed
    POST   /api/v1/admin/ingest/feeds/{id}/activate   re-activate a feed
    POST   /api/v1/admin/ingest/feeds/run        drain all active feeds now

Operators register the feeds the ingest pipeline polls (RSS, Reddit, EDGAR,
X). Deactivation is a flag flip, not a delete, so the row stays as audit.
"""
import uuid as uuid_module
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import get_current_user
from src.core.config import settings
from src.core.database import get_db
from src.data.entities.models import IngestFeed, User, UserRole
from src.services.ingest_adapters import FEED_TYPES

router = APIRouter()


class RegisterFeedRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    feed_type: str = Field(..., description=f"One of {list(FEED_TYPES)}")
    url: str = Field(..., min_length=1, max_length=1024)
    topic_hint: Optional[str] = Field(None, max_length=64)


def _serialize(feed: IngestFeed) -> dict:
    return {
        "id": str(feed.id),
        "name": feed.name,
        "feed_type": feed.feed_type,
        "url": feed.url,
        "topic_hint": feed.topic_hint,
        "is_active": feed.is_active,
        "last_fetched_at": feed.last_fetched_at.isoformat() if feed.last_fetched_at else None,
        "last_error": feed.last_error,
        "created_at": feed.created_at.isoformat() if feed.created_at else None,
    }


def _require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can manage ingest feeds",
        )


def _validate_type(feed_type: str) -> str:
    normalized = (feed_type or "").strip().lower()
    if normalized not in FEED_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown feed_type {feed_type!r}. Supported: {list(FEED_TYPES)}",
        )
    return normalized


@router.get("/feeds", summary="List registered ingest feeds (ADMIN)")
async def list_feeds(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    feeds = (await db.execute(
        select(IngestFeed).order_by(IngestFeed.created_at.desc())
    )).scalars().all()
    return {
        "items": [_serialize(f) for f in feeds],
        "count": len(feeds),
        "feed_types": list(FEED_TYPES),
        "max_feeds_per_run": settings.INGEST_MAX_FEEDS_PER_RUN,
        "max_items_per_feed": settings.INGEST_MAX_ITEMS_PER_FEED,
    }


@router.post("/feeds", status_code=status.HTTP_201_CREATED, summary="Register an ingest feed (ADMIN)")
async def register_feed(
    payload: RegisterFeedRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    feed_type = _validate_type(payload.feed_type)
    existing = (await db.execute(
        select(IngestFeed).where(
            IngestFeed.feed_type == feed_type,
            IngestFeed.url == payload.url.strip(),
        )
    )).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="This feed is already registered.")
    feed = IngestFeed(
        name=payload.name.strip(),
        feed_type=feed_type,
        url=payload.url.strip(),
        topic_hint=(payload.topic_hint or "").strip() or None,
    )
    db.add(feed)
    await db.commit()
    await db.refresh(feed)
    return _serialize(feed)


@router.get("/feeds/{feed_id}", summary="One ingest feed (ADMIN)")
async def get_feed(
    feed_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    feed = await _owned_feed(db, feed_id)
    return _serialize(feed)


@router.delete("/feeds/{feed_id}", summary="Deactivate an ingest feed (ADMIN)")
async def deactivate_feed(
    feed_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    feed = await _owned_feed(db, feed_id)
    if feed.is_active:
        feed.is_active = False
        await db.commit()
        return {**_serialize(feed), "deactivated": True}
    return {**_serialize(feed), "deactivated": False}


@router.post("/feeds/{feed_id}/activate", summary="Re-activate an ingest feed (ADMIN)")
async def activate_feed(
    feed_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    feed = await _owned_feed(db, feed_id)
    if not feed.is_active:
        feed.is_active = True
        feed.last_error = None
        await db.commit()
        return {**_serialize(feed), "activated": True}
    return {**_serialize(feed), "activated": False}


@router.post("/feeds/run", summary="Drain all active ingest feeds now (ADMIN)")
async def run_feeds_now(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    from src.services.ingest_service import FeedIngestService

    return await FeedIngestService(db).run_all()


async def _owned_feed(db: AsyncSession, feed_id: str) -> IngestFeed:
    try:
        feed_uuid = uuid_module.UUID(feed_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid feed id")
    feed = (await db.execute(
        select(IngestFeed).where(IngestFeed.id == feed_uuid)
    )).scalars().first()
    if feed is None:
        raise HTTPException(status_code=404, detail="Ingest feed not found")
    return feed