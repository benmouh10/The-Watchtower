"""
GlobeLens AI — OutboundWebhookController

    POST   /api/v1/webhooks         register an endpoint
    GET    /api/v1/webhooks         list your endpoints + recent deliveries
    POST   /api/v1/webhooks/{id}/test   send a test ping to one endpoint
    DELETE /api/v1/webhooks/{id}    deactivate an endpoint (kept as audit)
    GET    /api/v1/webhooks/rules   describe which event types are receivable

Endpoints are scoped per user and capped at WEBHOOK_MAX_ENDPOINTS. Each
registration carries its own signing secret; payloads are signed with
HMAC-SHA256 so receivers can verify authenticity.
"""
import secrets
import uuid as uuid_module
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import get_current_user
from src.core.config import settings
from src.core.database import get_db
from src.data.entities.models import OutboundWebhook, WebhookDelivery, User
from src.services.webhook_service import (
    EVENT_TYPE_PROCESSED,
    build_signature,
)

router = APIRouter()


class RegisterWebhookRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=1024)
    event_types: Optional[List[str]] = Field(
        None, description="Event types that fire this hook. Omit/empty = every processed event."
    )


def _serialize(webhook: OutboundWebhook) -> dict:
    return {
        "id": str(webhook.id),
        "url": webhook.url,
        "event_types": list(webhook.event_types or []),
        "is_active": webhook.is_active,
        "created_at": webhook.created_at.isoformat() if webhook.created_at else None,
    }


def _validate_event_types(event_types) -> List[str]:
    cleaned = sorted({t for t in (event_types or []) if t and t.strip()})
    for item in cleaned:
        if item != EVENT_TYPE_PROCESSED:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported event type: {item!r}. Supported: {EVENT_TYPE_PROCESSED!r}",
            )
    return cleaned


@router.post("", status_code=status.HTTP_201_CREATED, summary="Register an outbound webhook endpoint")
async def register_webhook(
    payload: RegisterWebhookRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event_types = _validate_event_types(payload.event_types)
    endpoint_count = (await db.execute(
        select(OutboundWebhook).where(
            OutboundWebhook.user_id == current_user.id,
            OutboundWebhook.is_active.is_(True),
        )
    )).scalars().all()
    if len(endpoint_count) >= settings.WEBHOOK_MAX_ENDPOINTS:
        raise HTTPException(
            status_code=422,
            detail=f"Endpoint cap reached ({settings.WEBHOOK_MAX_ENDPOINTS} active webhooks).",
        )
    webhook = OutboundWebhook(
        user_id=current_user.id,
        url=payload.url.strip(),
        secret=secrets.token_urlsafe(48),
        event_types=event_types,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return {**_serialize(webhook), "secret": webhook.secret}


@router.get("", summary="List your webhook endpoints with delivery status")
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    webhooks = (await db.execute(
        select(OutboundWebhook)
        .where(OutboundWebhook.user_id == current_user.id)
        .order_by(OutboundWebhook.created_at.desc())
    )).scalars().all()
    recent = (await db.execute(
        select(WebhookDelivery)
        .join(OutboundWebhook, WebhookDelivery.webhook_id == OutboundWebhook.id)
        .where(OutboundWebhook.user_id == current_user.id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(50)
    )).scalars().all()
    by_webhook = {}
    for delivery in recent:
        by_webhook.setdefault(str(delivery.webhook_id), []).append(
            {
                "id": str(delivery.id),
                "event_id": str(delivery.event_id),
                "status": delivery.status,
                "attempts": delivery.attempts,
                "last_error": delivery.last_error,
                "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
            }
        )
    return {
        "items": [
            {**_serialize(w), "deliveries": by_webhook.get(str(w.id), [])}
            for w in webhooks
        ],
        "max_endpoints": settings.WEBHOOK_MAX_ENDPOINTS,
        "count": len(webhooks),
    }


@router.post("/{webhook_id}/test", summary="Send a test ping to one endpoint")
async def test_webhook(
    webhook_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    webhook = await _owned_webhook(db, current_user, webhook_id)
    now = datetime.now(timezone.utc)
    raw = b'{"event":"test.ping"}'
    signature = build_signature(webhook.secret, raw)
    import httpx

    headers = {
        "Content-Type": "application/json",
        "X-GlobeLens-Event": "test.ping",
        "X-GlobeLens-Signature": signature,
        "X-GlobeLens-Timestamp": str(int(now.timestamp())),
    }
    if not settings.WEBHOOK_ENABLED:
        return {"webhook_id": str(webhook.id), "mode": "log", "event": "test.ping"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook.url, content=raw, headers=headers)
        ok = 200 <= response.status_code < 300
        return {
            "webhook_id": str(webhook.id),
            "event": "test.ping",
            "mode": "live",
            "status_code": response.status_code,
            "delivered": ok,
        }
    except Exception as exc:
        return {
            "webhook_id": str(webhook.id),
            "event": "test.ping",
            "mode": "live",
            "delivered": False,
            "error": str(exc)[:300],
        }


@router.delete("/{webhook_id}", summary="Deactivate one of your webhook endpoints")
async def delete_webhook(
    webhook_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    webhook = await _owned_webhook(db, current_user, webhook_id)
    if webhook.is_active:
        webhook.is_active = False
        await db.commit()
        return {**_serialize(webhook), "deactivated": True}
    return {**_serialize(webhook), "deactivated": False}


@router.get("/rules", summary="Which webhook event types are receivable")
async def webhook_rules():
    return {
        "max_endpoints": settings.WEBHOOK_MAX_ENDPOINTS,
        "event_types": [EVENT_TYPE_PROCESSED],
        "signing": "HMAC-SHA256 over the raw body in X-GlobeLens-Signature",
    }


async def _owned_webhook(db: AsyncSession, current_user: User, webhook_id: str) -> OutboundWebhook:
    try:
        webhook_uuid = uuid_module.UUID(webhook_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid webhook id")
    webhook = (await db.execute(
        select(OutboundWebhook).where(
            OutboundWebhook.id == webhook_uuid,
            OutboundWebhook.user_id == current_user.id,
        )
    )).scalars().first()
    if webhook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return webhook