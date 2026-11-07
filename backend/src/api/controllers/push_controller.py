"""
GlobeLens AI — PushController
GET /push/config | GET /push/following | POST/DELETE /push/following/{topic}
POST /push/subscriptions | GET /push/subscriptions | DELETE /push/subscriptions/{id} | POST /push/test
Self-service Web Push device management. One subscription per browser endpoint;
unregistering the device cancels its still-pending pushes.
"""
import uuid as _uuid  # noqa: F401 (used in unregister)
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from src.api.controllers.auth_controller import get_current_user
from src.core.config import settings
from src.core.database import get_db
from src.data.entities.models import User
from src.services.push_service import PushService
from src.services.webpush_sender import build_push_sender
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class RegisterSubscriptionRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    user_agent: Optional[str] = None


def _serialize(sub) -> dict:
    return {
        "id": str(sub.id),
        "endpoint": sub.endpoint,
        "p256dh": sub.p256dh,
        "auth": sub.auth,
        "user_agent": sub.user_agent,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


def _normalize_topic(topic: str) -> str:
    """Normalize a topic string for follow/unfollow. Free-form (events carry
    free-form topics) but bounded, and case-insensitive follow is intended."""
    cleaned = " ".join((topic or "").split()).upper()
    if not cleaned or len(cleaned) > 50:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="topic must be a non-empty string of at most 50 characters",
        )
    return cleaned


@router.get("/config", summary="Push configuration for browser clients")
async def push_config():
    """Public, unauthenticated: the browser needs this before it can subscribe.

    `vapid_public_key` is None while PUSH_ENABLED is off or the keypair is not
    configured, which is the honest signal that in-app pushes cannot be sent.
    """
    return {
        "enabled": settings.PUSH_ENABLED,
        "vapid_public_key": settings.VAPID_PUBLIC_KEY or None,
        "subject": settings.PUSH_SUBJECT,
    }


@router.get("/following", summary="List the topics the current user follows")
async def list_followed_topics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.refresh(current_user)
    topics = [t for t in (current_user.preferred_topics or []) if t] or []
    return {"topics": topics, "count": len(topics)}


@router.post("/following/{topic}", summary="Follow (opt in to alerts for) a topic")
async def follow_topic(
    topic: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    normalized = _normalize_topic(topic)
    await db.refresh(current_user)
    topics = [t for t in (current_user.preferred_topics or []) if t] or []
    changed = normalized not in topics
    if changed:
        current_user.preferred_topics = topics + [normalized]
        await db.commit()
    return {"topic": normalized, "following": True, "changed": changed}


@router.delete("/following/{topic}", summary="Unfollow a topic")
async def unfollow_topic(
    topic: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    normalized = _normalize_topic(topic)
    await db.refresh(current_user)
    topics = [t for t in (current_user.preferred_topics or []) if t] or []
    changed = normalized in topics
    if changed:
        current_user.preferred_topics = [t for t in topics if t != normalized]
        await db.commit()
    return {"topic": normalized, "following": False, "changed": changed}


@router.post("/subscriptions", summary="Register a Web Push subscription for the current user")
async def register_subscription(
    body: RegisterSubscriptionRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        sub = await PushService(db).register(
            user_id=current_user.id,
            endpoint=body.endpoint,
            p256dh=body.p256dh,
            auth=body.auth,
            user_agent=body.user_agent or request.headers.get("user-agent"),
        )
    except ValueError as exc:
        message = str(exc)
        code = status.HTTP_409_CONFLICT if "already registered" in message else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=message)
    return _serialize(sub)


@router.get("/subscriptions", summary="List the current user's device subscriptions")
async def list_subscriptions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    subs = await PushService(db).list_for(user_id=current_user.id)
    return {"subscriptions": [_serialize(s) for s in subs], "count": len(subs)}


@router.delete("/subscriptions/{subscription_id}", summary="Unregister a device subscription")
async def unregister_subscription(
    subscription_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        sub_uuid = _uuid.UUID(str(subscription_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid subscription id")
    removed = await PushService(db).remove(user_id=current_user.id, subscription_id=sub_uuid)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such device subscription for this user.",
        )
    return {"removed": True}


@router.post("/test", summary="Send a test notification to the current user's devices")
async def send_test(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    subs = await PushService(db).list_for(user_id=current_user.id)
    if not subs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No device subscriptions for this user.",
        )
    sender = build_push_sender()
    succeeded = 0
    failed = 0
    for sub in subs:
        try:
            await sender.send(
                endpoint=sub.endpoint,
                p256dh=sub.p256dh,
                auth=sub.auth,
                payload={"title": "GlobeLens AI test", "body": "This is a test notification.", "tag": "test"},
            )
            succeeded += 1
        except Exception:
            failed += 1
    return {
        "mode": "vapid" if settings.PUSH_ENABLED else "log",
        "delivered": succeeded,
        "failed": failed,
        "devices": len(subs),
    }