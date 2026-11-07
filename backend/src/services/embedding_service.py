"""
GlobeLens AI — EmbeddingService
Generates and stores vector embeddings for articles.
Pipeline Step 2: SCRAPED → EMBEDDED
"""
import json
import hashlib
from typing import List
import structlog

from src.core.database import AsyncSessionFactory
from src.data.repositories.article_repository import ArticleRepository
from src.data.repositories.embedding_repository import EmbeddingRepository
from src.services.cache_service import cache_service

logger = structlog.get_logger()


class EmbeddingService:
    """
    Produces vector representations of article text through the config-gated
    embedding adapter (see ai_providers). Stores vectors in PostgreSQL via
    pgvector for similarity-based clustering.
    """

    def __init__(self) -> None:
        # Embedding adapter chosen purely from settings.EMBEDDING_PROVIDER; the
        # adapter owns the client and per-provider transport (see ai_providers).
        from src.services.ai_providers import build_embedding_provider
        self._provider = build_embedding_provider()
        self._model = self._provider.model
        logger.info("Initializing EmbeddingService with provider", provider=self._provider.name)

    async def generate_vector(self, text: str) -> List[float]:
        """
        Call the embedding model API and return a float vector.
        Caches embeddings by content hash to avoid redundant API calls.
        """
        if not text or not text.strip():
            raise ValueError("Input text cannot be empty")

        # Normalize text and compute hash for caching
        text_bytes = text.encode("utf-8")
        cache_key = f"embedding:{hashlib.sha256(text_bytes).hexdigest()}"
        
        try:
            cached = await cache_service.get(cache_key)
            if cached:
                logger.debug("Embedding cache hit", cache_key=cache_key)
                return json.loads(cached)
        except Exception as cache_err:
            logger.warn("Cache lookup failed, proceeding to API call", error=str(cache_err))

        if not self._provider.is_configured():
            # Deliberately no mock vector.
            #
            # A hash-seeded random vector is unit-length and looks like a real
            # embedding to every downstream consumer, so it silently degrades
            # feature after feature: clustering groups articles by nothing, and
            # contradiction detection's paraphrase guard compares random
            # distances. Those outputs are not merely imprecise, they are
            # meaningless, and they are indistinguishable from real results.
            # Failing loudly keeps the article unembedded so it can be retried
            # once a real key is configured.
            logger.error(
                "Embedding API key is a placeholder; refusing to generate a fake vector",
                provider=self._provider.name,
            )
            raise RuntimeError(
                "No usable embedding API key is configured. Refusing to generate a "
                "deterministic mock vector, because a fabricated embedding silently "
                "corrupts clustering and contradiction detection."
            )

        logger.info("Calling embedding API", model=self._model, text_len=len(text))
        try:
            vector = await self._provider.embed(text)

            # Save to cache
            try:
                await cache_service.set(cache_key, json.dumps(vector), ttl_seconds=86400)
            except Exception as cache_save_err:
                logger.warn("Failed to cache embedding", error=str(cache_save_err))
                
            return vector
        except Exception as api_err:
            # A real provider call failed. Falling back to a random vector here
            # would write a plausible-looking row into the embeddings table that
            # nothing can distinguish from a genuine embedding, so the failure
            # is propagated instead. The caller rolls back and retries later.
            logger.error("Embedding API call failed; no vector produced", model=self._model, error=str(api_err))
            raise RuntimeError(
                f"Embedding API call failed ({self._model}). Refusing to substitute a "
                "mock vector, which would be stored as if it were a real embedding."
            ) from api_err

    async def generate_embedding(self, text: str) -> List[float]:
        """Alias for generate_vector to maintain backward compatibility."""
        return await self.generate_vector(text)

    async def process_scraped_batch(self, limit: int = 50) -> int:
        """
        Fetches unprocessed articles with status SCRAPED, generates their vector representations,
        and persists them. Returns the number of successfully processed articles.
        """
        logger.info("Starting embedding batch process", limit=limit)
        processed_count = 0

        async with AsyncSessionFactory() as session:
            repo = ArticleRepository(session)
            embed_repo = EmbeddingRepository(session)

            try:
                articles_db = await repo.get_unprocessed_articles(limit)
                # Project ORM objects to simple dictionaries immediately to prevent
                # MissingGreenlet / expired object access errors on loop iterations
                # after a rollback.
                articles = [
                    {
                        "id": art.id,
                        "title": art.title,
                        "content": art.content
                    }
                    for art in articles_db
                ]
                logger.info("Fetched unprocessed articles", count=len(articles))

                for article in articles:
                    art_id = article["id"]
                    art_title = article["title"]
                    art_content = article["content"]

                    if not art_content or not art_content.strip():
                        logger.warn("Article content empty, skipping embedding", article_id=str(art_id))
                        continue

                    try:
                        # Combine title and content for better contextual embedding representation
                        text_to_embed = f"{art_title}\n\n{art_content}"
                        vector = await self.generate_vector(text_to_embed)

                        await embed_repo.create_embedding(
                            article_id=art_id,
                            vector=vector,
                            model_name=self._model
                        )
                        logger.info("Article embedding processed and saved", article_id=str(art_id))
                        processed_count += 1
                    except Exception as article_err:
                        logger.error(
                            "Failed to process embedding for single article",
                            article_id=str(art_id),
                            error=str(article_err)
                        )
                        # Rollback the session for this failed article's transaction
                        await session.rollback()
                        continue

                logger.info("Embedding batch processing completed", processed_count=processed_count)
            except Exception as batch_err:
                logger.error("Embedding batch process encountered a fatal error", error=str(batch_err))
                await session.rollback()

        return processed_count
