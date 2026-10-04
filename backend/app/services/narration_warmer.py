"""
GlobeLens AI — NarrationWarmer
===============================
Local speech synthesis runs at roughly half real time on CPU, so the first press
of a play button on a cold article blocks for as long as the clip is long — thirty
seconds of silence while the listener waits for a ninety-second briefing. Chunks
cannot simply be synthesised in parallel: onnxruntime already runs several threads
per job, and threading Kokoro's espeak-backed `create()` measured only 1.2-1.5x for
a real risk of corrupted phonemes.

So the work is moved off the request path instead of made faster. A single
background task walks the most recent articles and events and synthesises any
narration that is not cached yet, one item at a time, pausing between items so a
sweep never starves live traffic. Pressing Listen then reads the cache.

Warm-ups are opportunistic: if a listener is mid-sentence the sweep skips that item
rather than queueing behind it, so pre-warming can never add to the wait of someone
who has actually pressed play.

Everything here is best-effort by design. A missing model, a Redis outage or a
failing query is logged and retried on the next sweep; it must never take the API
down or surface an error to a listener.
"""
import asyncio
import uuid

import structlog

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.entities.models import Article, Event, PublicationStatus
from app.services.cache_service import cache_service
from app.services.tts_service import (
    article_narration_text,
    event_narration_text,
    to_speech_text,
    tts_service,
)

logger = structlog.get_logger()


