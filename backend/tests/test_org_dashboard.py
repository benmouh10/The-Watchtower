"""Enterprise org dashboard: creation, seats, membership, usage rollup,
competitive-intelligence topics."""

import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_org(client, headers, name="Acme Analytics"):
    resp = await client.post("/api/v1/org", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_create_org_and_dashboard(client, auth):
    identity = await auth()
    org_id = await _create_org(client, identity["headers"])

    dash = await client.get("/api/v1/org", headers=identity["headers"])
    assert dash.status_code == 200, dash.text
    body = dash.json()
    assert body["id"] == org_id
    assert body["name"] == "Acme Analytics"
    assert body["plan"] == "ENTERPRISE"
    assert body["seats"]["used"] == 1
    assert body["seats"]["limit"] is not None
    assert body["current_user_id"] == identity["user_id"]
    assert [m["role"] for m in body["members"]] == ["owner"]


async def test_second_org_conflict(client, auth):
    identity = await auth()
    await _create_org(client, identity["headers"])
    resp = await client.post(
        "/api/v1/org", json={"name": "Second Org"}, headers=identity["headers"]
    )
    assert resp.status_code == 409


async def test_add_member_seat_limit_monkeypatched(client, auth, monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "ORG_MAX_SEATS", 1)
    owner = await auth()
    await _create_org(client, owner["headers"])
    other = await auth()
    resp = await client.post(
        "/api/v1/org/members",
        json={"email": other["email"], "role": "member"},
        headers=owner["headers"],
    )
    assert resp.status_code == 409


async def test_member_lifecycle_and_role_gates(client, auth):
    owner = await auth()
    helper = await auth()
    await _create_org(client, owner["headers"])

    added = await client.post(
        "/api/v1/org/members",
        json={"email": helper["email"], "role": "member"},
        headers=owner["headers"],
    )
    assert added.status_code == 201, added.text
    assert added.json()["role"] == "member"

    promoted = await client.put(
        f"/api/v1/org/members/{helper['user_id']}",
        json={"role": "admin"},
        headers=owner["headers"],
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    members = (await client.get("/api/v1/org/members", headers=owner["headers"])).json()
    roles = {m["user_id"]: m["role"] for m in members["members"]}
    assert roles[helper["user_id"]] == "admin"

    added_removed = await client.delete(
        f"/api/v1/org/members/{helper['user_id']}", headers=owner["headers"]
    )
    assert added_removed.status_code == 200
    assert added_removed.json()["removed"] is True


async def test_member_cannot_modify(client, auth):
    owner = await auth()
    helper = await auth()
    await _create_org(client, owner["headers"])
    await client.post(
        "/api/v1/org/members",
        json={"email": helper["email"], "role": "member"},
        headers=owner["headers"],
    )

    forbidden = await client.post(
        "/api/v1/org/members",
        json={"email": owner["email"], "role": "member"},
        headers=helper["headers"],
    )
    assert forbidden.status_code == 403

    other = await auth()
    resp = await client.post(
        "/api/v1/org/members",
        json={"email": other["email"], "role": "member"},
        headers=helper["headers"],
    )
    assert resp.status_code == 403


async def test_owner_cannot_be_removed_or_demoted(client, auth):
    owner = await auth()
    helper = await auth()
    await _create_org(client, owner["headers"])
    await client.post(
        "/api/v1/org/members",
        json={"email": helper["email"], "role": "admin"},
        headers=owner["headers"],
    )

    demote = await client.put(
        f"/api/v1/org/members/{owner['user_id']}",
        json={"role": "member"},
        headers=helper["headers"],
    )
    assert demote.status_code in (403, 422)

    remove = await client.delete(
        f"/api/v1/org/members/{owner['user_id']}", headers=helper["headers"]
    )
    assert remove.status_code in (403, 422)


async def test_org_usage_rollup(client, auth):
    from sqlalchemy import select

    from src.core.database import AsyncSessionFactory
    from src.data.entities.models import UsageEvent

    owner = await auth()
    helper = await auth()
    await _create_org(client, owner["headers"])
    await client.post(
        "/api/v1/org/members",
        json={"email": helper["email"], "role": "member"},
        headers=owner["headers"],
    )

    async with AsyncSessionFactory() as db:
        db.add(UsageEvent(user_id=helper["user_id"], meter="chat", quantity=5))
        db.add(UsageEvent(user_id=owner["user_id"], meter="fact_check", quantity=2))
        await db.commit()

    resp = await client.get("/api/v1/org/usage", headers=owner["headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meters"] == {"chat": 5, "fact_check": 2}
    assert body["members"] == 2


async def test_competitor_topics(client, auth):
    owner = await auth()
    await _create_org(client, owner["headers"])

    created = await client.post(
        "/api/v1/org/competitors",
        json={"competitor_name": "RivalCo", "keywords": ["pricing", "roadmap"]},
        headers=owner["headers"],
    )
    assert created.status_code == 201, created.text
    competitor_id = created.json()["id"]

    updated = await client.post(
        "/api/v1/org/competitors",
        json={"competitor_name": "RivalCo", "keywords": ["pricing", "hiring"], "is_active": False},
        headers=owner["headers"],
    )
    assert updated.status_code == 201
    assert updated.json()["id"] == competitor_id
    assert updated.json()["keywords"] == ["pricing", "hiring"]
    assert updated.json()["is_active"] is False

    listed = (await client.get("/api/v1/org/competitors", headers=owner["headers"])).json()
    assert len(listed["competitors"]) == 1

    removed = await client.delete(
        f"/api/v1/org/competitors/{competitor_id}", headers=owner["headers"]
    )
    assert removed.status_code == 200
    listed = (await client.get("/api/v1/org/competitors", headers=owner["headers"])).json()
    assert listed["competitors"] == []


async def test_no_org_returns_404(client, auth):
    identity = await auth()
    resp = await client.get("/api/v1/org", headers=identity["headers"])
    assert resp.status_code == 404