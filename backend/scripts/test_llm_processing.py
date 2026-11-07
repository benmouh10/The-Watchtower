import asyncio
import uuid
import structlog
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
from sqlalchemy import select, delete, update

# Add app to path if not present
import sys
sys.path.insert(0, "/app")

from src.api.main import app
from src.core.database import AsyncSessionFactory
from src.data.entities.models import User, UserRole, Article, Source, Event, ProcessingStatus, BiasLean
from src.services.llm_service import LLMService
from src.api.schemas.intelligence import EventIntelligenceResponse, IntelligenceTopic, IntelligenceBiasLean

logger = structlog.get_logger()

async def clean_db(session, test_user_email, test_source_name):
    # Delete test users
    await session.execute(
        delete(User).where(
            User.email.in_([
                test_user_email,
                "llm.std.api@example.com",
                "llm.admin.api@example.com"
            ])
        )
    )
    
    # Delete articles
    res_art = await session.execute(
        select(Article).where(Article.url.like("https://example.com/llm-test-%"))
    )
    articles = res_art.scalars().all()
    for art in articles:
        await session.delete(art)
        
    # Delete events
    res_evt = await session.execute(
        select(Event).where(Event.title.like("LLM Test Event%"))
    )
    events = res_evt.scalars().all()
    for evt in events:
        await session.delete(evt)
        
    # Delete source
    await session.execute(delete(Source).where(Source.name == test_source_name))
    
    await session.commit()


