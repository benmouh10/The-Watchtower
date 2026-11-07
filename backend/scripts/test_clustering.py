import asyncio
import uuid
import structlog
from httpx import AsyncClient
from sqlalchemy import select, delete, update

# Add app to path if not present
import sys
sys.path.insert(0, "/app")

from src.api.main import app
from src.core.database import AsyncSessionFactory
from src.data.entities.models import User, UserRole, Article, Source, Embedding, Event, ProcessingStatus
from src.services.clustering_service import ClusteringService

logger = structlog.get_logger()

async def clean_db(session, test_user_email, test_source_name):
    # Delete test users
    await session.execute(delete(User).where(User.email.in_([test_user_email, "cluster.std.api@example.com", "cluster.admin.api@example.com"])))
    
    # Delete test articles, embeddings and events
    res_art = await session.execute(select(Article).where(Article.url.like("https://example.com/cluster-test-%")))
    articles = res_art.scalars().all()
    for art in articles:
        await session.execute(delete(Embedding).where(Embedding.article_id == art.id))
        await session.delete(art)
        
    res_evt = await session.execute(select(Event).where(Event.title.like("Cluster Test %")))
    events = res_evt.scalars().all()
    for evt in events:
        await session.delete(evt)
        
    # Delete source
    await session.execute(delete(Source).where(Source.name == test_source_name))
    
    # Update pre-existing EMBEDDED articles to CLUSTERED to isolate our test run
    await session.execute(
        update(Article)
        .where(Article.processing_status == ProcessingStatus.EMBEDDED)
        .values(processing_status=ProcessingStatus.CLUSTERED)
    )
    
    await session.commit()

