"""
GlobeLens AI — Outbound webhook dispatcher.

Every PROCESSED event is fanned out to the active webhooks that asked for its
event type (an empty event_types list means "every event"). Queueing is
idempotent on (webhook_id, event_id). A background worker (or an admin-triggered
flush) drains the pending rows through HTTP POST, signing each payload with
HMAC-SHA256 over the raw body using the endpoint's secret. With WEBHOOK_ENABLED
false the drain records each delivery as 'logged' exactly like email/push log
mode, so the pipeline is always exercised without POSTing anywhere.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.data.entities.models import Event, OutboundWebhook, WebhookDelivery

logger = logging.getLogger("src.services.webhook_service")

PENDING = "pending"
SENT = "sent"
LOGGED = "logged"
FAILED = "failed"

EVENT_TYPE_PROCESSED = "event.created"


def build_signature(secret: str, raw_body: bytes) -> str:
    """Hex HMAC-SHA256 of the raw payload keyed with the endpoint secret."""
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def _next_attempt_at(attempts: int, now: datetime) -> datetime:
    delay = settings.WEBHOOK_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1))
    return now + timedelta(seconds=min(delay, 3600 * 8))


class WebhookService:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def queue_for_event(
        self, event: Event, event_type: str = EVENT_TYPE_PROCESSED, *, now: Optional[datetime] = None
    ) -> int:
        """Queue one delivery row per matching active webhook for `event`.

        A webhook matches when it is active and its event_types list is empty
        (subscribe to everything) or contains the event_type. Idempotent on the
        (webhook_id, event_id) unique constraint. Returns rows created.
        """
        rows = (
            await self._db.execute(
                select(OutboundWebhook).where(
                    OutboundWebhook.is_active.is_(True),
                )
            )
        ).scalars().all()
        matching = [
            w for w in rows if not w.event_types or event_type in (w.event_types or [])
        ]
        if not matching:
            return 0
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(WebhookDelivery).values(
            [
                {
                    "webhook_id": w.id,
                    "event_id": event.id,
                    "status": PENDING,
                    "next_attempt_at": now or datetime.now(timezone.utc),
                }
                for w in matching
            ]
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["webhook_id", "event_id"])
        result = await self._db.execute(stmt.returning(WebhookDelivery.id))
        created = len(list(result.all()))
        await self._db.commit()
        return created

    async def pending_deliveries(
        self, limit: int = 50, *, now: Optional[datetime] = None
    ) -> List[Tuple[WebhookDelivery, OutboundWebhook, Event]]:
        now = now or datetime.now(timezone.utc)
        result = await self._db.execute(
            select(WebhookDelivery, OutboundWebhook, Event)
            .join(OutboundWebhook, WebhookDelivery.webhook_id == OutboundWebhook.id)
            .join(Event, WebhookDelivery.event_id == Event.id)
            .where(
                WebhookDelivery.status == PENDING,
                or_(
                    WebhookDelivery.next_attempt_at.is_(None),
                    WebhookDelivery.next_attempt_at <= now,
                ),
            )
            .order_by(
                WebhookDelivery.next_attempt_at.asc().nulls_first(),
                WebhookDelivery.created_at.asc(),
                WebhookDelivery.id.asc(),
            )
            .limit(limit)
        )
        return list(result.all())

    async def flush_pending_deliveries(self, limit: int = 50, *, now: Optional[datetime] = None) -> dict:
        """POST (or, in log mode, record) up to `limit` due deliveries."""
        import httpx

        now = now or datetime.now(timezone.utc)
        rows = await self.pending_deliveries(limit, now=now)
        counts = {PENDING: 0, SENT: 0, LOGGED: 0, FAILED: 0}
        timeout = httpx.Timeout(10.0)
        for delivery, webhook, event in rows:
            if not webhook.is_active:
                delivery.status = FAILED
                delivery.last_error = "webhook endpoint deactivated"
                await self._db.flush()
                counts[status_key := delivery.status] = counts.get(status_key, 0) + 1
                continue
            try:
                payload_obj = self._event_payload(event)
                raw = json.dumps(payload_obj, separators=(",", ":")).encode()
                signature = build_signature(webhook.secret, raw)
                headers = {
                    "Content-Type": "application/json",
                    "X-GlobeLens-Event": EVENT_TYPE_PROCESSED,
                    "X-GlobeLens-Webhook-Id": str(delivery.id),
                    "X-GlobeLens-Signature": signature,
                    "X-GlobeLens-Timestamp": str(int(time.time())),
                }
                if settings.WEBHOOK_ENABLED:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        response = await client.post(
                            webhook.url, content=raw, headers=headers
                        )
                    if 200 <= response.status_code < 300:
                        delivery.status = SENT
                        delivery.sent_at = now
                        delivery.last_error = None
                    else:
                        raise RuntimeError(
                            f"receiver replied {response.status_code}: "
                            f"{response.text[:200]}"
                        )
                else:
                    logger.info(
                        "webhook [log] contract=%s url=%s payload=%s",
                        delivery.id, webhook.url, raw.decode()[:200],
                    )
                    delivery.status = LOGGED
                    delivery.sent_at = now
                    delivery.last_error = None
            except Exception as exc:
                delivery.attempts += 1
                if delivery.attempts >= settings.WEBHOOK_DELIVERY_MAX_ATTEMPTS:
                    delivery.status = FAILED
                else:
                    delivery.next_attempt_at = _next_attempt_at(
                        delivery.attempts, now
                    )
                delivery.last_error = str(exc)[:500]
            if delivery.status != PENDING:
                counts[delivery.status] = counts.get(delivery.status, 0) + 1
            else:
                counts[PENDING] = counts.get(PENDING, 0) + 1
            await self._db.flush()
        await self._db.commit()
        counts[PENDING] = len(await self.pending_deliveries(limit, now=now))
        return counts

    def _event_payload(self, event: Event) -> dict:
        return {
            "event": "event.created",
            "id": str(event.id),
            "topic": event.topic,
            "country": event.country,
            "title": event.title,
            "summary": event.summary,
            "latitude": event.latitude,
            "longitude": event.longitude,
            "importance_score": event.importance_score,
            "bias_lean": event.bias_lean.value if event.bias_lean else None,
            "status": event.status,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "updated_at": event.updated_at.isoformat() if event.updated_at else None,
        }

    async def delivery_counts(self) -> dict:
        result = await self._db.execute(
            select(WebhookDelivery.status, WebhookDelivery.id)
        )
        counts = {PENDING: 0, SENT: 0, LOGGED: 0, FAILED: 0}
        for status_key, _count in result.all():
            counts[status_key] = counts.get(status_key, 0) + 1
        return counts


async def run_webhook_worker() -> None:
    poll_seconds = max(5, int(getattr(settings, "WEBHOOK_POLL_SECONDS", 30) or 30))
    from src.core.database import get_db

    while True:
        try:
            async for db in get_db():
                try:
                    counts = await WebhookService(db).flush_pending_deliveries()
                    if any(counts.get(k) for k in (SENT, LOGGED, FAILED)):
                        logger.info("Webhook flush: %s", counts)
                finally:
                    await db.close()
                break
        except Exception:
            logger.error("Webhook flush failed", exc_info=True)
        await asyncio.sleep(poll_seconds)