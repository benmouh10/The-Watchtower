"""
GlobeLens AI — pytest fixture infrastructure.

Tests run against a dedicated PostgreSQL database (globelens_test) plus the
local Redis and Elasticsearch used by the live stack. The env below is set
before any `app.*` import so the cached settings singleton — and the async
engine it builds — target the test store.

Schema is created straight from Base.metadata (models are the source of
truth) rather than running Alembic, keeping tests fast and deterministic.
Tables are truncated between tests; Elasticsearch keeps a shared events
index because the model has no way to express "ES test index" per run.
"""
import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://globelens:globelens_secret@localhost:5432/globelens_test"
)
os.environ["REDIS_URL"] = "redis://:redis_secret@localhost:6379/0"
os.environ["ELASTICSEARCH_URL"] = "http://localhost:9200"
os.environ["SECRET_KEY"] = "local-dev-only-not-a-placeholder-1234567890abcdef"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"
os.environ["ALLOW_INSECURE_DEV_SECRET"] = "true"

# Workers and external channels stay off unless a test flips them explicitly.
os.environ["DIGEST_ENABLED"] = "false"
os.environ["EMAIL_ENABLED"] = "false"
os.environ["PUSH_ENABLED"] = "false"
os.environ["WEBHOOK_ENABLED"] = "false"
os.environ["INGEST_ENABLED"] = "false"
os.environ["REALTIME_ENABLED"] = "false"
os.environ["FACTCHECK_PROVIDER"] = ""

import secrets  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.core.database import engine  # noqa: E402
from src.data.entities.models import Base  # noqa: E402
from src.api.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _schema():
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        for ext in ("uuid-ossp", "vector", "pg_trgm", "pgcrypto"):
            await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
        await conn.run_sync(Base.metadata.create_all)
    from src.services.search_service import SearchService

    try:
        svc = SearchService()
        await svc.create_events_index()
        await svc.close()
    except Exception:
        pass
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_tables(_schema):
    yield
    async with engine.begin() as conn:
        names = [t.name for t in reversed(Base.metadata.sorted_tables)]
        if names:
            joined = ", ".join(names)
            await conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture(loop_scope="session")
async def client(_schema):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def auth(client):
    """Register + login a fresh user and return a token/identity bundle."""

    async def _make(role=None):
        email = f"u{uuid.uuid4().hex[:10]}@example.com"
        reg = await client.post(
            "/api/v1/auth/register",
            json={"name": "Test User", "email": email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "testpassword123"},
        )
        assert login.status_code == 200, login.text
        body = login.json()
        token = body["access_token"]
        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 200, me.text
        return {
            "email": email,
            "access_token": token,
            "refresh_token": body.get("refresh_token"),
            "user_id": me.json()["id"],
            "headers": {"Authorization": f"Bearer {token}"},
        }

    return _make


@pytest_asyncio.fixture(loop_scope="session")
async def admin(auth, client):
    """A user promoted to ADMIN directly in the database."""

    async def _make():
        identity = await auth()
        from sqlalchemy import select, update

        from src.core.database import AsyncSessionFactory
        from src.data.entities.models import User

        async with AsyncSessionFactory() as db:
            await db.execute(
                update(User).where(User.id == identity["user_id"]).values(role="ADMIN")
            )
            await db.commit()
        return identity

    return _make