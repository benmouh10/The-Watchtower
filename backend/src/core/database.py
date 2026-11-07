"""
GlobeLens AI — Async Database Engine & Session Factory
=======================================================
Uses SQLAlchemy 2.x async engine backed by asyncpg (PostgreSQL).

Key design decisions:
- Async engine: matches the async-first FastAPI + uvicorn model. asyncpg is
  dramatically faster than psycopg2 for high-concurrency workloads.
- Sync engine (for Alembic): Alembic's migration runner is synchronous, so we
  expose a separate sync URL (postgresql+psycopg2) used only in env.py.
- Session factory: AsyncSession with expire_on_commit=False prevents
  MissingGreenlet errors when accessing attributes after commit.
- get_db(): FastAPI dependency that yields a session and guarantees cleanup
  via try/finally regardless of exceptions inside the route.
"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import settings


# ─────────────────────────────────────────────────────────────────────────────
# Async Engine
# ─────────────────────────────────────────────────────────────────────────────
# DATABASE_URL uses the postgresql+asyncpg:// scheme (set in .env / config.py).
# pool_pre_ping=True: before each checkout, runs a lightweight "SELECT 1" to
#   discard stale connections (avoids "SSL connection has been closed" errors).
# pool_size / max_overflow: tune for your expected concurrency level.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_ENV == "development",   # SQL logging in dev only
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# ─────────────────────────────────────────────────────────────────────────────
# Async Session Factory
# ─────────────────────────────────────────────────────────────────────────────
# expire_on_commit=False: keeps ORM objects accessible after session.commit()
#   without needing an extra SELECT to refresh them. Critical for async code
#   where re-loading inside an event loop can cause greenlet errors.
AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Dependency — get_db()
# ─────────────────────────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an async SQLAlchemy session as a FastAPI dependency.

    Usage in a controller:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()

    The session is committed on success and rolled back on any exception.
    It is always closed at the end of the request, returning the connection
    to the pool.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
