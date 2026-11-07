"""
GlobeLens AI — Event-view analytics (reading habits / coverage mix).

EventView rows record who viewed which event (and roughly for how long).
AnalyticsService turns those rows into per-user summaries — totals, daily view
series, topic mix and bias-lean mix — and an admin global aggregate. All
queries are read-only derivations; nothing is ever written back.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.entities.models import Event, EventView, User

logger = logging.getLogger("src.services.analytics_service")

VIEW_SINCE_DAYS = 30


class AnalyticsService:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def record_view(
        self,
        user_id,
        event_id,
        *,
        dwell_seconds: Optional[int] = None,
        seen_at: Optional[datetime] = None,
    ) -> EventView:
        """Append one view row. Dwell is client-reported and best-effort."""
        if dwell_seconds is not None and dwell_seconds < 0:
            dwell_seconds = None
        view = EventView(
            user_id=user_id,
            event_id=event_id,
            dwell_seconds=dwell_seconds,
        )
        if seen_at is not None:
            view.seen_at = seen_at
        self._db.add(view)
        await self._db.commit()
        await self._db.refresh(view)
        return view

    def _window(self, since: Optional[datetime], until: Optional[datetime]):
        now = datetime.now(timezone.utc)
        window_start = since or (now - timedelta(days=VIEW_SINCE_DAYS))
        window_end = until or now
        return window_start, window_end

    async def views_for_user(
        self,
        user_id,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        window_start, window_end = self._window(since, until)
        rows = (await self._db.execute(
            select(EventView, Event)
            .join(Event, EventView.event_id == Event.id)
            .where(
                EventView.user_id == user_id,
                EventView.seen_at >= window_start,
                EventView.seen_at <= window_end,
            )
            .order_by(EventView.seen_at.desc())
            .limit(limit)
        )).all()
        return [
            {
                "event_id": str(view.event_id),
                "title": event.title,
                "topic": event.topic or "WORLD",
                "bias_lean": event.bias_lean.name if event.bias_lean else None,
                "seen_at": view.seen_at.isoformat() if view.seen_at else None,
                "dwell_seconds": view.dwell_seconds,
            }
            for view, event in rows
        ]

    async def summary_for_user(
        self,
        user_id,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Per-user rollups: totals, daily series, topic mix, bias-lean mix."""
        window_start, window_end = self._window(since, until)
        rows = (await self._db.execute(
            select(EventView, Event)
            .join(Event, EventView.event_id == Event.id)
            .where(
                EventView.user_id == user_id,
                EventView.seen_at >= window_start,
                EventView.seen_at <= window_end,
            )
        )).all()
        total = len(rows)
        dwells = [r[0].dwell_seconds for r in rows if r[0].dwell_seconds is not None]
        topics: Dict[str, int] = {}
        bias: Dict[str, int] = {}
        days: Dict[str, int] = {}
        for view, event in rows:
            topic = event.topic or "WORLD"
            topics[topic] = topics.get(topic, 0) + 1
            lean = event.bias_lean.name if event.bias_lean else "unclassified"
            bias[lean] = bias.get(lean, 0) + 1
            day = view.seen_at.date().isoformat() if view.seen_at else None
            if day:
                days[day] = days.get(day, 0) + 1
        return {
            "window": {
                "since": window_start.isoformat(),
                "until": window_end.isoformat(),
            },
            "total_views": total,
            "avg_dwell_seconds": round(sum(dwells) / len(dwells), 1) if dwells else None,
            "by_topic": topics,
            "by_bias_lean": bias,
            "daily": [{"date": day, "views": count} for day, count in sorted(days.items())],
        }

    async def global_summary(
        self,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Admin aggregate across all users in a window."""
        window_start, window_end = self._window(since, until)
        total = await self._db.scalar(
            select(func.count(EventView.id)).where(
                EventView.seen_at >= window_start,
                EventView.seen_at <= window_end,
            )
        )
        by_topic = {
            topic: count
            for topic, count in (await self._db.execute(
                select(Event.topic, func.count(EventView.id))
                .join(Event, EventView.event_id == Event.id)
                .where(
                    EventView.seen_at >= window_start,
                    EventView.seen_at <= window_end,
                )
                .group_by(Event.topic)
            )).all()
        } if total else {}
        by_bias = {
            lean: count
            for lean, count in (await self._db.execute(
                select(Event.bias_lean, func.count(EventView.id))
                .join(Event, EventView.event_id == Event.id)
                .where(
                    EventView.seen_at >= window_start,
                    EventView.seen_at <= window_end,
                )
                .group_by(Event.bias_lean)
            )).all()
        } if total else {}
        active = await self._db.scalar(
            select(func.count(func.distinct(EventView.user_id))).where(
                EventView.seen_at >= window_start,
                EventView.seen_at <= window_end,
            )
        )
        return {
            "window": {
                "since": window_start.isoformat(),
                "until": window_end.isoformat(),
            },
            "total_views": int(total or 0),
            "active_readers": int(active or 0),
            "by_topic": by_topic,
            "by_bias_lean": {
                k.name if k else "unclassified": v for k, v in by_bias.items()
            },
        }