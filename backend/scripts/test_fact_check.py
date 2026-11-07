import json
import urllib.request
import urllib.error
import time
import asyncio
from sqlalchemy import select

import sys
sys.path.insert(0, "/app")

from src.core.database import AsyncSessionFactory
from src.data.entities.models import User, FactCheckRequest as FactCheckModel

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

async def count_fact_checks(user_uuid):
    async with AsyncSessionFactory() as session:
        res = await session.execute(
            select(FactCheckModel).where(FactCheckModel.user_id == user_uuid)
        )
        records = res.scalars().all()
        return len(records)

async def run_fact_check_test():
    print("=== STARTING FACT-CHECK SERVICE INTEGRATION TESTS ===")
    
    timestamp = int(time.time())
    
    # User 1 (Main test user)
    email1 = f"fact.tester1.{timestamp}@example.com"
    user1_payload = {
        "name": "Fact Check User 1",
        "email": email1,
        "password": "testpassword123"
    }

    # User 2 (For cross-user access-control checks)
    email2 = f"fact.tester2.{timestamp}@example.com"
    user2_payload = {
        "name": "Fact Check User 2",
        "email": email2,
        "password": "testpassword123"
    }

    # 1. Register test users
    print("\n1. Registering test users...")
    status, u1 = make_request(f"{BASE_URL}/auth/register", "POST", user1_payload)
    assert status == 201, f"Failed user 1 registration: {status}"
    
    status, u2 = make_request(f"{BASE_URL}/auth/register", "POST", user2_payload)
    assert status == 201, f"Failed user 2 registration: {status}"
    
    # 2. Login as User 1
    print("\n2. Logging in User 1 to retrieve JWT authorization...")
    status, login1 = make_request(f"{BASE_URL}/auth/login", "POST", {
        "email": email1,
        "password": "testpassword123"
    })
    assert status == 200, f"Failed User 1 login: {status}"
    token1 = login1["access_token"]
    headers1 = {"Authorization": f"Bearer {token1}"}

    # 2b. Fetch User 1 profile to obtain UUID
    status, profile1 = make_request(f"{BASE_URL}/auth/me", "GET", headers=headers1)
    assert status == 200
    user1_uuid = profile1["id"]
    print(f"User 1 UUID: {user1_uuid}")

    # 3. Login as User 2
    status, login2 = make_request(f"{BASE_URL}/auth/login", "POST", {
        "email": email2,
        "password": "testpassword123"
    })
    assert status == 200
    token2 = login2["access_token"]
    headers2 = {"Authorization": f"Bearer {token2}"}
    
    status, profile2 = make_request(f"{BASE_URL}/auth/me", "GET", headers=headers2)
    assert status == 200
    user2_uuid = profile2["id"]

    # 4. Submit claim for fact check
    print("\n4. Submitting claims for AI fact check...")
    claim_text = "Standard maritime security agreement has been signed between the trilateral nations."
    payload = {"input_text_url": claim_text}
    
    status, fact_check_resp = make_request(f"{BASE_URL}/fact-check", "POST", payload, headers=headers1)
    print(f"Status: {status}")
    print(f"Response: {json.dumps(fact_check_resp, indent=2)}")
    
    assert status == 201, f"Expected 201, got {status}"
    assert fact_check_resp["status"] == "completed"
    assert fact_check_resp["input"] == claim_text
    assert "result" in fact_check_resp
    assert "explanation" in fact_check_resp
    assert "credibility_score" in fact_check_resp["explanation"]
    assert "summary" in fact_check_resp["explanation"]
    
    check_id = fact_check_resp["id"]

    # 5. Fetch single fact-check details by ID
    print("\n5. Fetching details of fact-check check_id...")
    status, check_detail = make_request(f"{BASE_URL}/fact-check/{check_id}", "GET", headers=headers1)
    assert status == 200
    assert check_detail["id"] == check_id
    assert check_detail["input"] == claim_text

    # 6. Verify User 2 cannot access User 1's fact check (Access Control)
    print("\n6. Testing access control: User 2 requesting User 1's fact check (should fail)...")
    status, err_resp = make_request(f"{BASE_URL}/fact-check/{check_id}", "GET", headers=headers2)
    print(f"Status: {status}")
    print(f"Response: {err_resp}")
    assert status == 403, f"Expected 403 Forbidden, got {status}"
    print("OK - Access control validated successfully!")

    # 7. Check user history retrieval
    print("\n7. Fetching User 1 fact-checking history list...")
    status, history_resp = make_request(f"{BASE_URL}/fact-check/users/{user1_uuid}", "GET", headers=headers1)
    assert status == 200
    assert history_resp["user_id"] == user1_uuid
    assert len(history_resp["requests"]) >= 1
    assert history_resp["requests"][0]["id"] == check_id
    print(f"History list contains the submitted claim! Count: {len(history_resp['requests'])}")

    # 8. Verify User 2 cannot fetch User 1's history list
    print("\n8. Testing access control: User 2 requesting User 1's history (should fail)...")
    status, err_resp2 = make_request(f"{BASE_URL}/fact-check/users/{user1_uuid}", "GET", headers=headers2)
    print(f"Status: {status}")
    print(f"Response: {err_resp2}")
    assert status == 403, f"Expected 403, got {status}"
    print("OK - History access control validated successfully!")

    print("\n=== ALL FACT-CHECK INTEGRATION TESTS COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    asyncio.run(run_fact_check_test())
