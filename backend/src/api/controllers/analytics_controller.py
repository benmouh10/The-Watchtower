"""
GlobeLens AI — AnalyticsController

    GET  /api/v1/analytics/me/views      user's recent event views
    GET  /api/v1/analytics/me/summary    user's reading-habit rollups
    GET  /api/v1/admin/analytics/views   global aggregate (ADMIN)

View recording lives on the event detail route (POST /events/{id}/view) in
the event controller so the client sends it from exactly the page being read.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import get_current_user
from src.core.database import get_db
from src.data.entities.models import User, UserRole
from src.services.analytics_service import AnalyticsService

router = APIRouter()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid ISO datetime: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@router.get("/me/views", summary="Your recent event views")
async def my_views(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    since: Optional[str] = Query(None, description="ISO datetime window start"),
    until: Optional[str] = Query(None, description="ISO datetime window end"),
    limit: int = Query(100, ge=1, le=500),
):
    items = await AnalyticsService(db).views_for_user(
        current_user.id,
        since=_parse_iso(since),
        until=_parse_iso(until),
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/me/summary", summary="Your reading-habit analytics summary")
async def my_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    since: Optional[str] = Query(None, description="ISO datetime window start"),
    until: Optional[str] = Query(None, description="ISO datetime window end"),
):
    return await AnalyticsService(db).summary_for_user(
        current_user.id,
        since=_parse_iso(since),
        until=_parse_iso(until),
    )


@router.get("/admin/views", summary="Global event-view analytics (ADMIN)")
async def global_views(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    since: Optional[str] = Query(None, description="ISO datetime window start"),
    until: Optional[str] = Query(None, description="ISO datetime window end"),
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can read global analytics",
        )
    return await AnalyticsService(db).global_summary(
        since=_parse_iso(since),
        until=_parse_iso(until),
    )