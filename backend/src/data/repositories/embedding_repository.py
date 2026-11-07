"""
GlobeLens AI — EmbeddingRepository
Async SQLAlchemy repository for vector Embedding entity.
"""
import uuid
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from src.data.entities.models import Embedding, Article, ProcessingStatus


class EmbeddingRepository:
    """Data access layer for vector Embedding entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_embedding(
        self, article_id: uuid.UUID, vector: List[float], model_name: str
    ) -> Embedding:
        """
        Create and persist a vector embedding.
        Updates the parent Article's processing_status to EMBEDDED.
        Runs within a single atomic transaction.
        """
        # Create and add the Embedding entity
        embedding = Embedding(
            article_id=article_id,
            vector=vector,
            model=model_name
        )
        self._session.add(embedding)

        # Update parent Article status
        article = await self._session.get(Article, article_id)
        if not article:
            raise ValueError(f"Article with ID {article_id} not found")
        
        article.processing_status = ProcessingStatus.EMBEDDED

        await self._session.commit()
        await self._session.refresh(embedding)
        return embedding
