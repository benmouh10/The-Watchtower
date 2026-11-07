"""Scheduled ingest feeds: adapters (RSS/Reddit/EDGAR/X) + admin CRUD.

The pipeline fans out over registered IngestFeed rows. Each adapter must
normalize an upstream payload into IngestItem records, X and EDGAR must refuse
loudly when their mandatory credential is missing, and the runner must persist
each new URL once while tracking per-feed health.
"""

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>Second Story</title>
    <link>https://news.example.org/second</link>
    <pubDate>Wed, 30 Sep 2026 09:00:00 GMT</pubDate>
    <description>A test story body.</description>
  </item>
  <item>
    <title>First Story</title>
    <link>https://news.example.org/first</link>
  </item>
</channel></rss>
"""

REDDIT_JSON = {
    "kind": "Listing",
    "data": {
        "children": [
            {
                "kind": "t3",
                "data": {
                    "title": "A subreddit story",
                    "permalink": "/r/worldnews/comments/abc/a_story/",
                    "created_utc": 1759212000,
                    "selftext": "body text",
                },
            }
        ]
    },
}

EDGAR_JSON = {
    "hits": {
        "hits": [
            {
                "_source": {
                    "title": "Acme Corp 8-K",
                    "entity_name": "ACME CORP",
                    "form_type": "8-K",
                    "report_url": "https://www.sec.gov/Archives/edgar/data/1/0000000001/0000000001-26-000001-index.htm",
                    "file_date": "2026-09-30",
                }
            }
        ]
    }
}

X_JSON = {
    "data": [
        {
            "id": "123456789",
            "text": "Breaking: market moves",
            "created_at": "2026-09-30T08:00:00.000Z",
            "author_id": "42",
        }
    ]
}


class FakeResponse:
    def __init__(self, payload, is_json=True):
        self._text = payload if not is_json else None
        self._json = payload if is_json else None

    @property
    def text(self):
        return self._text or ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._json or {}


class FakeAsyncClient:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None, **kwargs):
        self.calls.append({"url": url, "headers": headers, "kwargs": kwargs})
        return self._handler(url)

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs})
        return self._handler(url)


def _patch_http(monkeypatch, handler):
    import httpx

    fake = FakeAsyncClient(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
    return fake


# ── Adapters ──────────────────────────────────────────────────────────────────
async def test_rss_adapter_parses_items():
    from src.services.ingest_adapters import RssIngestAdapter

    adapter = RssIngestAdapter(
        "https://example.org/feed.xml", limit=25, client=FakeAsyncClient(
            lambda url: FakeResponse(RSS_XML, is_json=False)
        )
    )
    items = await adapter.fetch()
    assert len(items) == 2
    assert items[0].url == "https://news.example.org/second"
    assert items[0].published_at is not None
    assert items[0].summary == "A test story body."


async def test_reddit_adapter_normalizes_items(monkeypatch):
    from src.services.ingest_adapters import RedditIngestAdapter

    _patch_http(monkeypatch, lambda url: FakeResponse(REDDIT_JSON))
    adapter = RedditIngestAdapter("/r/worldnews/hot", limit=10)
    items = await adapter.fetch()
    assert len(items) == 1
    item = items[0]
    assert item.url == "https://www.reddit.com/r/worldnews/comments/abc/a_story/"
    assert item.title == "A subreddit story"
    assert item.source_name == "reddit"


async def test_edgar_requires_user_agent_email(monkeypatch):
    from src.core.config import settings
    from src.services.ingest_adapters import EdgarIngestAdapter

    monkeypatch.setattr(settings, "EDGAR_USER_AGENT_EMAIL", "")
    with pytest.raises(ValueError, match="EDGAR_USER_AGENT_EMAIL"):
        EdgarIngestAdapter("Acme")


async def test_edgar_adapter_parses_filings(monkeypatch):
    from src.core.config import settings
    from src.services.ingest_adapters import EdgarIngestAdapter

    monkeypatch.setattr(settings, "EDGAR_USER_AGENT_EMAIL", "ops@example.com")
    _patch_http(monkeypatch, lambda url: FakeResponse(EDGAR_JSON))
    items = await EdgarIngestAdapter("Acme", limit=5).fetch()
    assert len(items) == 1
    assert items[0].source_name == "sec-edgar"
    assert "8-K" in items[0].summary
    assert items[0].url.startswith("https://www.sec.gov/")


async def test_x_requires_bearer_token(monkeypatch):
    from src.core.config import settings
    from src.services.ingest_adapters import XIngestAdapter

    monkeypatch.setattr(settings, "X_BEARER_TOKEN", "")
    with pytest.raises(ValueError, match="X_BEARER_TOKEN"):
        XIngestAdapter("from:Reuters")


async def test_x_adapter_parses_tweets(monkeypatch):
    from src.core.config import settings
    from src.services.ingest_adapters import XIngestAdapter

    monkeypatch.setattr(settings, "X_BEARER_TOKEN", "test-token")
    _patch_http(monkeypatch, lambda url: FakeResponse(X_JSON))
    items = await XIngestAdapter("news lang:en", limit=5).fetch()
    assert len(items) == 1
    assert items[0].url == "https://x.com/i/web/status/123456789"
    assert items[0].title == "Breaking: market moves"


async def test_unknown_feed_type_refused():
    from src.services.ingest_adapters import build_ingest_adapter

    with pytest.raises(ValueError, match="Unknown feed_type"):
        build_ingest_adapter("carrier-pigeon", "url", 5)


# ── Admin CRUD + runner ───────────────────────────────────────────────────────
async def _make_admin(client, auth):
    identity = await auth()
    from sqlalchemy import update

    from src.core.database import AsyncSessionFactory
    from src.data.entities.models import User

    async with AsyncSessionFactory() as db:
        await db.execute(update(User).where(User.id == identity["user_id"]).values(role="ADMIN"))
        await db.commit()
    return identity


async def test_feed_crud_lifecycle(client, auth):
    admin = await _make_admin(client, auth)
    headers = admin["headers"]

    reg = await client.post(
        "/api/v1/admin/ingest/feeds",
        json={"name": "BBC World", "feed_type": "rss", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
        headers=headers,
    )
    assert reg.status_code == 201, reg.text
    feed = reg.json()
    assert feed["feed_type"] == "rss"

    listing = await client.get("/api/v1/admin/ingest/feeds", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["count"] == 1

    dup = await client.post(
        "/api/v1/admin/ingest/feeds",
        json={"name": "BBC Again", "feed_type": "rss", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
        headers=headers,
    )
    assert dup.status_code == 409

    bad = await client.post(
        "/api/v1/admin/ingest/feeds",
        json={"name": "Carrier", "feed_type": "carrier-pigeon", "url": "x"},
        headers=headers,
    )
    assert bad.status_code == 422

    deact = await client.delete(f"/api/v1/admin/ingest/feeds/{feed['id']}", headers=headers)
    assert deact.status_code == 200
    assert deact.json()["is_active"] is False

    rea = await client.post(f"/api/v1/admin/ingest/feeds/{feed['id']}/activate", headers=headers)
    assert rea.status_code == 200
    assert rea.json()["is_active"] is True


async def test_feed_crud_requires_admin(client, auth):
    identity = await auth()
    resp = await client.get("/api/v1/admin/ingest/feeds", headers=identity["headers"])
    assert resp.status_code == 403

    reg = await client.post(
        "/api/v1/admin/ingest/feeds",
        json={"name": "nope", "feed_type": "rss", "url": "https://example.org/feed.xml"},
        headers=identity["headers"],
    )
    assert reg.status_code == 403


async def test_run_feeds_persists_and_dedupes(monkeypatch, client, auth):
    from src.core.config import settings
    from src.services.ingest_adapters import RssIngestAdapter

    handler = lambda url: FakeResponse(RSS_XML, is_json=False)
    _patch_http(monkeypatch, handler)

    admin = await _make_admin(client, auth)
    headers = admin["headers"]
    reg = await client.post(
        "/api/v1/admin/ingest/feeds",
        json={"name": "Mock RSS", "feed_type": "rss", "url": "https://example.org/feed.xml"},
        headers=headers,
    )
    assert reg.status_code == 201

    first = await client.post("/api/v1/admin/ingest/feeds/run", headers=headers)
    assert first.status_code == 200
    assert first.json()["items"] == 2
    assert first.json()["errors"] == 0

    second = await client.post("/api/v1/admin/ingest/feeds/run", headers=headers)
    assert second.status_code == 200
    assert second.json()["items"] == 0

    from sqlalchemy import func, select

    from src.core.database import AsyncSessionFactory
    from src.data.entities.models import Article

    async with AsyncSessionFactory() as db:
        count = await db.scalar(select(func.count(Article.id)))
        assert count == 2

    feed = reg.json()
    status = await client.get(f"/api/v1/admin/ingest/feeds/{feed['id']}", headers=headers)
    assert status.json()["last_fetched_at"] is not None
    assert status.json()["last_error"] is None
