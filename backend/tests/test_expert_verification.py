"""Roadmap item 7: admin-granted expert verification on comments."""
import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

from sqlalchemy import select, update

from src.core.database import AsyncSessionFactory
from src.data.entities.models import Event, User, UserRole


async def _make_event(title: str = "Expert test event") -> uuid.UUID:
    async with AsyncSessionFactory() as db:
        evt = Event(title=title)
        db.add(evt)
        await db.commit()
        event_id = evt.id
    return event_id


async def _make_admin(auth):
    identity = await auth()
    async with AsyncSessionFactory() as db:
        await db.execute(
            update(User).where(User.id == identity["user_id"]).values(role="ADMIN")
        )
        await db.commit()
    return identity


async def _make_writer(auth):
    identity = await auth()
    target = identity["user_id"]
    return identity


async def _post_comment(client, headers, event_id, content="A considered opinion"):
    return await client.post(
        f"/api/v1/comments/events/{event_id}",
        json={"content": content},
        headers=headers,
    )


async def test_expert_badge_not_present_by_default(client, auth):
    identity = await auth()
    event_id = await _make_event()
    r = await _post_comment(client, identity["headers"], event_id)
    assert r.status_code == 201
    assert r.json()["expert_badge"] is None

    listing = await client.get(f"/api/v1/comments/events/{event_id}")
    assert listing.status_code == 200
    assert listing.json()["comments"][0]["expert_badge"] is None


async def test_admin_grants_badge_then_comment_renders_it(client, auth, admin):
    writer = await auth()
    adm = await _make_admin(auth)
    event_id = await _make_event()

    grant = await client.post(
        "/api/v1/admin/expert-verifications",
        json={"user_id": str(writer["user_id"]), "badge_title": "Verified economist"},
        headers=adm["headers"],
    )
    assert grant.status_code == 201, grant.text
    assert grant.json()["badge_title"] == "Verified economist"

    listing = await client.get(f"/api/v1/comments/events/{event_id}")
    assert listing.status_code == 200

    r = await _post_comment(client, writer["headers"], event_id)
    assert r.status_code == 201
    badge = r.json()["expert_badge"]
    assert badge == {
        "label": "Expert·Verified economist",
        "tone": "violet",
        "detail": "Verified expert, granted by an administrator",
    }

    listing = await client.get(f"/api/v1/comments/events/{event_id}")
    body = listing.json()
    assert body["comments"][0]["expert_badge"] is not None
    assert body["comments"][0]["expert_badge"]["label"] == "Expert·Verified economist"


async def test_grant_is_exclusive_and_revokable(client, auth, admin):
    writer = await auth()
    adm = await _make_admin(auth)
    event_id = await _make_event()

    headers = adm["headers"]
    await client.post(
        "/api/v1/admin/expert-verifications",
        json={"user_id": str(writer["user_id"]), "badge_title": "First title"},
        headers=headers,
    )
    resp = await client.post(
        "/api/v1/admin/expert-verifications",
        json={"user_id": str(writer["user_id"]), "badge_title": "Second title"},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["badge_title"] == "Second title"

    listing = await client.get("/api/v1/admin/expert-verifications", headers=headers)
    matches = [v for v in listing.json()["verifications"] if v["user_id"] == str(writer["user_id"])]
    assert len(matches) == 1
    assert matches[0]["badge_title"] == "Second title"

    r = await _post_comment(client, writer["headers"], event_id)
    assert r.json()["expert_badge"]["label"] == "Expert·Second title"

    revoke = await client.delete(
        f"/api/v1/admin/expert-verifications/{writer['user_id']}", headers=headers
    )
    assert revoke.status_code == 200
    assert revoke.json()["revoked"] is True

    r2 = await _post_comment(client, writer["headers"], event_id)
    assert r2.json()["expert_badge"] is None


async def test_grant_guards(client, admin):
    adm = await _make_admin(auth=admin)
    headers = adm["headers"]

    missing = await client.post(
        "/api/v1/admin/expert-verifications",
        json={"user_id": str(uuid.uuid4()), "badge_title": "Verified"},
        headers=headers,
    )
    assert missing.status_code == 404

    empty = await client.post(
        "/api/v1/admin/expert-verifications",
        json={"user_id": str(adm["user_id"]), "badge_title": "  "},
        headers=headers,
    )
    assert empty.status_code == 422

    unauthorized = await client.post(
        "/api/v1/admin/expert-verifications",
        json={"user_id": str(uuid.uuid4()), "badge_title": "x"},
    )
    assert unauthorized.status_code in (401, 403)


async def test_non_admin_cannot_grant(client, auth):
    identity = await auth()
    r = await client.post(
        "/api/v1/admin/expert-verifications",
        json={"user_id": str(identity["user_id"]), "badge_title": "x"},
        headers=identity["headers"],
    )
    assert r.status_code == 403