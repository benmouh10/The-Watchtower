"""
GlobeLens AI — ReadingListController
Named, ordered, private collections of events, with a print/PDF export.

Routes (router mounted at /api/v1/reading-lists):
    GET     /api/v1/reading-lists
    POST    /api/v1/reading-lists
    GET     /api/v1/reading-lists/{list_id}
    PATCH   /api/v1/reading-lists/{list_id}
    DELETE  /api/v1/reading-lists/{list_id}
    POST    /api/v1/reading-lists/{list_id}/events/{event_id}
    DELETE  /api/v1/reading-lists/{list_id}/events/{event_id}
    PUT     /api/v1/reading-lists/{list_id}/events/order
    GET     /api/v1/reading-lists/{list_id}/export
    GET     /api/v1/reading-lists/{list_id}/export.pdf

Everything is scoped to the authenticated user. There is no shared list: any
route that names a list only resolves it as
``where(ReadingList.id == list_id, ReadingList.user_id == current_user.id)``,
so a non-owner gets a 404 rather than either the data or proof of its
existence. Membership is ordered by ``position``; add appends at the tail,
remove compacts the gap, and the order endpoint assigns a fresh contiguous
0..n-1 in a two-phase write so the (list_id, position) uniqueness constraint
is never violated mid-transaction.
"""
import html
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.controllers.auth_controller import get_current_user, require_scopes
from src.core.database import get_db
from src.data.entities.models import Article, Event, ReadingList, ReadingListItem, Source, User
from src.services.api_key_service import KEY_SCOPE_READING_LISTS
from src.services.entitlement_service import (
    METER_EXPORT,
    UsageService,
    require_action_quota,
)
from src.services.pdf_service import PdfRenderError, render_html_to_pdf

# The whole router is private per-user data reached via the web UI's JWT
# sessions (which pass scope gates) or via a machine key minted with the
# `reading-lists` scope.
router = APIRouter(dependencies=[Depends(require_scopes(KEY_SCOPE_READING_LISTS))])

MAX_LISTS_PER_USER = 200
MAX_ITEMS_PER_LIST = 1000


class CreateReadingListRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    is_private: bool = False


class UpdateReadingListRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    is_private: Optional[bool] = None


class ReorderEventsRequest(BaseModel):
    event_ids: List[str] = Field(..., min_length=1)


def _parse_uuid(raw: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} UUID format",
        )


def _is_uuid(raw: str) -> bool:
    try:
        uuid.UUID(raw)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def _load_owned_list(
    db: AsyncSession,
    list_id: Any,
    current_user: User,
    *load: Any,
) -> ReadingList:
    """Load the caller's list, or 404 (never a 403: the list is invisible)."""
    stmt = select(ReadingList).where(
        ReadingList.id == list_id,
        ReadingList.user_id == current_user.id,
    )
    if load:
        stmt = stmt.options(*load)
    found = (await db.execute(stmt)).scalars().first()
    if found is None:
        raise HTTPException(status_code=404, detail="Reading list not found")
    return found


def _serialize_list(reading_list: ReadingList, item_count: int = 0) -> Dict[str, Any]:
    return {
        "id": str(reading_list.id),
        "title": reading_list.title,
        "description": reading_list.description,
        "is_private": reading_list.is_private,
        "item_count": item_count,
        "created_at": reading_list.created_at.isoformat() if reading_list.created_at else None,
        "updated_at": reading_list.updated_at.isoformat() if reading_list.updated_at else None,
    }


def _serialize_event(event: Optional[Event]) -> Optional[Dict[str, Any]]:
    """Enough event context to render a list row and the export block."""
    if event is None:
        return None
    article_count = len(event.articles) if event.articles is not None else 0
    return {
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
        "claims": [
            {
                "ordinal": claim.ordinal,
                "text": claim.text,
                "confidence": claim.confidence,
                "status": claim.status.name if claim.status else None,
                "reviewed_by": claim.reviewed_by,
            }
            for claim in (event.claims or [])
        ] if event.claims is not None else [],
    }


