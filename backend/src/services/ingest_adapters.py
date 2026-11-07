"""
GlobeLens AI — Real-time source adapters (beyond RSS).

The ingest pipeline fans out over registered IngestFeed rows. Each feed_type
maps to one adapter here:
  * rss    — any RSS/Atom feed URL (generic; BBC/CNN/Al Jazeera included).
  * reddit — a subreddit JSON feed; uses the public endpoint by default and
             OAuth client-credentials when REDDIT_CLIENT_ID/SECRET are set.
  * edgar  — SEC EDGAR full-text search; SEC requires a real contact email in
             the User-Agent, so EDGAR_USER_AGENT_EMAIL must be configured.
  * x      — the Twitter/X API v2 recent-search; X_BEARER_TOKEN is required.

Adapters return normalized IngestItem records; the caller decides what to
store. X and EDGAR refuse loudly at construction when their mandatory
credential is missing rather than silently degrading the feed to nothing.
"""
import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import feedparser
import httpx

from src.core.config import settings

logger = logging.getLogger("src.services.ingest_adapters")

FEED_TYPES = ("rss", "reddit", "edgar", "x")


@dataclass
class IngestItem:
    title: str
    url: str
    published_at: Optional[datetime]
    summary: Optional[str]
    source_name: str
    image_url: Optional[str] = None


def _coerce_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return None


def _rss_candidate(entry: Dict[str, Any], feed_url: str) -> Optional[IngestItem]:
    from src.services.scraper_service import extract_feed_image

    title = entry.get("title", "").strip()
    link = entry.get("link") or entry.get("id")
    if not title or not link:
        return None
    published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    published_at = None
    if published_parsed:
        published_at = datetime.fromtimestamp(
            calendar.timegm(published_parsed), tz=timezone.utc
        )
    summary = (entry.get("summary") or entry.get("description") or "").strip() or None
    return IngestItem(
        title=title,
        url=link,
        published_at=published_at,
        summary=summary,
        source_name=feed_url,
        image_url=extract_feed_image(entry),
    )


class RssIngestAdapter:
    feed_type = "rss"

    def __init__(self, url: str, limit: int = 25, client: Optional[httpx.AsyncClient] = None):
        self._url = url
        self._limit = limit
        self._client = client

    async def fetch(self) -> List[IngestItem]:
        if self._client is not None:
            response = await self._client.get(self._url)
            response.raise_for_status()
            raw = response.text
        else:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                raw = response.text
        feed = feedparser.parse(raw)
        items = []
        for entry in feed.entries:
            if len(items) >= self._limit:
                break
            candidate = _rss_candidate(entry, self._url)
            if candidate is not None:
                items.append(candidate)
        return items


class RedditIngestAdapter:
    feed_type = "reddit"

    def __init__(self, url: str, limit: int = 25):
        self._path = url.strip()
        self._limit = limit
        self._token: Optional[str] = None

    def _with_oauth(self) -> bool:
        if not (settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET):
            return False
        user_agent = settings.REDDIT_USER_AGENT or "globelens-ai/1.0"
        import base64

        basic = base64.b64encode(
            f"{settings.REDDIT_CLIENT_ID}:{settings.REDDIT_CLIENT_SECRET}".encode()
        ).decode()
        headers = {
            "Authorization": f"Basic {basic}",
            "User-Agent": user_agent,
        }
        data = {"grant_type": "client_credentials"}
        try:
            response = httpx.post(
                "https://www.reddit.com/api/v1/access_token",
                headers=headers,
                data=data,
                timeout=15.0,
            )
            response.raise_for_status()
            self._token = response.json().get("access_token")
        except Exception as exc:
            logger.warning("Reddit OAuth token failed, falling back to public JSON: %s", exc)
            self._token = None
        return bool(self._token)

    async def fetch(self) -> List[IngestItem]:
        path = self._path
        if not path.startswith("/"):
            path = "/" + path
        if path == "/":
            path = "/r/worldnews"
        suffix = f"?limit={self._limit}"
        url = f"https://www.reddit.com{path}.json{suffix}"
        headers = {"User-Agent": settings.REDDIT_USER_AGENT or "globelens-ai/1.0"}
        if self._token is None:
            self._with_oauth()
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()
        children = ((body.get("data") or {}).get("children")) or []
        items = []
        for child in children:
            if len(items) >= self._limit:
                break
            data = child.get("data") or {}
            title = (data.get("title") or "").strip()
            permalink = data.get("permalink")
            if not title or not permalink:
                continue
            items.append(
                IngestItem(
                    title=title,
                    url=f"https://www.reddit.com{permalink}",
                    published_at=_coerce_utc(data.get("created_utc")),
                    summary=(data.get("selftext") or "").strip()[:1000] or None,
                    source_name="reddit",
                )
            )
        return items


