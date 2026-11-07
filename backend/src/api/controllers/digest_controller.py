"""Public reads for scheduled comprehensive briefs ("digests").

Digest rows are read-only artifacts produced by the scheduled engine or an
admin-triggered manual run. These routes only surface them; nothing here
generates or mutates a digest.
"""
from typing import Optional

from fastapi import APIRouter, Path, Query
from fastapi import HTTPException, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.api.controllers.auth_controller import AuthIdentity
from src.api.controllers.public_read_guard import require_public_read
from src.services.api_key_service import KEY_SCOPE_READ_EVENTS
from src.data.entities.models import Digest
from fastapi import Depends

router = APIRouter()

MAX_LIST_LIMIT = 100


def _serialize_digest(digest: Digest, *, with_html: bool = False) -> dict:
    data = {
        "id": str(digest.id),
        "title": digest.title,
        "range_start": digest.range_start.isoformat() if digest.range_start else None,
        "range_end": digest.range_end.isoformat() if digest.range_end else None,
        "event_ids": digest.event_ids or [],
        "event_count": digest.event_count,
        "created_at": digest.created_at.isoformat() if digest.created_at else None,
    }
    if with_html:
        data["content_html"] = digest.content_html
    return data


async def _load_digest(db: AsyncSession, digest_id: str) -> Digest:
    import uuid as _uuid

    try:
        digest_uuid = _uuid.UUID(str(digest_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Digest not found")
    result = await db.execute(select(Digest).where(Digest.id == digest_uuid))
    digest = result.scalars().first()
    if digest is None:
        raise HTTPException(status_code=404, detail="Digest not found")
    return digest


@router.get("", summary="Recent comprehensive briefs (metadata only)")
async def list_digests(
    limit: int = Query(10, ge=1, le=MAX_LIST_LIMIT),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    result = await db.execute(
        select(Digest)
        .order_by(Digest.created_at.desc(), Digest.id.desc())
        .limit(limit)
    )
    digests = list(result.scalars())
    total = (await db.execute(select(func.count(Digest.id)))).scalar_one()
    return {
        "items": [_serialize_digest(d) for d in digests],
        "count": len(digests),
        "total": total,
        "limit": limit,
    }


@router.get("/latest", summary="The most recent comprehensive brief")
async def latest_digest(
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    result = await db.execute(
        select(Digest).order_by(Digest.created_at.desc(), Digest.id.desc()).limit(1)
    )
    digest = result.scalars().first()
    if digest is None:
        raise HTTPException(status_code=404, detail="No digest has been generated yet")
    return {"digest": _serialize_digest(digest, with_html=True)}


@router.get("/{digest_id}", summary="One comprehensive brief with its HTML")
async def get_digest(
    digest_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    digest = await _load_digest(db, digest_id)
    return {"digest": _serialize_digest(digest, with_html=True)}


@router.get("/{digest_id}/html", summary="A digest as a self-contained HTML document")
async def get_digest_html(
    digest_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    digest = await _load_digest(db, digest_id)
    return Response(
        content=digest.content_html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="globelens-digest-{digest.id}.html"'},
    )