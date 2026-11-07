"""
GlobeLens AI — Re-synthesize CLUSTERED orphan articles
=======================================================
Articles that had their events deleted (at 0,0) are now in CLUSTERED state
with event_id = NULL. This script groups them into individual clusters
(one event per article, since each was its own cluster) and runs LLM
synthesis on the first LIMIT of them.

Run: docker exec globelens_backend python -m src.data.resynth_clustered
"""

import asyncio
import uuid
import structlog
from sqlalchemy import select, text

from src.core.database import AsyncSessionFactory
from src.data.entities.models import Event, Article, BiasLean as BiasLeanEnum
from src.services.llm_service import LLMService

logger = structlog.get_logger()

LIMIT = 5          # How many clusters to synthesize this run
SLEEP_SEC = 6.0    # Seconds between API calls

BIAS_MAP = {
    "LEFT": BiasLeanEnum.LEFT,
    "CENTER_LEFT": BiasLeanEnum.CENTER_LEFT,
    "CENTER": BiasLeanEnum.CENTER,
    "CENTER_RIGHT": BiasLeanEnum.CENTER_RIGHT,
    "RIGHT": BiasLeanEnum.RIGHT,
}


async def main():
    logger.info("=== Re-synthesize CLUSTERED orphan articles: START ===", limit=LIMIT)

    # Fetch up to LIMIT articles that are CLUSTERED and have no event_id
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Article)
            .where(
                Article.processing_status == "CLUSTERED",
                Article.event_id.is_(None)
            )
            .limit(LIMIT)
        )
        articles_to_process = list(result.scalars().all())

    if not articles_to_process:
        logger.info("No CLUSTERED orphan articles found. Nothing to do.")
        return

    logger.info(f"Found {len(articles_to_process)} articles to synthesize")

    llm_service = LLMService()
    ok = 0
    err = 0

    for idx, article in enumerate(articles_to_process):
        logger.info(
            f"[{idx+1}/{len(articles_to_process)}] Processing article",
            article_id=str(article.id),
            title=(article.title or "N/A")[:80]
        )

        content = f"Title: {article.title}\nContent: {article.content}" if article.content else f"Title: {article.title}"

        try:
            intelligence = await llm_service.analyze_event_cluster([content])

            logger.info(
                "LLM synthesis succeeded",
                topic=intelligence.topic.value,
                country=intelligence.location_country,
                lat=intelligence.latitude,
                lon=intelligence.longitude,
                score=intelligence.importance_score,
            )

            bias_val = BIAS_MAP.get(intelligence.bias_lean.value, BiasLeanEnum.CENTER)

            new_event = Event(
                id=uuid.uuid4(),
                title=article.title or "Synthesized Event",
                summary=intelligence.summary,
                topic=intelligence.topic.value,
                bias_lean=bias_val,
                country=intelligence.location_country,
                latitude=intelligence.latitude,
                longitude=intelligence.longitude,
                importance_score=intelligence.importance_score,
                status="PROCESSED",
                citation_sources=[str(article.id)],
            )

            async with AsyncSessionFactory() as session:
                session.add(new_event)
                await session.flush()
                # Link article to the new event + mark PROCESSED
                await session.execute(
                    text(
                        f"UPDATE articles "
                        f"SET event_id = '{new_event.id}', processing_status = 'PROCESSED' "
                        f"WHERE id = '{article.id}'"
                    )
                )
                await session.commit()

            logger.info("✓ Event created and article linked", new_event_id=str(new_event.id))

            # Index in Elasticsearch
            try:
                from src.data.repositories.search_repository import SearchRepository
                from src.core.config import settings
                search_repo = SearchRepository()
                index_name = settings.ELASTICSEARCH_INDEX_EVENTS
                doc = {
                    "title": new_event.title,
                    "summary": new_event.summary,
                    "topic": new_event.topic,
                    "location_country": new_event.country,
                    "latitude": new_event.latitude,
                    "longitude": new_event.longitude,
                    "importance_score": new_event.importance_score,
                }
                await search_repo._client.index(
                    index=index_name,
                    id=str(new_event.id),
                    document=doc
                )
                logger.info("✓ Indexed in Elasticsearch", event_id=str(new_event.id))
            except Exception as es_err:
                logger.warn("ES indexing failed (non-fatal)", error=str(es_err))

            ok += 1

        except Exception as e:
            logger.error("Failed to synthesize article", article_id=str(article.id), error=str(e))
            err += 1

        if idx < len(articles_to_process) - 1:
            logger.info(f"  Sleeping {SLEEP_SEC}s before next call...")
            await asyncio.sleep(SLEEP_SEC)

    # Final check
    async with AsyncSessionFactory() as session:
        remaining_result = await session.execute(
            text("SELECT COUNT(*) FROM articles WHERE processing_status = 'CLUSTERED' AND event_id IS NULL")
        )
        remaining = remaining_result.scalar()

    logger.info("=== Re-synthesize CLUSTERED orphan articles: DONE ===")
    logger.info(f"  Synthesized (OK):  {ok}")
    logger.info(f"  Synthesized (ERR): {err}")
    logger.info(f"  Still CLUSTERED:   {remaining} articles remaining")


if __name__ == "__main__":
    asyncio.run(main())