def _serialize_item(item: ReadingListItem) -> Dict[str, Any]:
    return {
        "list_item_id": str(item.id),
        "position": item.position,
        "added_at": item.created_at.isoformat() if item.created_at else None,
        "event": _serialize_event(item.event),
    }


async def _count_items(db: AsyncSession, list_ids: List[Any]) -> Dict[Any, int]:
    rows = (await db.execute(
        select(ReadingListItem.list_id, func.count())
        .where(ReadingListItem.list_id.in_(list_ids))
        .group_by(ReadingListItem.list_id)
    )).all()
    return {list_id: count for list_id, count in rows}


@router.get("", summary="List the caller's reading lists")
async def list_reading_lists(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    limit = max(1, min(limit, MAX_LISTS_PER_USER))
    result = await db.execute(
        select(ReadingList)
        .where(ReadingList.user_id == current_user.id)
        .order_by(ReadingList.updated_at.desc(), ReadingList.id.asc())
        .limit(limit)
    )
    reading_lists = list(result.scalars())
    counts = await _count_items(db, [rl.id for rl in reading_lists])

    return {
        "items": [
            {**_serialize_list(rl), "item_count": counts.get(rl.id, 0)}
            for rl in reading_lists
        ],
        "count": len(reading_lists),
        "limit": limit,
    }


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create one of the caller's reading lists")
async def create_reading_list(
    payload: CreateReadingListRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="A list title is required")

    existing_count = (await db.execute(
        select(func.count()).where(ReadingList.user_id == current_user.id)
    )).scalar_one()
    if existing_count >= MAX_LISTS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"You already have {MAX_LISTS_PER_USER} reading lists",
        )

    reading_list = ReadingList(
        user_id=current_user.id,
        title=title,
        description=(payload.description or "").strip() or None,
        is_private=payload.is_private,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(reading_list)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"You already have a list named '{title}'",
        )

    await db.refresh(reading_list)
    return {**_serialize_list(reading_list), "item_count": 0}


@router.get("/{list_id}", summary="One reading list with its ordered events")
async def get_reading_list(
    list_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    list_uuid = _parse_uuid(list_id, "list")
    reading_list = await _load_owned_list(
        db, list_uuid, current_user,
        selectinload(ReadingList.items)
        .selectinload(ReadingListItem.event)
        .selectinload(Event.articles)
        .selectinload(Article.source),
        selectinload(ReadingList.items)
        .selectinload(ReadingListItem.event)
        .selectinload(Event.claims),
    )

    items = sorted(reading_list.items, key=lambda i: i.position)
    return {
        **_serialize_list(reading_list, item_count=len(items)),
        "items": [_serialize_item(i) for i in items],
    }


@router.patch("/{list_id}", summary="Rename or edit one of the caller's reading lists")
async def update_reading_list(
    payload: UpdateReadingListRequest,
    list_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    list_uuid = _parse_uuid(list_id, "list")
    reading_list = await _load_owned_list(db, list_uuid, current_user)

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="A list title is required")
        reading_list.title = title
    if payload.description is not None:
        reading_list.description = payload.description.strip() or None
    if payload.is_private is not None:
        reading_list.is_private = payload.is_private
    reading_list.updated_at = datetime.now(timezone.utc)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"You already have a list named '{reading_list.title}'",
        )

    count = (await db.execute(
        select(func.count()).where(ReadingListItem.list_id == list_uuid)
    )).scalar_one()
    return {**_serialize_list(reading_list, item_count=count)}


