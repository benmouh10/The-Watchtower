import json
import urllib.request
import urllib.error
import time
import asyncio
from sqlalchemy import select

# We can import app things if running in container, or query DB directly.
# Let's run this script inside the backend container to make it easy to access the database and the app server!
# Running it in the container makes importing app modules directly for promotions easy.
import sys
sys.path.insert(0, "/app")

from src.core.database import AsyncSessionFactory
from src.data.entities.models import User, UserRole, Article, Source

BASE_URL = "http://localhost:8000/api/v1"

def make_request(url, method="GET", data=None, headers=None):
    if headers is None:
        headers = {}
    
    req_data = None
    if data is not None:
        req_data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            status_code = response.getcode()
            response_data = json.loads(response.read().decode("utf-8"))
            return status_code, response_data
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_data = e.reason
        return e.code, err_data

async def promote_user(email: str, new_role: UserRole):
    print(f"Promoting user {email} to {new_role.value}...")
    async with AsyncSessionFactory() as session:
        res = await session.execute(select(User).where(User.email == email))
        user = res.scalars().first()
        if not user:
            raise ValueError(f"User with email {email} not found")
        user.role = new_role
        await session.commit()
    print("User promoted successfully!")

async def count_articles():
    async with AsyncSessionFactory() as session:
        res = await session.execute(select(Article))
        articles = res.scalars().all()
        return len(articles)

async def print_recent_articles(limit=5):
    async with AsyncSessionFactory() as session:
        res = await session.execute(select(Article).order_by(Article.created_at.desc()).limit(limit))
        articles = res.scalars().all()

        for idx, art in enumerate(articles, 1):
            print(f"[{idx}] Title: {art.title}")
            print(f"    URL: {art.url}")
            print(f"    Status: {art.processing_status.value if hasattr(art.processing_status, 'value') else art.processing_status}")
            print(f"    Content length: {len(art.content) if art.content else 0} chars")

async def run_scraper_test():
    print("=== STARTING SCRAPER PIPELINE FUNCTIONAL TESTS ===")
    
    timestamp = int(time.time())
    email = f"scraper.tester.{timestamp}@example.com"
    user_payload = {
        "name": "Scraper Tester",
        "email": email,
        "password": "scraperpassword123"
    }

    # 1. Register user (gets AUTH_USER role)
    print("\n1. Registering test user...")
    status, resp = make_request(f"{BASE_URL}/auth/register", "POST", user_payload)
    print(f"Status: {status}")
    assert status == 201, f"Expected 201, got {status}"
    
    # 2. Login to get token
    print("\n2. Logging in test user...")
    status, resp = make_request(f"{BASE_URL}/auth/login", "POST", {
        "email": email,
        "password": "scraperpassword123"
    })
    print(f"Status: {status}")
    assert status == 200, f"Expected 200, got {status}"
    token = resp["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Call sync endpoint (should be 403 Forbidden for AUTH_USER)
    print("\n3. Testing sync route with AUTH_USER role (should fail)...")
    status, resp = make_request(f"{BASE_URL}/articles/sync", "POST", headers=headers)
    print(f"Status: {status}")
    print(f"Response: {resp}")
    assert status == 403, f"Expected 403, got {status}"
    print("OK - Rejection of standard user passed!")

    # 4. Promote user to ADMIN
    await promote_user(email, UserRole.ADMIN)

    # 5. Call sync endpoint (should be 202 Accepted)
    print("\n5. Testing sync route with ADMIN role (should succeed)...")
    status, resp = make_request(f"{BASE_URL}/articles/sync", "POST", headers=headers)
    print(f"Status: {status}")
    print(f"Response: {resp}")
    assert status == 202, f"Expected 202, got {status}"
    assert resp.get("status") == "sync_initiated", "Status message mismatch"
    print("OK - Ingestion initiation passed!")

    # 6. Wait for background task to scrape articles
    initial_count = await count_articles()
    print(f"\n6. Waiting for scraping task (Current article count: {initial_count})...")
    print("Sleeping for 15 seconds to allow Playwright browser tasks to run...")
    time.sleep(15)
    
    new_count = await count_articles()
    print(f"Scrape verification - New article count: {new_count}")
    
    if new_count > initial_count:
        print("\nSUCCESS: Articles were successfully scraped and inserted into the database!")
        await print_recent_articles(5)
    else:
        # Wait a bit longer, Playwright can take some time
        print("Still no articles. Waiting another 15 seconds...")
        time.sleep(15)
        new_count = await count_articles()
        print(f"Scrape verification (retry) - New article count: {new_count}")
        if new_count > initial_count:
            print("\nSUCCESS: Articles were successfully scraped and inserted into the database!")
            await print_recent_articles(5)
        else:
            print("\nWARNING: No new articles were saved. Check logs for scraping errors.")
            
    print("\n=== SCRAPER TESTS COMPLETED ===")

if __name__ == "__main__":
    asyncio.run(run_scraper_test())
