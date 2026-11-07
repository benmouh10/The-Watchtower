import asyncio
import uuid
import structlog
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock

# Add app to path if not present
import sys
sys.path.insert(0, "/app")

from src.api.main import app
from src.core.database import AsyncSessionFactory
from src.data.entities.models import User, UserRole, Article, Source, Embedding, ProcessingStatus
from src.services.embedding_service import EmbeddingService
from sqlalchemy import select, delete

logger = structlog.get_logger()

async def clean_db(session, test_user_email, test_article_url, test_source_name):
    # Delete test users
    await session.execute(delete(User).where(User.email.in_([test_user_email, "embed.standard@example.com"])))
    
    # Get article if it exists
    res = await session.execute(select(Article).where(Article.url == test_article_url))
    art = res.scalars().first()
    if art:
        # Delete embedding
        await session.execute(delete(Embedding).where(Embedding.article_id == art.id))
        # Delete article
        await session.delete(art)
        
    # Delete source
    await session.execute(delete(Source).where(Source.name == test_source_name))
    await session.commit()

async def run_tests():
    print("=== STARTING EMBEDDING ENGINE PIPELINE TESTS ===")
    
    test_user_email = "embed.admin@example.com"
    test_source_name = "Embed Test Source"
    test_article_url = "https://example.com/embed-test-article"
    
    # 1. Setup Test Database Records
    print("\n1. Setting up test records (Source, Article with SCRAPED status)...")
    async with AsyncSessionFactory() as session:
        # Clean up any leftover records
        await clean_db(session, test_user_email, test_article_url, test_source_name)
        
        # Create test source
        source = Source(
            name=test_source_name,
            url="https://example.com"
        )
        session.add(source)
        await session.flush() # Populate source.id
        
        # Create test article in SCRAPED status
        article = Article(
            title="Vector Processing Test Article",
            content="This is a test article content that will be transformed into a vector embedding.",
            url=test_article_url,
            processing_status=ProcessingStatus.SCRAPED,
            source_id=source.id
        )
        session.add(article)
        
        # Create Admin User
        admin_user = User(
            name="Embed Admin",
            email=test_user_email,
            password_hash="dummy_hash_for_test",
            role=UserRole.ADMIN
        )
        session.add(admin_user)
        
        # Create Standard User
        standard_user = User(
            name="Embed Standard User",
            email="embed.standard@example.com",
            password_hash="dummy_hash",
            role=UserRole.AUTH_USER
        )
        session.add(standard_user)
        await session.commit()
        
    # 2. Test Embedding Service generate_vector and process_scraped_batch in isolation (Mocking OpenAI API)
    print("\n2. Testing EmbeddingService.process_scraped_batch with mocked API...")
    mock_vector = [0.1] * 1536
    
    with patch.object(EmbeddingService, 'generate_vector', new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_vector
        
        service = EmbeddingService()
        processed = await service.process_scraped_batch(limit=200)
        
        print(f"Processed batch size: {processed}")
        assert processed >= 1, "Expected at least 1 article to be processed"
        assert mock_gen.call_count >= 1
        
        # Verify DB changes
        async with AsyncSessionFactory() as session:
            res = await session.execute(select(Article).where(Article.url == test_article_url))
            updated_art = res.scalars().first()
            assert updated_art is not None
            print(f"Updated Article processing_status: {updated_art.processing_status.value}")
            assert updated_art.processing_status == ProcessingStatus.EMBEDDED, "Status should be EMBEDDED"
            
            # Verify Embedding row
            embed_res = await session.execute(select(Embedding).where(Embedding.article_id == updated_art.id))
            embedding_row = embed_res.scalars().first()
            assert embedding_row is not None
            print(f"Saved Embedding dimensions: {len(embedding_row.vector)}")
            assert len(embedding_row.vector) == 1536
            assert embedding_row.model == service._model
            
    # 3. Test HTTP Endpoint POST /api/v1/admin/embed/process Security and Flow
    print("\n3. Testing HTTP Admin endpoint /api/v1/admin/embed/process...")
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Register and login standard user via API to get real JWT token
        reg_payload = {
            "name": "Standard User",
            "email": "embed.std.api@example.com",
            "password": "stdpassword123"
        }
        await client.post("/api/v1/auth/register", json=reg_payload)
        login_resp = await client.post("/api/v1/auth/login", json={
            "email": "embed.std.api@example.com",
            "password": "stdpassword123"
        })
        std_token = login_resp.json()["access_token"]
        
        # Try calling endpoint with standard user token (should return 403)
        resp = await client.post(
            "/api/v1/admin/embed/process",
            headers={"Authorization": f"Bearer {std_token}"}
        )
        print(f"Standard User status: {resp.status_code}")
        assert resp.status_code == 403
        
        # Register and login admin user via API to get real admin JWT token
        admin_reg_payload = {
            "name": "Admin User",
            "email": "embed.admin.api@example.com",
            "password": "adminpassword123"
        }
        await client.post("/api/v1/auth/register", json=admin_reg_payload)
        
        # Promote admin user in DB
        async with AsyncSessionFactory() as session:
            res = await session.execute(select(User).where(User.email == "embed.admin.api@example.com"))
            user = res.scalars().first()
            user.role = UserRole.ADMIN
            await session.commit()
            
        admin_login_resp = await client.post("/api/v1/auth/login", json={
            "email": "embed.admin.api@example.com",
            "password": "adminpassword123"
        })
        admin_token = admin_login_resp.json()["access_token"]
        
        # Setup database for endpoint test (need at least one SCRAPED article)
        async with AsyncSessionFactory() as session:
            await session.execute(delete(Article).where(Article.url == "https://example.com/api-embed-test"))
            res = await session.execute(select(Source).where(Source.name == test_source_name))
            source = res.scalars().first()
            
            article = Article(
                title="API Endpoint Processing Test Article",
                content="This is content for testing through the API endpoint.",
                url="https://example.com/api-embed-test",
                processing_status=ProcessingStatus.SCRAPED,
                source_id=source.id
            )
            session.add(article)
            await session.commit()
            
        # Trigger via endpoint using admin token (should return 202)
        with patch.object(EmbeddingService, 'generate_vector', new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = mock_vector
            
            resp = await client.post(
                "/api/v1/admin/embed/process",
                headers={"Authorization": f"Bearer {admin_token}"}
            )
            print(f"Admin User status: {resp.status_code}")
            print(f"Admin User response: {resp.json()}")
            assert resp.status_code == 202
            assert resp.json() == {"status": "embedding_batch_initiated"}
            
            # Wait for background task to run
            await asyncio.sleep(2)
            mock_gen.assert_called()
            
    # 4. Clean up test records
    print("\n4. Cleaning up test database records...")
    async with AsyncSessionFactory() as session:
        await clean_db(session, test_user_email, test_article_url, test_source_name)
        # Clean up api test records
        await session.execute(delete(User).where(User.email.in_(["embed.std.api@example.com", "embed.admin.api@example.com"])))
        res = await session.execute(select(Article).where(Article.url == "https://example.com/api-embed-test"))
        art = res.scalars().first()
        if art:
            await session.execute(delete(Embedding).where(Embedding.article_id == art.id))
            await session.delete(art)
        await session.commit()
        
    print("\n=== ALL EMBEDDING ENGINE TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