class EdgarIngestAdapter:
    feed_type = "edgar"

    def __init__(self, url: str, limit: int = 25):
        self._query = (url or "").strip()
        self._limit = limit
        if not settings.EDGAR_USER_AGENT_EMAIL:
            raise ValueError(
                "EDGAR_USER_AGENT_EMAIL is required for SEC EDGAR ingest; "
                "SEC refuses requests without a real contact email."
            )

    async def fetch(self) -> List[IngestItem]:
        query = self._query or "*"
        params = {"q": query, "forms": "8-K,10-K,10-Q", "start": 0, "count": self._limit}
        headers = {
            "User-Agent": f"GlobeLens AI {settings.EDGAR_USER_AGENT_EMAIL}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                "https://efts.sec.gov/LATEST/search-index",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        hits = (body.get("hits") or {}).get("hits") or []
        items = []
        for hit in hits:
            if len(items) >= self._limit:
                break
            source = hit.get("_source") or {}
            title = (source.get("title") or source.get("display_names") or "SEC filing").strip()
            url = source.get("report_url") or source.get("url")
            if not url:
                continue
            if isinstance(title, list):
                title = ", ".join(str(t) for t in title if t)
            items.append(
                IngestItem(
                    title=str(title)[:500],
                    url=url,
                    published_at=_coerce_utc(source.get("file_date")),
                    summary=(
                        f"SEC {source.get('form_type') or 'filing'} by "
                        f"{source.get('entity_name') or 'unknown filer'}"
                    ),
                    source_name="sec-edgar",
                )
            )
        return items


class XIngestAdapter:
    feed_type = "x"

    def __init__(self, url: str, limit: int = 25):
        self._query = (url or "").strip()
        self._limit = limit
        if not settings.X_BEARER_TOKEN:
            raise ValueError(
                "X_BEARER_TOKEN is required for Twitter/X ingest; the API v2 "
                "search endpoint has no anonymous access."
            )

    async def fetch(self) -> List[IngestItem]:
        query = self._query or "news lang:en -is:retweet"
        params = {
            "query": query,
            "max_results": min(self._limit, 100),
            "tweet.fields": "created_at,author_id",
        }
        headers = {"Authorization": f"Bearer {settings.X_BEARER_TOKEN}"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                "https://api.twitter.com/2/tweets/search/recent",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        tweets = body.get("data") or []
        items = []
        for tweet in tweets:
            if len(items) >= self._limit:
                break
            tweet_id = tweet.get("id")
            text = (tweet.get("text") or "").strip()
            if not tweet_id or not text:
                continue
            items.append(
                IngestItem(
                    title=text[:200],
                    url=f"https://x.com/i/web/status/{tweet_id}",
                    published_at=_coerce_utc(tweet.get("created_at")),
                    summary=text,
                    source_name="x",
                )
            )
        return items


def build_ingest_adapter(feed_type: str, url: str, limit: int) -> Any:
    """Construct the adapter for `feed_type`, refusing unknown types loudly."""
    normalized = (feed_type or "").strip().lower()
    if normalized == "rss":
        return RssIngestAdapter(url=url, limit=limit)
    if normalized == "reddit":
        return RedditIngestAdapter(url=url, limit=limit)
    if normalized == "edgar":
        return EdgarIngestAdapter(url=url, limit=limit)
    if normalized == "x":
        return XIngestAdapter(url=url, limit=limit)
    raise ValueError(
        f"Unknown feed_type {feed_type!r}. Supported: {', '.join(FEED_TYPES)}"
    )