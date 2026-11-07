"""
GlobeLens AI — Feed-ingestion runner over registered IngestFeed rows.

Fans out over active feeds (capped by INGEST_MAX_FEEDS_PER_RUN), pulls raw
items through the per-type adapter, persists each new URL exactly once, and
updates each feed's health row (last_fetched_at / last_error). One broken
feed never aborts the run.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select

from src.core.config import settings
from src.data.entities.models import Article, IngestFeed, ProcessingStatus

logger = logging.getLogger("src.services.ingest_service")


class FeedIngestService:
    def __init__(self, db):
        self._db = db

    async def active_feeds(self, limit: Optional[int] = None) -> List[IngestFeed]:
        stmt = (
            select(IngestFeed)
            .where(IngestFeed.is_active.is_(True))
            .order_by(IngestFeed.created_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def run_feed(self, feed: IngestFeed) -> int:
        """Fetch one feed adapter and insert its new items. Returns rows created."""
        from src.services.ingest_adapters import build_ingest_adapter

        limit = settings.INGEST_MAX_ITEMS_PER_FEED
        created = 0
        try:
            adapter = build_ingest_adapter(feed.feed_type, feed.url, limit)
            items = await adapter.fetch()
            for item in items:
                existing = (
                    await self._db.execute(select(Article).where(Article.url == item.url))
                ).scalars().first()
                if existing:
                    continue
                article = Article(
                    title=item.title,
                    content=item.summary,
                    url=item.url,
                    published_at=item.published_at,
                    processing_status=ProcessingStatus.SCRAPED,
                    source_id=None,
                    is_hidden=False,
                    image_url=item.image_url,
                    media_type="image" if item.image_url else None,
                )
                self._db.add(article)
                created += 1
            feed.last_fetched_at = datetime.now(timezone.utc)
            feed.last_error = None
        except Exception as exc:
            feed.last_error = str(exc)[:1000]
            feed.last_fetched_at = datetime.now(timezone.utc)
            logger.warning(
                "Feed ingest failed for %s (%s): %s",
                feed.name, feed.feed_type, feed.last_error,
            )
        await self._db.commit()
        return created

    async def run_all(self) -> dict:
        feeds = await self.active_feeds(settings.INGEST_MAX_FEEDS_PER_RUN)
        results = {"feeds": len(feeds), "items": 0, "errors": 0}
        for feed in feeds:
            results["items"] += await self.run_feed(feed)
            if feed.last_error:
                results["errors"] += 1
        return results