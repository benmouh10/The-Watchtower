"""Scheduled comprehensive-brief ("digest") engine.

Selects the most important PROCESSED events inside a window, renders a single
self-contained HTML brief, and persists the finished document plus its coverage
facts in the `digests` table. Selection is deliberately naive-but-honest: plain
importance ranking inside an explicit window with a hard cap, never invented.
Delivery (email/push) is a later roadmap item; this service only produces the
artifact, and any reader can audit exactly which events a brief covered.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.config import settings
from src.data.entities.models import Article, Digest, Event
from src.services.brief_renderer import render_brief_html


class DigestService:
    """Generate and persist scheduled comprehensive briefs."""

    def __init__(self, db: AsyncSession):
        self._db = db
        raw_max = getattr(settings, "DIGEST_MAX_EVENTS", 10) or 10
        self._max_events = max(1, int(raw_max))
        raw_lookback = getattr(settings, "DIGEST_LOOKBACK_HOURS", 24) or 24
        self._default_lookback = timedelta(hours=max(1, int(raw_lookback)))
        raw_weekly_max = getattr(settings, "DIGEST_WEEKLY_MAX_EVENTS", 12) or 12
        self._weekly_max_events = max(1, int(raw_weekly_max))
        raw_weekly_lookback = getattr(settings, "DIGEST_WEEKLY_LOOKBACK_DAYS", 7) or 7
        self._weekly_lookback = timedelta(days=max(1, int(raw_weekly_lookback)))

    def _cap_for(self, frequency: str) -> int:
        return self._weekly_max_events if frequency == "weekly" else self._max_events

    def _lookback_for(self, frequency: str) -> timedelta:
        return self._weekly_lookback if frequency == "weekly" else self._default_lookback

    async def previous_run(self) -> Optional[Digest]:
        result = await self._db.execute(
            select(Digest).order_by(Digest.created_at.desc()).limit(1)
        )
        return result.scalars().first()

    async def generate_digest(
        self,
        *,
        anchor: Optional[datetime] = None,
        title: Optional[str] = None,
        frequency: str = "daily",
    ) -> Optional[Digest]:
        """Run one digest: rank PROCESSED events in the window, render, persist.

        `anchor` overrides "now" (used by tests for a deterministic window).
        `title` overrides the default auto-titled brief (used by the admin
        manual trigger to mark the run as manual). `frequency` is 'daily' (the
        scheduled comprehensive brief) or 'weekly' (the Sunday deep-dive, which
        widens the window and raises the cap). Returns the persisted row.
        """
        cap = self._cap_for(frequency)
        lookback = self._lookback_for(frequency)
        now = (anchor or datetime.now(timezone.utc)).astimezone(timezone.utc)

        previous = await self.previous_run()
        if previous is not None and previous.created_at is not None:
            window_start = previous.created_at.astimezone(timezone.utc)
        else:
            window_start = now - lookback

        # A second run moments after the first (manual double-trigger) would
        # derive start >= now and select nothing. Clamp to a sane window rather
        # than emitting a self-contradicting brief.
        if window_start >= now:
            window_start = now - lookback

        events = await self._select_window_events(window_start, now, cap)
        rendered_docs = [self._serialize_event(e) for e in events]

        generated_at = now.strftime("%Y-%m-%d %H:%M UTC")
        window_label = (
            f"{window_start.strftime('%Y-%m-%d %H:%M')} to "
            f"{now.strftime('%Y-%m-%d %H:%M')} UTC"
        )
        final_title = title or (
            f"Weekly Deep Dive - {now.strftime('%Y-%m-%d')}"
            if frequency == "weekly"
            else f"Comprehensive Brief - {generated_at}"
        )
        kind_label = (
            ("Manual weekly deep-dive" if title else "Weekly deep-dive")
            if frequency == "weekly"
            else ("Manual comprehensive brief" if title else "Scheduled comprehensive brief")
        )

        content_html = render_brief_html(
            title=final_title,
            kind_label=kind_label,
            generated_at=generated_at,
            events=rendered_docs,
            description=(
                f"Events processed from {window_label}; ranked by importance "
                f"and capped at {cap}."
            ),
            provenance=f"Compiled by the GlobeLens AI scheduled digest engine at {generated_at}.",
            empty_message=(
                "No processed events landed in this window. The next scheduled "
                "run will try again."
            ),
        )

        digest = Digest(
            title=final_title,
            range_start=window_start,
            range_end=now,
            event_ids=[str(e.id) for e in events],
            event_count=len(events),
            content_html=content_html,
            frequency=frequency,
        )
        self._db.add(digest)
        await self._db.commit()
        await self._db.refresh(digest)
        return digest

    async def _select_window_events(
        self,
        window_start: datetime,
        window_end: datetime,
        cap: Optional[int] = None,
    ) -> List[Event]:
        result = await self._db.execute(
            select(Event)
            .where(Event.status == "PROCESSED")
            .where(Event.created_at >= window_start, Event.created_at <= window_end)
            .order_by(Event.importance_score.desc().nullslast(), Event.created_at.desc())
            .limit(cap if cap is not None else self._max_events)
            .options(
                selectinload(Event.articles).selectinload(Article.source),
                selectinload(Event.claims),
            )
        )
        return list(result.scalars())

    @staticmethod
    def _serialize_event(event: Event) -> Dict[str, Any]:
        """Shape the renderer consumes; mirrors the reading-list serializer."""
        return {
            "title": event.title,
            "summary": event.summary,
            "topic": event.topic,
            "country": event.country,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "importance_score": event.importance_score,
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
                }
                for claim in (event.claims or [])
            ],
        }