"""
GlobeLens AI — NewsletterController
POST/DELETE /newsletter/subscribe  |  GET /newsletter/status  |  GET /newsletter/config
POST /newsletter/unsubscribe (one-click via emailed token)
Self-service digest email subscription. One row per user; unsubscribe ends the
subscription and cancels its still-pending sends rather than pretending to stop
after queueing. The one-click route is public because it is driven by a signed
token embedded in the email, never by a session.
"""
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.controllers.auth_controller import get_current_user
from src.core.config import settings
from src.core.database import get_db
from src.data.entities.models import User
from src.services.newsletter_service import FREQUENCIES, NewsletterService, verify_unsubscribe_token
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class UnsubscribeTokenRequest(BaseModel):
    token: str


class NewsletterPreferencesUpdate(BaseModel):
    digest_frequency: Optional[str] = Field(None, description="daily | weekly | off")
    topics: Optional[List[str]] = Field(
        None,
        description="Newsletter-scoped topic interests; a brief must cover at "
                    "least one to be delivered. Clears any account-level fallback "
                    "within this newsletter when set.",
    )
    preferred_digest_hour: Optional[int] = Field(None, ge=0, le=23)


def _subscription_payload(sub, latest: Optional[dict]) -> dict:
    base = {
        "subscribed": bool(sub) and sub.unsubscribed_at is None,
        "subscriber_since": sub.created_at.isoformat() if sub else None,
        "unsubscribed_at": sub.unsubscribed_at.isoformat() if sub and sub.unsubscribed_at else None,
        "digest_frequency": (sub.digest_frequency if sub else "daily"),
        "topics": (list(sub.topics) if sub and sub.topics else []),
        "preferred_digest_hour": (sub.preferred_digest_hour if sub else 6),
        "last_email_sent_at": (
            sub.last_email_sent_at.isoformat()
            if sub and sub.last_email_sent_at
            else None
        ),
        "frequencies": list(FREQUENCIES),
    }
    if latest:
        base["last_digest"] = latest
    return base


@router.get("/config", summary="Newsletter delivery configuration (public)")
async def newsletter_config():
    """Honest capability read for the settings UI.

    `delivered_via` is downstream-true: when EMAIL_ENABLED is off (the default)
    every send is captured as 'logged', never claimed as emailed.
    """
    return {
        "enabled": settings.EMAIL_ENABLED,
        "digest_enabled": settings.DIGEST_ENABLED,
        "delivered_via": "smtp" if settings.EMAIL_ENABLED else "log",
        "poll_seconds": settings.EMAIL_POLL_SECONDS,
    }


@router.post("/subscribe", summary="Subscribe the current user to the digest newsletter")
async def subscribe(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await NewsletterService(db).subscribe(user_id=current_user.id)
    latest = await _latest_digest_summary(db)
    return _subscription_payload(sub, latest)


@router.delete("/subscribe", summary="Unsubscribe the current user from the digest newsletter")
async def unsubscribe(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ended = await NewsletterService(db).unsubscribe(user_id=current_user.id)
    if not ended:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active newsletter subscription to end.",
        )
    return {"subscribed": False, "unsubscribed_at": _now_iso()}


@router.post("/unsubscribe", summary="One-click unsubscribe via the emailed token (public)")
async def unsubscribe_with_token(
    body: UnsubscribeTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """End the subscription named by a bearer token from the email footer.

    The token is the credential: no logged-in session is required. Valid but
    already-unsubscribed tokens answer 200 with the current (unsubscribed)
    state, so a retried click is never an error.
    """
    try:
        user_id = verify_unsubscribe_token(body.token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    service = NewsletterService(db)
    subscription = await service.subscription_for(user_id)
    if subscription is None or subscription.unsubscribed_at is not None:
        return {"subscribed": False, "reason": "already_unsubscribed", "unsubscribed_at": None}
    await service.unsubscribe(user_id)
    return {"subscribed": False, "unsubscribed_at": _now_iso()}


@router.get("/status", summary="Newsletter subscription status for the current user")
async def newsletter_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await NewsletterService(db).subscription_for(user_id=current_user.id)
    latest = await _latest_digest_summary(db)
    return _subscription_payload(sub, latest)


@router.get("/settings", summary="Newsletter preferences for the current user")
async def newsletter_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the user's newsletter preferences (frequency, topics, delivery hour)."""
    sub = await NewsletterService(db).subscription_for(user_id=current_user.id)
    payload = _subscription_payload(sub, None)
    payload.pop("last_digest", None)
    return payload


@router.put("/settings", summary="Update newsletter preferences for the current user")
async def newsletter_settings_update(
    body: NewsletterPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update digest frequency, newsletter topic interests, and delivery hour.

    Setting preferences on a reader with no subscription subscribes them (daily
    by default); updating preferences on an unsubscribed reader re-activates
    the subscription, because choosing delivery details is an opt-in signal.
    """
    try:
        sub = await NewsletterService(db).set_preferences(
            user_id=current_user.id,
            frequency=body.digest_frequency,
            topics=body.topics,
            preferred_digest_hour=body.preferred_digest_hour,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    payload = _subscription_payload(sub, None)
    payload.pop("last_digest", None)
    return payload


async def _latest_digest_summary(db) -> Optional[dict]:
    from src.data.entities.models import Digest
    from sqlalchemy import select

    digest = (
        await db.execute(select(Digest).order_by(Digest.created_at.desc()).limit(1))
    ).scalars().first()
    if digest is None:
        return None
    return {
        "id": str(digest.id),
        "title": digest.title,
        "generated_at": digest.created_at.isoformat(),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()