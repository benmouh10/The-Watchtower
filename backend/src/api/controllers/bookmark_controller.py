"""
GlobeLens AI — BookmarkController
Per-user saved events ("Dossiers").

Routes (this router is mounted at /api/v1/bookmarks):
    GET    /api/v1/bookmarks
    POST   /api/v1/bookmarks/events/{event_id}
    DELETE /api/v1/bookmarks/events/{event_id}
    GET    /api/v1/bookmarks/events/{event_id}

The event page previously had a bookmark button that only toggled React state,
so a "save" was lost on refresh and existed on no server. These routes make it
real and durable.

Every route is scoped to the authenticated user. A bookmark is private: there is
no route that reads another user's dossier, and listing is always filtered by
the caller's own id rather than an id taken from the request.
"""
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.api.controllers.auth_controller import get_current_user, require_scopes
from src.core.database import get_db
from src.data.entities.models import Article, Bookmark, Event, Source, User
from src.services.api_key_service import KEY_SCOPE_BOOKMARKS

# Dossiers are private per-user data reached by the web UI's JWT sessions
# (which pass scope gates) or by a machine key minted with the `bookmarks`
# scope.
router = APIRouter(dependencies=[Depends(require_scopes(KEY_SCOPE_BOOKMARKS))])

MAX_DOSSIER_ITEMS = 500


def _parse_uuid(raw: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} UUID format",
        )


def _serialize_bookmark(bookmark: Bookmark) -> Dict[str, Any]:
    """Serialize a bookmark with just enough event context to render a list row."""
    event = bookmark.event
    article_count = len(event.articles) if event and event.articles is not None else 0

    return {
        "id": str(bookmark.id),
        "created_at": bookmark.created_at.isoformat() if bookmark.created_at else None,
        "event": {
            "id": str(event.id),
            "title": event.title,
            "summary": event.summary,
            "topic": event.topic,
            "country": event.country,
            "importance_score": event.importance_score,
            "bias_lean": event.bias_lean.name if event.bias_lean else None,
            "is_classified": event.bias_lean is not None,
            "status": event.status,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "updated_at": event.updated_at.isoformat() if event.updated_at else None,
            "source_count": article_count,
            "source_names": sorted(
                {
                    a.source.name
                    for a in (event.articles or [])
                    if a.source is not None and a.source.name
                }
            ),
        } if event else None,
    }


@router.get("", summary="List the caller's saved events")
async def list_bookmarks(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    limit = max(1, min(limit, MAX_DOSSIER_ITEMS))

    result = await db.execute(
        select(Bookmark)
        .options(
            joinedload(Bookmark.event)
            .joinedload(Event.articles)
            .joinedload(Article.source)
        )
        .where(Bookmark.user_id == current_user.id)
        .order_by(Bookmark.created_at.desc(), Bookmark.id.asc())
        .limit(limit)
    )
    bookmarks = list(result.scalars().unique())

    return {
        "items": [_serialize_bookmark(b) for b in bookmarks],
        "count": len(bookmarks),
        "limit": limit,
    }


@router.get("/events/{event_id}", summary="Whether the caller has saved this event")
async def get_bookmark_state(
    event_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lets the event page render the real saved state instead of guessing."""
    event_uuid = _parse_uuid(event_id, "event")

    result = await db.execute(
        select(Bookmark.id).where(
            Bookmark.user_id == current_user.id,
            Bookmark.event_id == event_uuid,
        )
    )
    row = result.first()

    return {"event_id": str(event_uuid), "is_bookmarked": row is not None}


@router.post(
    "/events/{event_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Save an event to the caller's dossier (idempotent)",
)
async def create_bookmark(
    event_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event_uuid = _parse_uuid(event_id, "event")

    event_exists = await db.execute(select(Event.id).where(Event.id == event_uuid))
    if event_exists.first() is None:
        raise HTTPException(status_code=404, detail="Event not found")

    # Re-saving is a no-op rather than a duplicate row or a 409, so a
    # double-tap or a retried request cannot inflate the dossier.
    existing = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == current_user.id,
            Bookmark.event_id == event_uuid,
        )
    )
    found = existing.scalars().first()
    if found is not None:
        return {
            "id": str(found.id),
            "event_id": str(event_uuid),
            "is_bookmarked": True,
            "created": False,
        }

    bookmark = Bookmark(user_id=current_user.id, event_id=event_uuid)
    db.add(bookmark)
    try:
        await db.commit()
    except IntegrityError:
        # Lost a race against a concurrent save; the other row is equally valid.
        await db.rollback()
        return {
            "id": None,
            "event_id": str(event_uuid),
            "is_bookmarked": True,
            "created": False,
        }

    await db.refresh(bookmark)
    return {
        "id": str(bookmark.id),
        "event_id": str(event_uuid),
        "is_bookmarked": True,
        "created": True,
    }


@router.delete("/events/{event_id}", summary="Remove an event from the caller's dossier")
async def delete_bookmark(
    event_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event_uuid = _parse_uuid(event_id, "event")

    result = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == current_user.id,
            Bookmark.event_id == event_uuid,
        )
    )
    bookmark = result.scalars().first()

    # Deleting something that is not saved is a success, not an error, so an
    # unsave toggle is safe to retry.
    if bookmark is None:
        return {"event_id": str(event_uuid), "is_bookmarked": False, "deleted": False}

    await db.delete(bookmark)
    await db.commit()

    return {"event_id": str(event_uuid), "is_bookmarked": False, "deleted": True}