@router.delete("/{list_id}", summary="Delete one of the caller's reading lists")
async def delete_reading_list(
    list_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    list_uuid = _parse_uuid(list_id, "list")
    reading_list = await _load_owned_list(db, list_uuid, current_user)

    await db.delete(reading_list)
    await db.commit()

    return {"id": str(list_uuid), "deleted": True}


@router.post(
    "/{list_id}/events/{event_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Append an event to a reading list (idempotent)",
)
async def add_event_to_list(
    list_id: str = Path(...),
    event_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    list_uuid = _parse_uuid(list_id, "list")
    event_uuid = _parse_uuid(event_id, "event")
    reading_list = await _load_owned_list(db, list_uuid, current_user)

    event_exists = await db.execute(select(Event.id).where(Event.id == event_uuid))
    if event_exists.first() is None:
        raise HTTPException(status_code=404, detail="Event not found")

    existing = await db.execute(
        select(ReadingListItem).where(
            ReadingListItem.list_id == list_uuid,
            ReadingListItem.event_id == event_uuid,
        )
    )
    already = existing.scalars().first()
    if already is not None:
        return {
            "list_id": str(list_uuid),
            "event_id": str(event_uuid),
            "position": already.position,
            "added": False,
        }

    item_count = (await db.execute(
        select(func.count()).where(ReadingListItem.list_id == list_uuid)
    )).scalar_one()
    if item_count >= MAX_ITEMS_PER_LIST:
        raise HTTPException(
            status_code=400,
            detail=f"This list already has {MAX_ITEMS_PER_LIST} events",
        )

    next_position = (await db.execute(
        select(func.coalesce(func.max(ReadingListItem.position), -1)).where(
            ReadingListItem.list_id == list_uuid
        )
    )).scalar_one() + 1

    item = ReadingListItem(
        list_id=list_uuid,
        event_id=event_uuid,
        position=next_position,
    )
    db.add(item)
    reading_list.updated_at = datetime.now(timezone.utc)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent duplicate add (same list + event); treat as already-there.
        await db.rollback()
        return {"list_id": str(list_uuid), "event_id": str(event_uuid), "position": None, "added": False}

    await db.refresh(item)
    return {
        "list_id": str(list_uuid),
        "event_id": str(event_uuid),
        "position": item.position,
        "added": True,
    }


@router.delete("/{list_id}/events/{event_id}", summary="Remove an event from a list and compact positions")
async def remove_event_from_list(
    list_id: str = Path(...),
    event_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    list_uuid = _parse_uuid(list_id, "list")
    event_uuid = _parse_uuid(event_id, "event")
    reading_list = await _load_owned_list(db, list_uuid, current_user)

    found = (await db.execute(
        select(ReadingListItem).where(
            ReadingListItem.list_id == list_uuid,
            ReadingListItem.event_id == event_uuid,
        )
    )).scalars().first()

    if found is None:
        return {
            "list_id": str(list_uuid),
            "event_id": str(event_uuid),
            "removed": False,
        }

    await db.delete(found)
    reading_list.updated_at = datetime.now(timezone.utc)
    await db.flush()

    # Compact: renumber the surviving items 0..n-1 so ordering stays gap-free.
    survivors = (await db.execute(
        select(ReadingListItem).where(
            ReadingListItem.list_id == list_uuid
        ).order_by(ReadingListItem.position)
    )).scalars().all()
    for index, item in enumerate(survivors):
        item.position = index

    await db.commit()

    return {
        "list_id": str(list_uuid),
        "event_id": str(event_uuid),
        "removed": True,
    }


@router.put("/{list_id}/events/order", summary="Reorder a list's events to the supplied order")
async def reorder_list_events(
    payload: ReorderEventsRequest,
    list_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    list_uuid = _parse_uuid(list_id, "list")
    reading_list = await _load_owned_list(db, list_uuid, current_user)

    items = list((await db.execute(
        select(ReadingListItem).where(
            ReadingListItem.list_id == list_uuid
        ).order_by(ReadingListItem.position)
    )).scalars())

    if len(items) != len(payload.event_ids):
        raise HTTPException(
            status_code=400,
            detail="The reorder payload must contain exactly the events in this list",
        )
    if any(not _is_uuid(eid) for eid in payload.event_ids):
        raise HTTPException(status_code=400, detail="Invalid event UUID in reorder payload")

    requested = [event_id.lower() for event_id in payload.event_ids]
    current = {str(item.event_id).lower() for item in items}
    if set(requested) != current:
        raise HTTPException(
            status_code=400,
            detail="The reorder payload must contain exactly the events in this list",
        )

    by_event = {str(item.event_id).lower(): item for item in items}

    # Two-phase write: shift every position out of 0..n-1 first, then assign
    # the final order. Doing this in one pass can step on a still-occupied slot
    # and trip the (list_id, position) unique constraint mid-transaction.
    shift = len(items)
    await db.execute(
        update(ReadingListItem)
        .where(ReadingListItem.list_id == list_uuid)
        .values(position=ReadingListItem.position + shift)
    )
    await db.flush()

    for index, event_id in enumerate(requested):
        by_event[event_id].position = index

    reading_list.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "list_id": str(list_uuid),
        "order": [{"event_id": eid, "position": index} for index, eid in enumerate(requested)],
    }


# ── Print / PDF export ────────────────────────────────────────────────────────
async def _export_surface(
    db: AsyncSession,
    current_user: User,
    list_uuid: uuid.UUID,
):
    """Load the caller's list, meter the export, and render the shared
    self-contained print HTML. Returns ``(reading_list, html_document)`` so
    the HTML and PDF routes bill once and reuse one layout."""
    reading_list = await _load_owned_list(
        db, list_uuid, current_user,
        selectinload(ReadingList.items)
        .selectinload(ReadingListItem.event)
        .selectinload(Event.articles)
        .selectinload(Article.source),
        selectinload(ReadingList.items)
        .selectinload(ReadingListItem.event)
        .selectinload(Event.claims),
    )

    items = sorted(reading_list.items, key=lambda i: i.position)
    events = [item.event for item in items]

    await UsageService(db).record(current_user.id, METER_EXPORT, meta={"event_count": len(events)})
    await db.commit()

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    document = _render_export_html(
        title=reading_list.title,
        description=reading_list.description,
        generated_at=generated_at,
        events=[_serialize_event(e) or {} for e in events],
    )
    return reading_list, document


@router.get("/{list_id}/export", summary="Print-friendly HTML used by the browser to 'Print / PDF' a list")
async def export_reading_list(
    list_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _quota_guard: None = Depends(require_action_quota(METER_EXPORT)),
):
    """
    Returns a self-contained HTML document (no external assets) styled for
    print. The reader opens it and uses the browser's Print → Save as PDF, so
    there is no client dependency on the server-side renderer. Export is scoped
    to the owner, like every other list route.
    """
    list_uuid = _parse_uuid(list_id, "list")
    reading_list, document = await _export_surface(db, current_user, list_uuid)
    return Response(
        content=document,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="globelens-reading-list-{reading_list.id}.html"'},
    )


@router.get(
    "/{list_id}/export.pdf",
    summary="The list as a server-rendered PDF document",
)
async def export_reading_list_pdf(
    list_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _quota_guard: None = Depends(require_action_quota(METER_EXPORT)),
):
    """
    Renders the same export through headless Chromium (Playwright) and returns
    the PDF bytes. The engine honors the brief's print CSS, so pagination and
    the honesty footer match the HTML surface. Returns 503 when the PDF
    renderer is unavailable so the caller can fall back to the HTML export.
    """
    list_uuid = _parse_uuid(list_id, "list")
    reading_list, document = await _export_surface(db, current_user, list_uuid)
    try:
        pdf = await render_html_to_pdf(document)
    except PdfRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="globelens-reading-list-{reading_list.id}.pdf"'},
    )


def _render_export_html(
    title: str,
    description: Optional[str],
    generated_at: str,
    events: List[Dict[str, Any]],
) -> str:
    """Self-contained print HTML for a reading list, shared with the digest
    engine via brief_renderer so both produce one consistent layout."""
    from src.services.brief_renderer import render_brief_html

    return render_brief_html(
        title=title,
        kind_label="GlobeLens AI reading list",
        generated_at=generated_at,
        events=events,
        description=description,
        provenance=f"Generated by GlobeLens AI at {generated_at} from the owner's private reading list.",
        empty_message=(
            "This list has no events yet. Add a few from any event page, "
            "then print again to get a dossier."
        ),
    )