async def run_tests():
    print("=== STARTING CLUSTERING ENGINE PIPELINE TESTS ===")
    
    test_user_email = "cluster.admin@example.com"
    test_source_name = "Cluster Test Source"
    
    # Vectors: 1536 dimensions
    # Base vector: direction (1, 0, 0, ...)
    base_vector = [1.0] + [0.0] * 1535
    # Very similar vector: direction (0.99, 0.01, 0, ...)
    similar_vector = [0.99, 0.01] + [0.0] * 1534
    # Very different vector: direction (0, 1, 0, ...)
    different_vector = [0.0, 1.0] + [0.0] * 1534
    
    async with AsyncSessionFactory() as session:
        # Clean up any leftover records and isolate EMBEDDED status
        await clean_db(session, test_user_email, test_source_name)
        
        # Create test source
        source = Source(
            name=test_source_name,
            url="https://example.com"
        )
        session.add(source)
        await session.flush()
        
        # Create an existing Event
        existing_event = Event(
            title="Cluster Test Event 1",
            summary="Existing event summary",
            topic="Tech"
        )
        session.add(existing_event)
        await session.flush()
        
        # Create an already CLUSTERED article in this event with base_vector
        base_article = Article(
            title="Base Article on Healthcare AI",
            content="AI is revolutionizing hospital workflows and medical diagnoses globally.",
            url="https://example.com/cluster-test-base",
            processing_status=ProcessingStatus.CLUSTERED,
            source_id=source.id,
            event_id=existing_event.id
        )
        session.add(base_article)
        await session.flush()
        
        base_embedding = Embedding(
            article_id=base_article.id,
            vector=base_vector,
            model="text-embedding-3-small"
        )
        session.add(base_embedding)
        
        # Create an EMBEDDED (unclustered) article SIMILAR to base article
        similar_article = Article(
            title="Similar Article on Healthcare AI",
            content="Medical workflows and clinical diagnostics are seeing major disruptions due to AI.",
            url="https://example.com/cluster-test-similar",
            processing_status=ProcessingStatus.EMBEDDED,
            source_id=source.id
        )
        session.add(similar_article)
        await session.flush()
        
        similar_embedding = Embedding(
            article_id=similar_article.id,
            vector=similar_vector,
            model="text-embedding-3-small"
        )
        session.add(similar_embedding)
        
        # Create an EMBEDDED (unclustered) article DIFFERENT from base article
        different_article = Article(
            title="Different Article on Space Exploration",
            content="Mars rover discovers new indicators of ancient liquid water under crater sediment.",
            url="https://example.com/cluster-test-different",
            processing_status=ProcessingStatus.EMBEDDED,
            source_id=source.id
        )
        session.add(different_article)
        await session.flush()
        
        different_embedding = Embedding(
            article_id=different_article.id,
            vector=different_vector,
            model="text-embedding-3-small"
        )
        session.add(different_embedding)
        
        # Create Admin User
        admin_user = User(
            name="Cluster Admin",
            email=test_user_email,
            password_hash="dummy_hash_for_test",
            role=UserRole.ADMIN
        )
        session.add(admin_user)
        await session.commit()
        
    # 2. Test Clustering Service cluster_unassigned_articles
    print("\n2. Testing ClusteringService.cluster_unassigned_articles in isolation...")
    
    service = ClusteringService()
    assigned, created = await service.cluster_unassigned_articles()
    
    print(f"Assigned to existing event: {assigned}")
    print(f"New events created: {created}")
    
    assert assigned == 1, f"Expected similar article to merge with the existing event, but got assigned={assigned}"
    assert created == 1, f"Expected different article to spawn a new event, but got created={created}"
    
    # Verify DB changes
    async with AsyncSessionFactory() as session:
        # Check similar article
        res = await session.execute(select(Article).where(Article.url == "https://example.com/cluster-test-similar"))
        art_sim = res.scalars().first()
        assert art_sim is not None
        print(f"Similar Article processing_status: {art_sim.processing_status.value}")
        print(f"Similar Article event_id: {art_sim.event_id}")
        assert art_sim.processing_status == ProcessingStatus.CLUSTERED
        assert art_sim.event_id == existing_event.id, "Similar article should join existing event"
        
        # Check different article
        res_diff = await session.execute(select(Article).where(Article.url == "https://example.com/cluster-test-different"))
        art_diff = res_diff.scalars().first()
        assert art_diff is not None
        print(f"Different Article processing_status: {art_diff.processing_status.value}")
        print(f"Different Article event_id: {art_diff.event_id}")
        assert art_diff.processing_status == ProcessingStatus.CLUSTERED
        assert art_diff.event_id is not None
        assert art_diff.event_id != existing_event.id, "Different article should spawn a new event"
        
        # Verify new event details
        evt_res = await session.get(Event, art_diff.event_id)
        assert evt_res is not None
        print(f"New Event title placeholder: '{evt_res.title}'")
        assert evt_res.title == "Different Article on Space Exploration"
        
    # 3. Test HTTP Endpoint POST /api/v1/admin/cluster/process Security and Flow
    print("\n3. Testing HTTP Admin endpoint /api/v1/admin/cluster/process...")
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Register and login standard user
        await client.post("/api/v1/auth/register", json={
            "name": "Standard User",
            "email": "cluster.std.api@example.com",
            "password": "stdpassword123"
        })
        login_resp = await client.post("/api/v1/auth/login", json={
            "email": "cluster.std.api@example.com",
            "password": "stdpassword123"
        })
        std_token = login_resp.json()["access_token"]
        
        # Try calling endpoint with standard user (403 expected)
        resp = await client.post(
            "/api/v1/admin/cluster/process",
            headers={"Authorization": f"Bearer {std_token}"}
        )
        print(f"Standard User status: {resp.status_code}")
        assert resp.status_code == 403
        
        # Register and login admin user
        await client.post("/api/v1/auth/register", json={
            "name": "Admin User",
            "email": "cluster.admin.api@example.com",
            "password": "adminpassword123"
        })
        async with AsyncSessionFactory() as session:
            res = await session.execute(select(User).where(User.email == "cluster.admin.api@example.com"))
            user = res.scalars().first()
            user.role = UserRole.ADMIN
            await session.commit()
            
        admin_login_resp = await client.post("/api/v1/auth/login", json={
            "email": "cluster.admin.api@example.com",
            "password": "adminpassword123"
        })
        admin_token = admin_login_resp.json()["access_token"]
        
        # Trigger via endpoint using admin token (202 expected)
        resp = await client.post(
            "/api/v1/admin/cluster/process",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        print(f"Admin User status: {resp.status_code}")
        print(f"Admin User response: {resp.json()}")
        assert resp.status_code == 202
        assert resp.json() == {"status": "clustering_process_initiated"}
        
    # 4. Clean up test records
    print("\n4. Cleaning up test database records...")
    async with AsyncSessionFactory() as session:
        await clean_db(session, test_user_email, test_source_name)
        
    print("\n=== ALL CLUSTERING ENGINE TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