class NarrationWarmer:
    """Fills the narration cache ahead of the first play."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Spawn the background task. Safe to call more than once."""
        if not settings.TTS_PREWARM_ENABLED:
            logger.info("Narration pre-warming disabled")
            return
        if self._task is not None and not self._task.done():
            return
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run(), name="narration-warmer")
        logger.info(
            "Narration warmer started",
            interval=settings.TTS_PREWARM_INTERVAL_SECONDS,
            batch=settings.TTS_PREWARM_BATCH,
        )

    async def stop(self) -> None:
        """Cancel the task and wait for it to unwind."""
        task, self._task = self._task, None
        self._queue = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("Narration warmer stopped")

    def enqueue_article(self, article_id: uuid.UUID) -> None:
        """Ask for one article to be warmed as soon as the worker is free."""
        self._enqueue("article", article_id)

    def enqueue_event(self, event_id: uuid.UUID) -> None:
        """Ask for one event briefing to be warmed as soon as the worker is free."""
        self._enqueue("event", event_id)

    def _enqueue(self, kind: str, item_id: uuid.UUID) -> None:
        if self._queue is None:
            # Pre-warming is off or not started yet; the periodic sweep will pick it up.
            return
        try:
            self._queue.put_nowait((kind, item_id))
        except Exception as exc:  # pragma: no cover - queue is unbounded in practice
            logger.warn("Could not queue narration warm-up", kind=kind, error=str(exc))

    # ── Worker ───────────────────────────────────────────────────────────────
    async def _run(self) -> None:
        # Give boot, migrations and health checks the CPU before the first sweep.
        try:
            await asyncio.sleep(settings.TTS_PREWARM_STARTUP_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise

        # Sweep once as soon as the API is up, so a restart re-warms the newest
        # clips immediately rather than leaving them cold for a whole interval.
        await self._sweep()

        interval = max(30.0, float(settings.TTS_PREWARM_INTERVAL_SECONDS))
        while True:
            try:
                kind, item_id = await asyncio.wait_for(self._queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                # Idle for a whole interval: use the wake-up to catch what the
                # queue missed (a publish that landed while pre-warming was off, a
                # failed warm-up, content scraped since the last pass).
                await self._sweep()
                continue
            except asyncio.CancelledError:
                raise

            await self._warm_one(kind, item_id)
            await self._settle()

    async def _settle(self) -> None:
        """Yield the CPU between items so requests keep their share of the cores."""
        delay = max(0.0, float(settings.TTS_PREWARM_ITEM_DELAY_SECONDS))
        if delay:
            await asyncio.sleep(delay)

    async def _warm_one(self, kind: str, item_id: uuid.UUID) -> None:
        """Synthesise a single item's narration if it is not already cached."""
        if not tts_service.configured:
            return
        try:
            text = await self._load_text(kind, item_id)
        except Exception as exc:
            logger.warn("Narration warm-up lookup failed", kind=kind, id=str(item_id), error=str(exc))
            return

        if not text:
            return
        if len(text) > settings.TTS_PREWARM_MAX_CHARS:
            # Warming a very long piece would burn minutes of CPU for a clip most
            # listeners will not reach; the play button still narrates it on demand.
            logger.info(
                "Narration too long to pre-warm",
                kind=kind,
                id=str(item_id),
                chars=len(text),
                limit=settings.TTS_PREWARM_MAX_CHARS,
            )
            return
        if await self._is_cached(text):
            logger.debug("Narration already warm", kind=kind, id=str(item_id))
            return

        await self._synthesise(kind, item_id, text)

    async def _synthesise(self, kind: str, item_id: uuid.UUID, text: str) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            warmed = await tts_service.warm_narration(text)
        except Exception as exc:
            # A provider or model failure is not fatal: the play button will report
            # it if the listener asks for this clip before the next sweep.
            logger.warn("Narration warm-up failed", kind=kind, id=str(item_id), error=str(exc))
            return
        if warmed is None:
            # A listener is already using the cores. Nothing to do here; the clip
            # will be voiced on demand, or caught by a later sweep.
            return
        audio, media_type = warmed
        logger.info(
            "Narration warmed",
            kind=kind,
            id=str(item_id),
            bytes=len(audio),
            content_type=media_type,
            seconds=round(loop.time() - started, 1),
        )

    async def _is_cached(self, text: str) -> bool:
        try:
            return bool(await cache_service.get(tts_service.cache_key(text)))
        except Exception as exc:
            # Redis is down; synthesising anyway is harmless and still useful.
            logger.warn("Narration warm-up cache lookup failed", error=str(exc))
            return False

    async def _load_text(self, kind: str, item_id: uuid.UUID) -> str:
        """Build the same spoken text the matching audio route would serve."""
        from sqlalchemy import select

        async with AsyncSessionFactory() as db:
            if kind == "event":
                row = (
                    await db.execute(
                        select(Event.title, Event.summary).where(Event.id == item_id)
                    )
                ).first()
                if row is None:
                    return ""
                return to_speech_text(event_narration_text(row[0], row[1]), settings.TTS_MAX_CHARS)

            article = await db.scalar(
                select(Article).where(
                    Article.id == item_id,
                    Article.publication_status == PublicationStatus.PUBLISHED,
                    Article.is_hidden.is_(False),
                )
            )
            if article is None:
                return ""
            return to_speech_text(article_narration_text(article), settings.TTS_MAX_CHARS)

    async def _sweep(self) -> None:
        """Warm the newest few articles and events that the queue did not cover."""
        if not tts_service.configured:
            return
        batch = max(1, int(settings.TTS_PREWARM_BATCH))
        try:
            articles, events = await self._recent(batch)
        except Exception as exc:
            logger.warn("Narration sweep query failed", error=str(exc))
            return

        warmed = 0
        for kind, items in (("article", articles), ("event", events)):
            for item_id in items:
                await self._warm_one(kind, item_id)
                warmed += 1
                await self._settle()

        logger.info("Narration sweep complete", candidates=warmed)

    async def _recent(self, batch: int) -> tuple[list, list]:
        """Newest published articles and newest summarised events, skipping drafts."""
        from sqlalchemy import select

        async with AsyncSessionFactory() as db:
            article_ids = list(
                (
                    await db.scalars(
                        select(Article.id)
                        .where(
                            Article.publication_status == PublicationStatus.PUBLISHED,
                            Article.is_hidden.is_(False),
                            Article.content.is_not(None),
                        )
                        .order_by(Article.published_at.desc().nullslast(), Article.created_at.desc())
                        .limit(batch)
                    )
                ).all()
            )
            event_ids = list(
                (
                    await db.scalars(
                        select(Event.id)
                        .where(Event.summary.is_not(None))
                        .order_by(Event.created_at.desc())
                        .limit(batch)
                    )
                ).all()
            )
        return article_ids, event_ids


narration_warmer = NarrationWarmer()