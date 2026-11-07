"""
Smoke tests proving the pytest harness talks to the live test stack.

These cover the widest, cheapest public surface: health, plans catalogue,
registration/login, and the events feed. If these pass, the fixtures
(client/auth plus schema create/truncate) are wired correctly.
"""

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"


async def test_public_events(client):
    r = await client.get("/api/v1/events")
    assert r.status_code == 200
    assert "total" in r.json() or "events" in r.json()


async def test_plans_catalogue(client):
    r = await client.get("/api/v1/plans")
    assert r.status_code == 200
    tiers = [p["plan"] for p in r.json()["tiers"]]
    assert tiers == ["free", "pro", "enterprise"]


async def test_register_login_me(client):
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Smoke User",
            "email": "smoke@example.com",
            "password": "testpassword123",
        },
    )
    assert reg.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "smoke@example.com", "password": "testpassword123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "smoke@example.com"


async def test_auth_fixture_bundle(auth):
    identity = await auth()
    assert identity["access_token"]
    assert identity["user_id"]
    assert identity["headers"]["Authorization"].startswith("Bearer ")


async def test_unauthenticated_not_allowed(client):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 403