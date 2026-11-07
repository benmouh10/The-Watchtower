"""
GlobeLens AI — Fix Zero-Coordinate Events
==========================================
1. Finds all events at (0.0, 0.0) — events that got no valid LLM synthesis
2. Collects their article clusters (grouped by event_id)
3. Deletes those events from PostgreSQL + Elasticsearch
4. Resets their articles to CLUSTERED status (event_id = NULL)
5. Re-runs LLM synthesis (Nvidia NIM) on the first 5 article clusters only
6. Leaves the remaining clusters as CLUSTERED (ready for a future run)

Run from the backend directory:
  python -m src.data.fix_zero_coords
"""

import asyncio
import uuid
import structlog
import os, sys

# Make sure the app can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import select, update, delete, and_
from src.core.database import AsyncSessionFactory
from src.data.entities.models import Event, Article, ProcessingStatus
from src.services.llm_service import LLMService
from src.services.search_service import SearchService

logger = structlog.get_logger()

LLM_LIMIT = 5          # Only re-synthesize this many clusters
SLEEP_BETWEEN = 6.0    # Seconds between API calls (rate limit protection)


async def main():
    logger.info("=== Fix Zero-Coordinate Events: START ===")

    # ── Step 1: Find events at (0,0) ─────────────────────────────────────────
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Event).where(
                and_(Event.latitude == 0.0, Event.longitude == 0.0)
            )
        )
        zero_events = list(result.scalars().all())

    if not zero_events:
        logger.info("No events found at (0,0). Nothing to do.")
        return

    logger.info(f"Found {len(zero_events)} events at (0.0, 0.0)", count=len(zero_events))
    for e in zero_events:
        logger.info(f"  → Event {e.id} | title: {e.title[:60] if e.title else 'N/A'}")

    zero_event_ids = [e.id for e in zero_events]

    # ── Step 2: Collect article clusters grouped by their current event_id ───
    # We record which articles belong to which cluster BEFORE deleting events
    async with AsyncSessionFactory() as session:
        art_result = await session.execute(
            select(Article).where(Article.event_id.in_(zero_event_ids))
        )
        all_articles = list(art_result.scalars().all())

    # Build a dict: original_event_id → [article_id, ...]
    cluster_map: dict[uuid.UUID, list[uuid.UUID]] = {}
    for art in all_articles:
        eid = art.event_id
        if eid not in cluster_map:
            cluster_map[eid] = []
        cluster_map[eid].append(art.id)

    logger.info(f"Total articles in zero-coord clusters: {len(all_articles)}")
    logger.info(f"Total article clusters: {len(cluster_map)}")

    # ── Step 3: Delete events from PostgreSQL + Elasticsearch ────────────────
    logger.info("Deleting zero-coord events from database and search index...")

    # Remove from Elasticsearch first (using direct client delete)
    try:
        from src.data.repositories.search_repository import SearchRepository
        from src.core.config import settings
        search_repo = SearchRepository()
        index_name = settings.ELASTICSEARCH_INDEX_EVENTS
        for eid in zero_event_ids:
            try:
                await search_repo._client.delete(index=index_name, id=str(eid), ignore=[404])
                logger.info("Deleted from Elasticsearch", event_id=str(eid))
            except Exception as es_err:
                logger.warn("ES delete failed (may not exist)", event_id=str(eid), error=str(es_err))
    except Exception as e:
        logger.warn("SearchService unavailable for ES cleanup", error=str(e))

    # Delete from PostgreSQL
    if not zero_event_ids:
        logger.info("No zero-coordinate events to delete.")
    else:
        async with AsyncSessionFactory() as session:
            # Bound parameters rather than interpolated SQL: this statement
            # deletes rows, and an empty IN () list is a syntax error in
            # Postgres rather than a no-op. ProcessingStatus.CLUSTERED is
            # passed as the enum member so the ORM coerces it correctly
            # without needing a raw text() cast.
            await session.execute(
                update(Article)
                .where(Article.event_id.in_(zero_event_ids))
                .values(event_id=None, processing_status=ProcessingStatus.CLUSTERED)
            )
            await session.execute(
                delete(Event).where(Event.id.in_(zero_event_ids))
            )
            await session.commit()

        logger.info(
            f"✓ Deleted {len(zero_event_ids)} events. Articles reset to CLUSTERED."
        )

    # ── Step 4: Re-run LLM on first LLM_LIMIT clusters ───────────────────────
    cluster_list = list(cluster_map.items())          # [(event_id, [art_ids...]), ...]
    to_process   = cluster_list[:LLM_LIMIT]           # Only first 5
    to_skip      = cluster_list[LLM_LIMIT:]           # The rest stay CLUSTERED

    logger.info(
        f"Will LLM-synthesize {len(to_process)} clusters, "
        f"leaving {len(to_skip)} in CLUSTERED state."
    )

    llm_service = LLMService()
    processed_ok = 0
    processed_err = 0

    for idx, (orig_event_id, art_ids) in enumerate(to_process):
        logger.info(f"[{idx+1}/{len(to_process)}] Synthesizing cluster (orig event_id={orig_event_id})")

        async with AsyncSessionFactory() as session:
            # Fetch article content
            art_result = await session.execute(
                select(Article)
                .where(Article.id.in_(art_ids))
                .order_by(Article.created_at.asc(), Article.id.asc())
            )
            articles = list(art_result.scalars().all())

        articles_content = []
        ordered_article_ids = []
        for a in articles:
            if a.content and a.content.strip():
                articles_content.append(f"Title: {a.title}\nContent: {a.content}")
                ordered_article_ids.append(str(a.id))

        if not articles_content:
            logger.warn("No content found for cluster, skipping", orig_event_id=str(orig_event_id))
            processed_err += 1
            continue

        try:
            # Call Nvidia NIM via LLMService
            intelligence = await llm_service.analyze_event_cluster(articles_content)

            logger.info(
                "LLM synthesis succeeded",
                topic=intelligence.topic,
                country=intelligence.location_country,
                lat=intelligence.latitude,
                lon=intelligence.longitude,
                score=intelligence.importance_score,
            )

            # Create a new Event record — use correct model field names
            from src.data.entities.models import BiasLean as BiasLeanEnum
            # Map the LLM bias string to the enum member
            bias_map = {
                "LEFT": BiasLeanEnum.LEFT,
                "CENTER_LEFT": BiasLeanEnum.CENTER_LEFT,
                "CENTER": BiasLeanEnum.CENTER,
                "CENTER_RIGHT": BiasLeanEnum.CENTER_RIGHT,
                "RIGHT": BiasLeanEnum.RIGHT,
            }
            bias_val = bias_map.get(intelligence.bias_lean.value, BiasLeanEnum.CENTER)

            new_event = Event(
                id=uuid.uuid4(),
                title=articles[0].title if articles else "Synthesized Event",
                summary=intelligence.summary,
                topic=intelligence.topic.value,
                bias_lean=bias_val,
                country=intelligence.location_country,     # Event model uses 'country'
                latitude=intelligence.latitude,
                longitude=intelligence.longitude,
                importance_score=intelligence.importance_score,
                status="PROCESSED",                        # Event.status is a plain String
                citation_sources=ordered_article_ids,
            )

            async with AsyncSessionFactory() as session:
                session.add(new_event)
                await session.flush()   # get new_event.id generated
                # Link articles to new event + mark PROCESSED
                if art_ids:
                    await session.execute(
                        update(Article)
                        .where(Article.id.in_(art_ids))
                        .values(event_id=new_event.id, processing_status="PROCESSED")
                    )
                await session.commit()

            logger.info("✓ New event created and articles linked", new_event_id=str(new_event.id))

            # Index in Elasticsearch
            try:
                search_service = SearchService()
                event_data = {
                    "title": new_event.title,
                    "summary": new_event.summary,
                    "topic": new_event.topic,
                    "location_country": new_event.country,
                    "latitude": new_event.latitude,
                    "longitude": new_event.longitude,
                    "importance_score": new_event.importance_score,
                }
                await search_service.index_processed_event(new_event.id, event_data)
                logger.info("✓ Indexed in Elasticsearch", event_id=str(new_event.id))
            except Exception as es_err:
                logger.warn("ES indexing failed (non-fatal)", error=str(es_err))

            processed_ok += 1

        except Exception as err:
            logger.error(
                "LLM synthesis failed for cluster",
                orig_event_id=str(orig_event_id),
                error=str(err)
            )
            processed_err += 1

        # Rate limit protection
        if idx < len(to_process) - 1:
            logger.info(f"  Sleeping {SLEEP_BETWEEN}s before next call...")
            await asyncio.sleep(SLEEP_BETWEEN)

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("=== Fix Zero-Coordinate Events: DONE ===")
    logger.info(f"  Deleted events:         {len(zero_event_ids)}")
    logger.info(f"  Articles reset:         {len(all_articles)}")
    logger.info(f"  LLM-synthesized (OK):   {processed_ok}")
    logger.info(f"  LLM-synthesized (ERR):  {processed_err}")
    logger.info(f"  Left as CLUSTERED:      {len(to_skip)} clusters")


if __name__ == "__main__":
    asyncio.run(main())