async def run_tests():
    print("=== STARTING LLM PROCESSING ENGINE TESTS ===")
    
    test_user_email = "llm.admin@example.com"
    test_source_name = "LLM Test Source"
    
    # 1. Setup Test Database Records
    print("\n1. Setting up test records...")
    async with AsyncSessionFactory() as session:
        # Clean up any leftover records
        await clean_db(session, test_user_email, test_source_name)
        
        # Create test source
        source = Source(
            name=test_source_name,
            url="https://example.com"
        )
        session.add(source)
        await session.flush() # Populate source.id
        
        # Create test event in status DRAFT
        event = Event(
            title="LLM Test Event 1",
            summary=None,
            status="DRAFT"
        )
        session.add(event)
        await session.flush()
        
        # Create test articles in CLUSTERED status linked to the event
        article1 = Article(
            title="LLM Test Article 1",
            content="Local clinics are experiencing high volumes of patients seeking treatment for a new seasonal flu strain.",
            url="https://example.com/llm-test-art1",
            processing_status=ProcessingStatus.CLUSTERED,
            source_id=source.id,
            event_id=event.id
        )
        article2 = Article(
            title="LLM Test Article 2",
            content="A surge in winter viruses has prompted health authorities to issue new recommendations for clinics.",
            url="https://example.com/llm-test-art2",
            processing_status=ProcessingStatus.CLUSTERED,
            source_id=source.id,
            event_id=event.id
        )
        session.add(article1)
        session.add(article2)
        
        # Create Admin User
        admin_user = User(
            name="LLM Admin",
            email=test_user_email,
            password_hash="dummy_hash_for_test",
            role=UserRole.ADMIN
        )
        session.add(admin_user)
        await session.commit()
        
    # 2. Test LLM Service analyze_event_cluster and process_pending_events in isolation (Mocking LLM API calls)
    print("\n2. Testing LLMService.process_pending_events with mocked API...")
    
    mock_intelligence = EventIntelligenceResponse(
        summary=(
            "Health authorities are urging caution after a surge in seasonal winter flu strains.\n\n"
            "Local clinics have experienced record volumes of patients seeking clinical diagnostics and standard treatments.\n\n"
            "Medical experts recommend standard hygiene protocols to curb further virus transmissions."
        ),
        topic=IntelligenceTopic.HEALTH,
        bias_lean=IntelligenceBiasLean.CENTER,
        location_country="United Kingdom",
        latitude=55.3781,
        longitude=-3.4360,
        importance_score=4.5
    )
    
    with patch.object(LLMService, 'analyze_event_cluster', new_callable=AsyncMock) as mock_analyze:
        mock_analyze.return_value = mock_intelligence
        
        service = LLMService()
        processed, failed = await service.process_pending_events()
        
        print(f"Processed events: {processed}, Failed events: {failed}")
        assert processed == 1, "Expected exactly 1 event to be processed"
        assert failed == 0, "Expected 0 events to fail"
        mock_analyze.assert_called_once()
        
        # Verify DB changes
        async with AsyncSessionFactory() as session:
            # Check Event
            res_evt = await session.execute(
                select(Event).where(Event.title == "LLM Test Event 1")
            )
            updated_evt = res_evt.scalars().first()
            assert updated_evt is not None
            print(f"Updated Event status: {updated_evt.status}")
            print(f"Updated Event summary length: {len(updated_evt.summary)}")
            print(f"Updated Event topic: {updated_evt.topic}")
            print(f"Updated Event bias_lean: {updated_evt.bias_lean}")
            print(f"Updated Event location: {updated_evt.country} ({updated_evt.latitude}, {updated_evt.longitude})")
            print(f"Updated Event importance_score: {updated_evt.importance_score}")
            
            assert updated_evt.status == "PROCESSED"
            assert updated_evt.topic == "HEALTH"
            assert updated_evt.bias_lean == BiasLean.CENTER
            assert updated_evt.country == "United Kingdom"
            assert updated_evt.latitude == 55.3781
            assert updated_evt.longitude == -3.4360
            assert updated_evt.importance_score == 4.5
            
            # Verify associated articles are now PROCESSED
            res_arts = await session.execute(
                select(Article).where(Article.event_id == updated_evt.id)
            )
            arts = res_arts.scalars().all()
            assert len(arts) == 2
            for art in arts:
                print(f"Article {art.title} status: {art.processing_status.value}")
                assert art.processing_status == ProcessingStatus.PROCESSED
                
    # 3. Test HTTP Endpoint POST /api/v1/admin/llm/process Security and Flow
    print("\n3. Testing HTTP Admin endpoint /api/v1/admin/llm/process...")
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Register and login standard user via API
        await client.post("/api/v1/auth/register", json={
            "name": "Standard User",
            "email": "llm.std.api@example.com",
            "password": "stdpassword123"
        })
        login_resp = await client.post("/api/v1/auth/login", json={
            "email": "llm.std.api@example.com",
            "password": "stdpassword123"
        })
        std_token = login_resp.json()["access_token"]
        
        # Call endpoint with standard user (403 expected)
        resp = await client.post(
            "/api/v1/admin/llm/process",
            headers={"Authorization": f"Bearer {std_token}"}
        )
        print(f"Standard User status: {resp.status_code}")
        assert resp.status_code == 403
        
        # Register and login admin user via API
        await client.post("/api/v1/auth/register", json={
            "name": "Admin User",
            "email": "llm.admin.api@example.com",
            "password": "adminpassword123"
        })
        
        # Promote admin user in DB
        async with AsyncSessionFactory() as session:
            res = await session.execute(
                select(User).where(User.email == "llm.admin.api@example.com")
            )
            user = res.scalars().first()
            user.role = UserRole.ADMIN
            await session.commit()
            
        admin_login_resp = await client.post("/api/v1/auth/login", json={
            "email": "llm.admin.api@example.com",
            "password": "adminpassword123"
        })
        admin_token = admin_login_resp.json()["access_token"]
        
        # Setup database for endpoint test (need one unprocessed event with articles)
        async with AsyncSessionFactory() as session:
            res_src = await session.execute(
                select(Source).where(Source.name == test_source_name)
            )
            source = res_src.scalars().first()
            
            event_api = Event(
                title="LLM Test Event API Endpoint",
                summary=None,
                status="DRAFT"
            )
            session.add(event_api)
            await session.flush()
            
            art_api = Article(
                title="LLM Test Article API",
                content="Clinical diagnostics and medical diagnostics show heavy reliance on seasonal vaccine campaigns.",
                url="https://example.com/llm-test-art-api",
                processing_status=ProcessingStatus.CLUSTERED,
                source_id=source.id,
                event_id=event_api.id
            )
            session.add(art_api)
            await session.commit()
            
        # Trigger via endpoint using admin token (should return 202)
        with patch.object(LLMService, 'analyze_event_cluster', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = mock_intelligence
            
            resp = await client.post(
                "/api/v1/admin/llm/process",
                headers={"Authorization": f"Bearer {admin_token}"}
            )
            print(f"Admin User status: {resp.status_code}")
            print(f"Admin User response: {resp.json()}")
            assert resp.status_code == 202
            assert resp.json() == {"status": "llm_processing_initiated"}
            
            # Wait for background task to run
            await asyncio.sleep(2)
            mock_analyze.assert_called()
            
    # 4. Clean up test records
    print("\n4. Cleaning up test database records...")
    async with AsyncSessionFactory() as session:
        await clean_db(session, test_user_email, test_source_name)
        # Clean up api test records
        await session.execute(
            delete(Article).where(Article.url == "https://example.com/llm-test-art-api")
        )
        res_evt = await session.execute(
            select(Event).where(Event.title == "LLM Test Event API Endpoint")
        )
        evt_api = res_evt.scalars().first()
        if evt_api:
            await session.delete(evt_api)
        await session.commit()
        
    print("\n=== ALL LLM PROCESSING ENGINE TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
