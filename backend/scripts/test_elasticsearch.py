"""
GlobeLens AI — Elasticsearch Integration Test Suite
===================================================
Tests index mappings, bulk database sync, real-time pipeline indexing,
and endpoints GET /query and GET /autocomplete.
"""
import asyncio
import sys
import uuid
import structlog
from httpx import AsyncClient
from sqlalchemy import select, delete

# Add /app to sys.path to resolve imports within the container environment
sys.path.insert(0, "/app")

from src.api.main import app
from src.core.config import settings
from src.core.database import AsyncSessionFactory
from src.core.elasticsearch import es_client
from src.data.entities.models import Event, Article, Source, ProcessingStatus
from src.services.search_service import SearchService
from src.data.repositories.search_repository import SearchRepository
from src.api.schemas.intelligence import EventIntelligenceResponse, IntelligenceTopic, IntelligenceBiasLean

logger = structlog.get_logger()

# Test constants
TEST_EVENT_TITLE = "Elasticsearch Test Event Title"
TEST_EVENT_SUMMARY = (
    "This is a detailed summary of the Elasticsearch test event. It contains three paragraphs as required by validation.\n\n"
    "Paragraph two discusses the engineering of search index pipelines and Elasticsearch mappings.\n\n"
    "Paragraph three concludes the test suite for real-time document sync and prefix matching."
)
TEST_SOURCE_NAME = "ES Test Source"


async def clean_up(session) -> None:
    """Safely cleans up database records and resets the Elasticsearch test index."""
    # Delete test events
    res_evt = await session.execute(
        select(Event).where(
            Event.title.like("%Test Event%") |
            Event.title.like("%Search Index Sync%") |
            Event.title.like("%Supernovae%")
        )
    )
    events = res_evt.scalars().all()
    for evt in events:
        await session.delete(evt)

    # Delete test articles
    res_art = await session.execute(
        select(Article).where(Article.url.like("https://example.com/es-test-%"))
    )
    articles = res_art.scalars().all()
    for art in articles:
        await session.delete(art)

    # Delete test source
    await session.execute(
        delete(Source).where(Source.name == TEST_SOURCE_NAME)
    )
    await session.commit()

    # Clean up Elasticsearch
    try:
        await es_client.indices.delete(index=settings.ELASTICSEARCH_INDEX_EVENTS, ignore_unavailable=True)
        search_service = SearchService()
        await search_service.create_events_index()
        logger.info("Recreated clean Elasticsearch test index")
    except Exception as exc:
        logger.error("Failed to reset Elasticsearch index", error=str(exc))


