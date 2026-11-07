"""
GlobeLens AI — Async Elasticsearch Client & Connection Initialization
====================================================================
Establishes the connection to Elasticsearch and handles startup retries.
"""
import asyncio
import structlog
from elasticsearch import AsyncElasticsearch

from src.core.config import settings

logger = structlog.get_logger()

# Global asynchronous Elasticsearch client instance
es_client = AsyncElasticsearch(hosts=[settings.ELASTICSEARCH_URL])


async def init_elasticsearch(max_retries: int = 5, retry_delay: float = 5.0) -> None:
    """
    Verifies reachability of the Elasticsearch container.
    Retries connection gracefully on startup latency.
    """
    logger.info("Initializing Elasticsearch connection", url=settings.ELASTICSEARCH_URL)
    
    for attempt in range(1, max_retries + 1):
        try:
            ping_ok = await es_client.ping()
            if ping_ok:
                logger.info("Successfully connected to Elasticsearch")
                return
            else:
                logger.warn(
                    "Elasticsearch ping returned False",
                    attempt=attempt,
                    max_retries=max_retries
                )
        except Exception as exc:
            logger.warn(
                "Elasticsearch connection attempt failed",
                attempt=attempt,
                max_retries=max_retries,
                error=str(exc)
            )
            
        if attempt < max_retries:
            logger.info("Waiting to retry Elasticsearch connection...", delay=retry_delay)
            await asyncio.sleep(retry_delay)
            
    logger.error(
        "Could not establish connection to Elasticsearch after maximum retries",
        max_retries=max_retries
    )
    # Note: We do not raise an exception here to allow the FastAPI application
    # to start up in a degraded mode rather than crashing entirely.
