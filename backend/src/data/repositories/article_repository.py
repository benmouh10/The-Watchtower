"""
GlobeLens AI — ArticleRepository
Async SQLAlchemy repository for Article entity persistence and retrieval.
"""
import uuid
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.data.entities.models import Article, ProcessingStatus


class ArticleRepository:
    """
    Data access layer for the Article entity.
    All methods accept an AsyncSession injected by FastAPI dependency injection.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, article: Article) -> Article:
        """Insert or update an Article record."""
        self._session.add(article)
        await self._session.commit()
        await self._session.refresh(article)
        return article

    async def find_by_id(self, article_id: uuid.UUID) -> Optional[Article]:
        """Fetch a single article by primary key."""
        return await self._session.get(Article, article_id)

    async def find_unprocessed(self, status: ProcessingStatus) -> List[Article]:
        """Return all articles at a given processing stage for pipeline workers."""
        result = await self._session.execute(
            select(Article).where(Article.processing_status == status)
        )
        return list(result.scalars().all())

    async def find_by_event(self, event_id: uuid.UUID) -> List[Article]:
        """
        Return all articles belonging to a given Event, in a deterministic order.

        Ordering is load-bearing: the summary's inline [N] citations are resolved
        against article position, so every read of an event's articles must yield
        the same sequence. Ties on created_at are broken by id (UUID) because
        two articles scraped in the same batch can share a timestamp.
        """
        result = await self._session.execute(
            select(Article)
            .where(Article.event_id == event_id)
            .order_by(Article.created_at.asc(), Article.id.asc())
        )
        return list(result.scalars().all())

    async def list_for_moderation(
        self,
        include_hidden: bool = True,
        limit: int = 100,
    ) -> List[Article]:
        """
        Return articles for the admin moderation queue, newest first.

        `source` and `event` are eagerly loaded because the moderation UI renders
        the publisher name and event title for every row; a lazy load there would
        raise MissingGreenlet inside the async serializer.
        """
        stmt = (
            select(Article)
            .options(joinedload(Article.source), joinedload(Article.event))
            .order_by(Article.created_at.desc(), Article.id.asc())
            .limit(limit)
        )
        if not include_hidden:
            stmt = stmt.where(Article.is_hidden.is_(False))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self, article_id: uuid.UUID, status: ProcessingStatus
    ) -> None:
        """Efficiently update only the processing_status column."""
        await self._session.execute(
            update(Article)
            .where(Article.id == article_id)
            .values(processing_status=status)
        )
        await self._session.commit()

    async def create_scraped_article(
        self, article_data: dict
    ) -> Optional[Article]:
        """
        Create and persist a scraped article.
        Checks for URL uniqueness; returns None if it's a duplicate.
        """
        # Idempotency check against URL
        result = await self._session.execute(
            select(Article).where(Article.url == article_data["url"])
        )
        existing = result.scalars().first()
        if existing:
            # Skip transaction safely without committing to prevent duplicates
            return None

        # Resolve published_at if present
        published_at = article_data.get("published_at")

        # Check source_id if present
        source_id = article_data.get("source_id")
        if isinstance(source_id, str):
            source_id = uuid.UUID(source_id)

        article = Article(
            title=article_data["title"],
            content=article_data.get("content"),
            url=article_data["url"],
            published_at=published_at,
            processing_status=ProcessingStatus.SCRAPED,
            source_id=source_id,
            is_hidden=False,
            image_url=article_data.get("image_url"),
            image_caption=article_data.get("image_caption"),
            video_url=article_data.get("video_url"),
            media_type=article_data.get("media_type"),
        )

        self._session.add(article)
        await self._session.commit()
        await self._session.refresh(article)
        return article

    async def get_unprocessed_articles(self, limit: int = 50) -> List[Article]:
        """Fetch articles with status SCRAPED up to a specified limit."""
        result = await self._session.execute(
            select(Article)
            .where(Article.processing_status == ProcessingStatus.SCRAPED)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_embedded_unclustered_articles(self, limit: int = 100) -> List[Article]:
        """Fetch articles with status EMBEDDED, eagerly loading their embeddings, up to a specified limit."""
        result = await self._session.execute(
            select(Article)
            .options(joinedload(Article.embedding))
            .where(Article.processing_status == ProcessingStatus.EMBEDDED)
            .limit(limit)
        )
        return list(result.scalars().all())

