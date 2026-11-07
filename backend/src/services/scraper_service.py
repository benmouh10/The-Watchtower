"""
GlobeLens AI — ScraperService
Handles automated, fault-tolerant news ingestion from BBC, CNN, and Al Jazeera
using a hybrid RSS discovery + Playwright browser hydration + BeautifulSoup parsing pipeline.
"""
import calendar
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import httpx
import feedparser
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import structlog
from sqlalchemy import select

from src.core.database import AsyncSessionFactory
from src.data.entities.models import Source, Article
from src.data.repositories.article_repository import ArticleRepository

logger = structlog.get_logger()

# Mapping publisher homepages/names to their standard RSS feeds
RSS_FEED_MAPPING = {
    "bbc news": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "cnn": "http://rss.cnn.com/rss/edition.rss",
    "al jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "aljazeera": "https://www.aljazeera.com/xml/rss/all.xml"
}


@dataclass
class ArticleExtraction:
    """Result of hydrating one article page: clean text plus any media the
    source published on the page (og:image / og:image:alt / og:video)."""
    content: str
    image_url: Optional[str] = None
    image_caption: Optional[str] = None
    video_url: Optional[str] = None


def _valid_image_url(value: Optional[str]) -> Optional[str]:
    """Accept only absolute http(s) URLs. A data: URI, a relative reference or
    a placeholder is not an image we can attribute to the source, so it is
    dropped rather than stored."""
    if not value:
        return None
    candidate = str(value).strip().strip('"').strip("'")
    if candidate.startswith(("http://", "https://")):
        return candidate
    return None


def extract_feed_image(entry: Dict[str, Any]) -> Optional[str]:
    """Pull the first usable image URL an RSS entry actually provides, in the
    order feeds commonly publish them: media:thumbnail, media:content with an
    image medium, then any image*/ enclosure. Honest in the same way the rest of
    the scraper is: a feed that offers no image yields None, not a guess."""
    thumbnails = entry.get("media_thumbnail") or []
    if isinstance(thumbnails, list):
        for item in thumbnails:
            if isinstance(item, dict):
                url = _valid_image_url(item.get("url"))
                if url:
                    return url

    media_content = entry.get("media_content") or []
    if isinstance(media_content, list):
        for item in media_content:
            if not isinstance(item, dict):
                continue
            medium = (item.get("medium") or "").lower()
            mtype = (item.get("type") or "").lower()
            if medium == "image" or mtype.startswith("image/"):
                url = _valid_image_url(item.get("url"))
                if url:
                    return url

    for enclosure in entry.get("enclosures") or []:
        if not isinstance(enclosure, dict):
            continue
        if (enclosure.get("type") or "").lower().startswith("image/"):
            url = _valid_image_url(enclosure.get("href") or enclosure.get("url"))
            if url:
                return url

    image_field = entry.get("image") or {}
    if isinstance(image_field, dict):
        return _valid_image_url(image_field.get("href"))

    return None


_OG_IMAGE_ATTRS = (
    ("property", "og:image"),
    ("property", "og:image:url"),
    ("property", "og:image:secure_url"),
    ("name", "twitter:image"),
)


def resolve_og_image(soup: BeautifulSoup) -> Optional[str]:
    """First http(s) URL on the hydrated page from the standard OpenGraph /
    Twitter-card image meta tags. These are the source's own published image
    metadata; a page without them yields None."""
    for key, value in _OG_IMAGE_ATTRS:
        tag = soup.find("meta", attrs={key: value})
        if tag:
            url = _valid_image_url(tag.get("content"))
            if url:
                return url
    return None


_OG_CAPTION_ATTRS = (
    ("property", "og:image:alt"),
    ("name", "twitter:image:alt"),
)


def resolve_og_caption(soup: BeautifulSoup) -> Optional[str]:
    """Alt/caption text the source attached to its og:image. Never guessed."""
    for key, value in _OG_CAPTION_ATTRS:
        tag = soup.find("meta", attrs={key: value})
        if tag:
            caption = (tag.get("content") or "").strip()
            if caption:
                return caption
    return None


