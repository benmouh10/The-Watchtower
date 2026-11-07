"""Cron-driven background loop for scheduled ingestion.

Started from the FastAPI lifespan only when INGEST_ENABLED is true. Reuses the
same five-field cron parser and alignment loop as the digest scheduler so the
two periodic engines share one understanding of "on schedule". Each fire runs
the full RSS-discovery + Playwright-hydration pipeline exactly once, and a bad
run is logged and swallowed so a transient source outage cannot kill the
schedule.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.core.config import settings
from src.services.digest_scheduler import (
    MINUTE, HOUR, DOM, MONTH, DOW,
    _parse_configured_cron,
    cron_matches,
)

logger = logging.getLogger("src.services.ingest_scheduler")


async def _run_ingestion() -> None:
    """Run one scraping pipeline pass against the configured source list and
    every registered active ingest feed."""
    from src.services.ingest_service import FeedIngestService
    from src.services.scraper_service import ScraperService
    from src.core.database import AsyncSessionFactory

    await ScraperService().run_pipeline()

    async with AsyncSessionFactory() as session:
        try:
            await FeedIngestService(session).run_all()
        finally:
            await session.close()


async def _ingest_loop(cron: str) -> None:
    """Tick on `cron`, firing the ingestion pipeline, until cancelled."""
    fields = _parse_configured_cron(cron)
    logger.info("Ingest scheduler enabled on %r", cron)
    fired_key = None
    while True:
        now = datetime.now(timezone.utc)
        next_aligned = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        await asyncio.sleep(max(1.0, (next_aligned - now).total_seconds()))

        moment = next_aligned
        key = moment.strftime("%Y-%m-%dT%H:%M")
        if key == fired_key or not cron_matches(fields, moment):
            fired_key = key
            continue

        fired_key = key
        logger.info("Ingest scheduler fire at %s", moment.isoformat())
        try:
            await _run_ingestion()
        except Exception as exc:
            logger.error("Ingest run failed", exc_info=exc)


async def run_ingest_scheduler() -> None:
    """Run the configured ingestion schedule until cancelled.

    Raises at startup on a malformed INGEST_CRON so the operator fixes it before
    the loop starts; afterwards every per-run failure is logged and swallowed so
    one bad run cannot kill the schedule.
    """
    cron = settings.INGEST_CRON or "0 */4 * * *"
    _parse_configured_cron(cron)  # fail loudly on a bad expression
    await _ingest_loop(cron)