async def run_tests() -> None:
    print("=== STARTING ELASTICSEARCH INTEGRATION TESTS ===")

    # Initialize/Reset Database and Elasticsearch
    async with AsyncSessionFactory() as session:
        await clean_up(session)

        # Setup base source
        source = Source(name=TEST_SOURCE_NAME, url="https://example.com")
        session.add(source)
        await session.flush()

        # 1. Create a PROCESSED event directly in DB to test Bulk Sync
        print("\n1. Testing Bulk Synchronization...")
        event_bulk = Event(
            title="Search Index Sync Event",
            summary=(
                "A comprehensive study on distributed database search systems.\n\n"
                "It focuses on standard tokenizers and inverted index structures.\n\n"
                "In conclusion, Elasticsearch is highly performant."
            ),
            topic="TECHNOLOGY",
            country="United States",
            latitude=37.7749,
            longitude=-122.4194,
            importance_score=8.5,
            status="PROCESSED"
        )
        session.add(event_bulk)
        await session.commit()
        event_bulk_id = event_bulk.id

    # Run Bulk Sync
    search_service = SearchService()
    indexed, failed = await search_service.sync_all_processed_events()
    print(f"Bulk sync complete. Indexed: {indexed}, Failed: {failed}")
    assert indexed >= 1, "Expected at least 1 document to be bulk synchronized"
    assert failed == 0, "Expected 0 bulk sync failures"

    # Allow Elasticsearch to refresh the index
    await es_client.indices.refresh(index=settings.ELASTICSEARCH_INDEX_EVENTS)

    # Verify document exists in Elasticsearch via SearchRepository
    search_repo = SearchRepository()
    matches = await search_repo.search("distributed database search")
    print(f"Search results for 'distributed database search': {len(matches)}")
    assert len(matches) > 0, "Expected search to return the bulk synced event"
    assert matches[0]["id"] == str(event_bulk_id)

    # 2. Test Real-time Synchronization (Pipeline Trigger)
    print("\n2. Testing Real-time Synchronization Pipeline...")
    async with AsyncSessionFactory() as session:
        source_res = await session.execute(select(Source).where(Source.name == TEST_SOURCE_NAME))
        source = source_res.scalars().first()

        event_rt = Event(
            title="Supernovae and Space Telescope Missions",
            summary=None,
            status="DRAFT"
        )
        session.add(event_rt)
        await session.flush()

        article_rt = Article(
            title="James Webb Space Telescope Captures Supernova",
            content="NASA's JWST has captured a spectacular new image of a distant supernova, revealing detailed gas expansion.",
            url="https://example.com/es-test-rt1",
            processing_status=ProcessingStatus.CLUSTERED,
            source_id=source.id,
            event_id=event_rt.id
        )
        session.add(article_rt)
        await session.commit()
        event_rt_id = event_rt.id

    mock_intelligence = EventIntelligenceResponse(
        summary=(
            "The James Webb Space Telescope has captured a stunning supernova explosion.\n\n"
            "This observation allows researchers to study gas expansion in unprecedented detail.\n\n"
            "Astronomers believe this discovery will recalibrate cosmological distance models."
        ),
        topic=IntelligenceTopic.TECHNOLOGY,
        bias_lean=IntelligenceBiasLean.CENTER,
        location_country="France",
        latitude=46.2276,
        longitude=2.2137,
        importance_score=7.0
    )

    # IMPORTANT: Do NOT call process_pending_events() here — it would process
    # ALL draft events in the database with mock data, contaminating production.
    # Instead, scope the update to only the test event via the repository.
    from src.data.repositories.event_repository import EventRepository
    async with AsyncSessionFactory() as session:
        event_repo = EventRepository(session)
        intel_data = {
            "summary": mock_intelligence.summary,
            "topic": mock_intelligence.topic.value,
            "bias_lean": mock_intelligence.bias_lean.value,
            "location_country": mock_intelligence.location_country,
            "latitude": mock_intelligence.latitude,
            "longitude": mock_intelligence.longitude,
            "importance_score": mock_intelligence.importance_score
        }
        await event_repo.update_event_intelligence(event_rt_id, intel_data)
        print(f"Test event updated to PROCESSED via scoped repository call")

    # Trigger real-time search indexing for the test event only
    event_data = {
        "title": "Supernovae and Space Telescope Missions",
        "summary": mock_intelligence.summary,
        "topic": mock_intelligence.topic.value,
        "location_country": mock_intelligence.location_country,
        "importance_score": mock_intelligence.importance_score
    }
    await search_service.index_processed_event(event_rt_id, event_data)

    # Allow Elasticsearch to refresh the index
    await es_client.indices.refresh(index=settings.ELASTICSEARCH_INDEX_EVENTS)

    # Verify the real-time event is searchable via repository
    matches_rt = await search_repo.search("Webb Space Telescope")
    print(f"Search results for 'Webb Space Telescope': {len(matches_rt)}")
    assert len(matches_rt) > 0, "Expected search to return the real-time indexed event"
    assert matches_rt[0]["id"] == str(event_rt_id)

    # 3. Test HTTP API Endpoints via AsyncClient
    print("\n3. Testing GET API Endpoints...")
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Search Query Endpoint
        resp = await client.get("/api/v1/search/query", params={"q": "Space Telescope"})
        print(f"GET /api/v1/search/query status: {resp.status_code}")
        assert resp.status_code == 200
        results = resp.json()
        print(f"GET /api/v1/search/query results count: {len(results)}")
        assert len(results) > 0
        assert results[0]["id"] == str(event_rt_id)
        assert results[0]["location_country"] == "France"

        # Autocomplete Endpoint
        resp_ac = await client.get("/api/v1/search/autocomplete", params={"prefix": "Superno"})
        print(f"GET /api/v1/search/autocomplete status: {resp_ac.status_code}")
        assert resp_ac.status_code == 200
        suggestions = resp_ac.json()
        print(f"GET /api/v1/search/autocomplete results count: {len(suggestions)}")
        assert len(suggestions) > 0
        assert any(s["id"] == str(event_rt_id) for s in suggestions)

    # Clean up test records
    print("\n4. Cleaning up test database and index records...")
    async with AsyncSessionFactory() as session:
        await clean_up(session)

    print("\n=== ALL ELASTICSEARCH INTEGRATION TESTS PASSED SUCCESSFULLY! ===")


if __name__ == "__main__":
    asyncio.run(run_tests())