_OG_VIDEO_ATTRS = (
    ("property", "og:video"),
    ("property", "og:video:url"),
    ("property", "og:video:secure_url"),
    ("name", "twitter:player:stream"),
    ("name", "twitter:player"),
)


def resolve_og_video(soup: BeautifulSoup) -> Optional[str]:
    """First http(s) URL the source declared as its story video (OpenGraph /
    Twitter player meta). Pages without video metadata yield None."""
    for key, value in _OG_VIDEO_ATTRS:
        tag = soup.find("meta", attrs={key: value})
        if tag:
            url = _valid_image_url(tag.get("content"))
            if url:
                return url
    return None


def pick_article_media(
    page_image: Optional[str],
    feed_image: Optional[str],
    page_caption: Optional[str],
) -> tuple:
    """Decide what to store when both the hydrated page and the RSS feed offered
    media. The page's own og:image wins; the feed thumbnail is the fallback.
    Captions only ever come from the page, and only accompany the page's own
    image — using the page's alt text beside the feed thumbnail would attribute
    it to a picture the source never captioned. Returns (image_url,
    image_caption); both None means neither source published an image."""
    image_url = page_image or feed_image
    image_caption = page_caption if page_image else None
    return image_url, image_caption

class ScraperService:
    """
    Service responsible for RSS discovery and scraping using Playwright + BeautifulSoup.
    """

    def _resolve_rss_url(self, source_name: str, source_url: str) -> str:
        """Resolve the homepage URL of a source to its RSS feed endpoint."""
        name_key = source_name.lower().strip()
        if name_key in RSS_FEED_MAPPING:
            return RSS_FEED_MAPPING[name_key]
        
        # Dialect matching / fallback
        if "bbc" in source_url:
            return "https://feeds.bbci.co.uk/news/world/rss.xml"
        if "cnn" in source_url:
            return "http://rss.cnn.com/rss/edition.rss"
        if "aljazeera" in source_url:
            return "https://www.aljazeera.com/xml/rss/all.xml"
            
        return source_url

    async def fetch_rss_links(self, rss_url: str) -> List[Dict[str, Any]]:
        """
        Fetches RSS feed items and returns a list of dictionaries with metadata.
        """
        logger.info("Fetching RSS feed articles", rss_url=rss_url)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(rss_url)
                response.raise_for_status()
                
            # Parse XML feed
            feed = feedparser.parse(response.text)
            articles = []
            
            for entry in feed.entries:
                title = entry.get("title", "Untitled")
                link = entry.get("link")
                if not link:
                    continue
                
                # Convert time.struct_time to timezone-aware datetime (UTC)
                published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
                if published_parsed:
                    ts = calendar.timegm(published_parsed)
                    published_at = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    published_at = datetime.now(timezone.utc)
                    
                articles.append({
                    "title": title,
                    "url": link,
                    "published_at": published_at,
                    # The feed's own thumbnail for the story, if it published one.
                    "image_url": extract_feed_image(entry),
                })
                
            logger.info("RSS parsing complete", rss_url=rss_url, count=len(articles))
            return articles
        except Exception as e:
            logger.error("Failed to fetch or parse RSS feed", rss_url=rss_url, error=str(e))
            return []

    async def extract_full_content(self, url: str) -> str:
        """
        Launches an async headless browser context via Playwright, navigates to the URL,
        waits for DOM content hydration, and parses the clean body text using BeautifulSoup.

        Returns only the extracted text (kept for callers that need a plain string).
        """
        result = await self.extract_article(url)
        return result.content

    async def extract_article(self, url: str) -> ArticleExtraction:
        """
        Like extract_full_content, but returns the clean text together with the
        image the page published for the story (og:image) and its alt text
        (og:image:alt). Both are optional and honest: pages without image
        metadata come back with None, never a placeholder.
        """
        logger.info("Launching Playwright for article extraction", url=url)
        async with async_playwright() as p:
            # Launch headless browser
            browser = await p.chromium.launch(headless=True)
            try:
                # Setup browser context with realistic User-Agent
                user_agent = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
                context = await browser.new_context(user_agent=user_agent)
                page = await context.new_page()

                # Navigate and wait for DOM Content Loaded
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Retrieve fully hydrated HTML content
                html = await page.content()

                # Parse with BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")

                # Clean HTML document of typical metadata/script junk
                for junk_tag in soup(["script", "style", "form", "header", "footer", "nav", "aside", "iframe"]):
                    junk_tag.decompose()

                # Collect body paragraph contents
                paragraphs = soup.find_all("p")
                text_blocks = []
                for p_tag in paragraphs:
                    text = p_tag.get_text(strip=True)
                    # Filter out short fragments (cookies notices, newsletter signups, read-more links)
                    if len(text) > 30:
                        text_blocks.append(text)

                extracted_content = "\n\n".join(text_blocks)
                logger.info("Playwright content extraction success", url=url, size_chars=len(extracted_content))
                return ArticleExtraction(
                    content=extracted_content,
                    image_url=resolve_og_image(soup),
                    image_caption=resolve_og_caption(soup),
                    video_url=resolve_og_video(soup),
                )
            except Exception as e:
                logger.error("Playwright content extraction failed", url=url, error=str(e))
                raise e
            finally:
                await browser.close()

    async def run_pipeline(self) -> None:
        """
        Runs the full hybrid scraping pipeline: discovers new links, checks duplicates,
        runs browser-based extraction, and inserts new articles into the database.
        """
        logger.info("Starting ingestion scraper pipeline execution")
        
        # Spin up dedicated session for background task
        async with AsyncSessionFactory() as session:
            try:
                # 1. Fetch active Sources from DB
                res = await session.execute(select(Source))
                sources_db = res.scalars().all()
                sources = [
                    {"id": s.id, "name": s.name, "url": s.url}
                    for s in sources_db
                ]
                logger.info("Found media sources for sync", count=len(sources))
                
                repo = ArticleRepository(session)
                
                for source in sources:
                    rss_url = self._resolve_rss_url(source["name"], source["url"])
                    logger.info("Syncing source feed", source_name=source["name"], rss_url=rss_url)
                    
                    # 2. Discover articles from RSS feed
                    rss_articles = await self.fetch_rss_links(rss_url)
                    
                    for item in rss_articles:
                        url = item["url"]
                        
                        try:
                            # 3. Fast idempotency pre-check to prevent starting browser for duplicates
                            stmt = select(Article).where(Article.url == url)
                            check_res = await session.execute(stmt)
                            if check_res.scalars().first():
                                logger.debug("Skipping duplicate article url", url=url)
                                continue
                                
                            # 4. Perform Playwright Browser extraction
                            extracted = await self.extract_article(url)
                            if not extracted.content.strip():
                                logger.warn("Scraped article text is empty, skipping insertion", url=url)
                                continue

                            # The page's own OpenGraph image wins; the feed
                            # thumbnail is the fallback when the page published
                            # none. media_type is honest: "image" only when we
                            # actually stored one.
                            image_url, image_caption = pick_article_media(
                                extracted.image_url,
                                item.get("image_url"),
                                extracted.image_caption,
                            )

                            # 5. Insert new article into database
                            article_data = {
                                "title": item["title"],
                                "url": url,
                                "published_at": item["published_at"],
                                "content": extracted.content,
                                "source_id": str(source["id"]),
                                "image_url": image_url,
                                "image_caption": image_caption,
                                "video_url": extracted.video_url,
                                "media_type": "image" if image_url else (
                                    "video" if extracted.video_url else None
                                ),
                            }
                            
                            new_art = await repo.create_scraped_article(article_data)
                            if new_art:
                                logger.info(
                                    "Saved new article to database",
                                    id=str(new_art.id),
                                    title=new_art.title,
                                    url=new_art.url
                                )
                            else:
                                logger.info("Duplicate article dropped during insert", url=url)
                                
                        except Exception as entry_err:
                            logger.exception(
                                "Failed to process article entry in pipeline",
                                url=url
                            )
                            # Rollback session to clear failed transaction state
                            await session.rollback()
                            continue
                            
                logger.info("Ingestion scraper pipeline completed successfully")
            except Exception as e:
                logger.exception("Scraper pipeline encountered a fatal execution failure")
                await session.rollback()
