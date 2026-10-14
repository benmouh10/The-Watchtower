"""
GlobeLens AI — Celery tasks.

Tasks are synchronous entry points (Celery's model) that drive async services by
creating their own database session. They must not reuse a request-scoped
session: a worker process has no FastAPI request.
"""
import asyncio
import logging

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.services.illustration_service import IllustrationService
from app.services.llm_service import LLMService
from app.services.newsletter_service import send_digest
from app.services.push_service import send_daily_briefings
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# Guards against a second enrichment pass running while the first is still
# going; see LLM_ENRICH_LOCK_TTL_SECONDS.
_ENRICH_LOCK_KEY = "globelens:llm_enrich:lock"

# Same guard for illustration batches, which share the local LLM with
# enrichment for prompt composition.
_ILLUSTRATION_LOCK_KEY = "globelens:illustration:lock"


@celery_app.task(name="app.worker.tasks.send_digest_task")
def send_digest_task(frequency: str = "daily") -> dict:
    """Send the digest for a cadence; scheduled by beat, also runnable ad hoc."""

    async def _run() -> dict:
        async with AsyncSessionFactory() as db:
            return await send_digest(db, frequency)

    result = asyncio.run(_run())
    logger.info("Digest task finished: %s", result)
    return result


@celery_app.task(name="app.worker.tasks.send_push_briefings_task")
def send_push_briefings_task() -> dict:
    """Send the daily Web Push briefing to opted-in users (scheduled by beat)."""

    async def _run() -> dict:
        async with AsyncSessionFactory() as db:
            return await send_daily_briefings(db)

    result = asyncio.run(_run())
    logger.info("Push briefing task finished: %s", result)
    return result


@celery_app.task(name="app.worker.tasks.enrich_pending_events_task")
def enrich_pending_events_task() -> dict:
    """Score and summarise clustered events that are still DRAFT placeholders.

    Clustering creates an event per unmatched cluster with importance_score=0.0
    and status DRAFT, and nothing else in the system promotes it: the API filters
    on min_score, so an unenriched event is invisible. Beat calls this on an
    interval so the placeholder is replaced shortly after the article lands.

    Loops while progress is being made, because a single pass is capped at
    LLM_ENRICH_BATCH_SIZE and a scraper burst leaves far more than that queued.
    It stops on the first pass that scores nothing: those rows are unprocessable
    (no article content), and retrying them would spin until MAX_BATCHES.

    Takes a Redis lock for the duration. Beat is interval-based and one pass can
    outlast the interval on a CPU-served model, so without the guard every tick
    would add a concurrent run; they would then contend for the same inference
    endpoint and each take longer than the last.
    """
    if not settings.LLM_ENRICH_ENABLED:
        logger.info("Event enrichment task skipped: LLM_ENRICH_ENABLED is false")
        return {"processed": 0, "errors": 0, "batches": 0, "skipped": "disabled"}

    async def _run() -> dict:
        lock = aioredis.from_url(settings.REDIS_URL)
        try:
            acquired = await lock.set(
                _ENRICH_LOCK_KEY, "1", nx=True, ex=settings.LLM_ENRICH_LOCK_TTL_SECONDS
            )
        finally:
            await lock.aclose()

        if not acquired:
            logger.info(
                "Event enrichment task skipped: a run is already in progress"
            )
            return {
                "processed": 0,
                "errors": 0,
                "batches": 0,
                "skipped": "already_running",
            }

        try:
            service = LLMService()
            total_processed = 0
            total_errors = 0
            batches = 0
            max_batches = max(1, settings.LLM_ENRICH_MAX_BATCHES)

            for _ in range(max_batches):
                processed, errors = await service.process_pending_events(
                    limit=settings.LLM_ENRICH_BATCH_SIZE
                )
                batches += 1
                total_processed += processed
                total_errors += errors

                if processed == 0 and errors == 0:
                    break
                if batches < max_batches:
                    await asyncio.sleep(settings.LLM_ENRICH_SLEEP_SECONDS)

            return {
                "processed": total_processed,
                "errors": total_errors,
                "batches": batches,
            }
        finally:
            releaser = aioredis.from_url(settings.REDIS_URL)
            try:
                await releaser.delete(_ENRICH_LOCK_KEY)
            finally:
                await releaser.aclose()

    result = asyncio.run(_run())
    logger.info("Event enrichment task finished: %s", result)
    return result


@celery_app.task(name="app.worker.tasks.generate_illustrations_task")
def generate_illustrations_task(
    limit: int | None = None, max_batches: int | None = None
) -> dict:
    """Generate artwork for events that do not have any yet.

    Lives on the worker rather than in a request because one image takes seconds
    and a batch takes minutes; the API answers 202 straight away. The service
    manages its own session and never raises, so a provider outage shows up as a
    tally of failures instead of a task that retries forever.

    Loops until the backlog is empty rather than doing one batch per task. The
    worker runs one process per core, so a backfill enqueued as many tasks would
    race them all against the shared lock and eleven would no-op while one did
    all the work; looping inside the task serialises the same way but actually
    drains the queue.

    Takes that Redis lock for the duration, because prompt composition goes to
    the CPU-bound local LLM that event enrichment also uses.

    Stops on the first batch that generates nothing: those events are either all
    failing or all below the importance threshold, and retrying cannot help.
    """
    batch_limit = limit
    ceiling = max_batches if max_batches is not None else settings.ILLUSTRATION_MAX_BATCHES

    async def _run() -> dict:
        lock = aioredis.from_url(settings.REDIS_URL)
        try:
            acquired = await lock.set(
                _ILLUSTRATION_LOCK_KEY,
                "1",
                nx=True,
                ex=settings.ILLUSTRATION_LOCK_TTL_SECONDS,
            )
        finally:
            await lock.aclose()

        if not acquired:
            logger.info("Illustration task skipped: a batch is already running")
            return {
                "generated": 0,
                "failed": 0,
                "skipped": 0,
                "batches": 0,
                "already_running": True,
            }

        try:
            service = IllustrationService()
            totals = {"generated": 0, "failed": 0, "skipped": 0}
            batches = 0

            for _ in range(max(1, ceiling)):
                result = await service.process_batch(batch_limit)
                batches += 1
                for key in totals:
                    totals[key] += result.get(key, 0)

                if result.get("generated", 0) == 0:
                    break
                if batches < max(1, ceiling):
                    await asyncio.sleep(settings.ILLUSTRATION_SLEEP_SECONDS)

            totals["batches"] = batches
            return totals
        finally:
            releaser = aioredis.from_url(settings.REDIS_URL)
            try:
                await releaser.delete(_ILLUSTRATION_LOCK_KEY)
            finally:
                await releaser.aclose()

    result = asyncio.run(_run())
    logger.info("Illustration task finished: %s", result)
    return result
