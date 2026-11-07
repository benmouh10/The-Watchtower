"""Web Push automation on top of the digital-comprehensive-brief engine.

Subscriptions are per-browser (one row per endpoint), owned by a user. Every
digest run enqueues one delivery row per *existing* subscription; the
(digest_id, subscription_id) unique constraint makes the queue immune to
double-sends. A delivery worker (or an admin-triggered flush) drains pending
rows through the config-gated push sender described in webpush_sender.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.data.entities.models import (
    Digest, Event, EventFollow, PushDelivery, PushSubscription, User,
)
from src.services.webpush_sender import (
    PushGoneError,
    build_payload_for_digest,
    build_payload_for_event,
    build_push_sender,
)

logger = logging.getLogger("src.services.push_service")

PENDING = "pending"
SENT = "sent"
LOGGED = "logged"
FAILED = "failed"


def validate_subscription(endpoint: str, p256dh: str, auth: str, user_agent: Optional[str]) -> None:
    """Structural validation for an incoming PushSubscription object."""
    if not endpoint or not isinstance(endpoint, str):
        raise ValueError("endpoint is required and must be a URL")
    if not (endpoint.startswith("https://") or endpoint.startswith("http://")):
        raise ValueError("endpoint must be an http(s) URL")
    if not p256dh or not isinstance(p256dh, str):
        raise ValueError("p256dh is required")
    if not auth or not isinstance(auth, str):
        raise ValueError("auth is required")


class PushService:
    def __init__(self, db: AsyncSession):
        self._db = db

    # ── device subscriptions ───────────────────────────────────────────────────
    async def register(self, *, user_id, endpoint: str, p256dh: str, auth: str,
                       user_agent: Optional[str]) -> PushSubscription:
        validate_subscription(endpoint, p256dh, auth, user_agent)
        sub = PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent,
        )
        self._db.add(sub)
        try:
            await self._db.commit()
        except IntegrityError:
            await self._db.rollback()
            raise ValueError("endpoint already registered")
        await self._db.refresh(sub)
        return sub

    async def list_for(self, user_id) -> List[PushSubscription]:
        result = await self._db.execute(
            select(PushSubscription)
            .where(PushSubscription.user_id == user_id)
            .order_by(PushSubscription.created_at.asc())
        )
        return list(result.scalars())

    async def remove(self, *, user_id, subscription_id) -> bool:
        """Remove a device subscription and cancel its still-pending sends.

        Returns True when the subscription existed and belonged to the user.
        Pending (never-sent) deliveries are removed so an unregistered device
        stops receiving queued pushes; already-sent rows stay as audit.
        """
        sub = (
            await self._db.execute(
                select(PushSubscription).where(
                    PushSubscription.id == subscription_id,
                    PushSubscription.user_id == user_id,
                )
            )
        ).scalars().first()
        if sub is None:
            return False
        await self._db.execute(
            PushDelivery.__table__.delete().where(
                PushDelivery.subscription_id == sub.id,
                PushDelivery.delivery_status == PENDING,
            )
        )
        await self._db.delete(sub)
        await self._db.commit()
        return True

    async def all_subscriptions(self) -> List[PushSubscription]:
        result = await self._db.execute(select(PushSubscription).order_by(PushSubscription.created_at.asc()))
        return list(result.scalars())

    async def targeted_subscriptions_for(self, digest: Digest) -> List[PushSubscription]:
        """The subscriptions whose owners should be notified about `digest`.

        Registering a device is the opt-in for push; a user who has not set any
        preferred topics receives digest pushes by default. A user who *has*
        set topics only receives them when the digest covers at least one of
        those topics (case-insensitive), so preferences always narrow — they
        can never widen the blast radius.
        """
        subs = await self.all_subscriptions()
        if not subs:
            return []
        owners = list({s.user_id for s in subs})
        pref_rows = await self._db.execute(
            select(User.id, User.preferred_topics).where(User.id.in_(owners))
        )
        user_topics = {uid: [t for t in (topics or []) if t] for uid, topics in pref_rows.all()}

        event_topics = set()
        raw_ids = digest.event_ids if isinstance(digest.event_ids, list) else (digest.event_ids or [])
        if raw_ids:
            import uuid as _uuid

            parsed = []
            for item in raw_ids:
                try:
                    parsed.append(_uuid.UUID(str(item)))
                except (ValueError, TypeError):
                    continue
            if parsed:
                topic_rows = await self._db.execute(
                    select(Event.topic).where(Event.id.in_(parsed))
                )
                event_topics = {t.upper() for (t,) in topic_rows.all() if t}

        def _wants(sub: PushSubscription) -> bool:
            prefs = {t.upper() for t in (user_topics.get(sub.user_id) or []) if t}
            if not prefs:
                return True
            return bool(event_topics and (prefs & event_topics))

        return [s for s in subs if _wants(s)]

    # ── enqueue / flush ────────────────────────────────────────────────────────
    async def enqueue_digest(self, digest: Digest) -> int:
        """Queue one delivery per targeted device for `digest`.

        Idempotent by construction: the (digest_id, subscription_id) unique
        constraint makes a duplicate insert a no-op. Returns how many rows this
        call created.
        """
        subs = await self.targeted_subscriptions_for(digest)
        if not subs:
            return 0
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(PushDelivery).values(
            [
                {"digest_id": digest.id, "subscription_id": s.id}
                for s in subs
            ]
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["digest_id", "subscription_id"])
        result = await self._db.execute(stmt.returning(PushDelivery.id))
        created = len(list(result.all()))
        await self._db.commit()
        return created

    async def enqueue_follow_event(self, event: Event) -> int:
        """Queue one delivery per device whose user follows `event`.

        A follow is the user's explicit opt-in, so every push subscription they
        own is a valid target (there is no digest-category preference to
        consult). Idempotent per device: the partial unique index on
        (event_id, subscription_id) turns a re-enqueue into a no-op.
        """
        follow_stmt = select(EventFollow.user_id).where(EventFollow.event_id == event.id)
        follower_ids = (await self._db.execute(follow_stmt)).scalars().all()
        if not follower_ids:
            return 0

        sub_stmt = select(PushSubscription).where(PushSubscription.user_id.in_(follower_ids))
        subs = (await self._db.execute(sub_stmt)).scalars().all()
        if not subs:
            return 0

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(PushDelivery).values(
            [
                {"event_id": event.id, "subscription_id": sub.id}
                for sub in subs
            ]
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["event_id", "subscription_id"],
            index_where=text("event_id IS NOT NULL"),
        )
        result = await self._db.execute(stmt.returning(PushDelivery.id))
        created = len(list(result.all()))
        await self._db.commit()
        return created

    async def pending_deliveries(self, limit: int = 50) -> List[Tuple[PushDelivery, Optional[Digest], PushSubscription, Optional[Event]]]:
        """Pending rows joined with their digest (if any) and event (if any).

        A delivery is exactly one kind: digest_id set (digest push) or
        event_id set (followed-event push). The outer joins keep both kinds in
        the same queue drained by one worker.
        """
        result = await self._db.execute(
            select(PushDelivery, Digest, PushSubscription, Event)
            .join(PushSubscription, PushDelivery.subscription_id == PushSubscription.id)
            .outerjoin(Digest, PushDelivery.digest_id == Digest.id)
            .outerjoin(Event, PushDelivery.event_id == Event.id)
            .where(PushDelivery.delivery_status == PENDING)
            .order_by(PushDelivery.created_at.asc(), PushDelivery.id.asc())
            .limit(limit)
        )
        return list(result.all())

    async def flush_pending_deliveries(self, limit: int = 50) -> dict:
        sender = build_push_sender()
        rows = await self.pending_deliveries(limit)
        counts = {PENDING: 0, SENT: 0, LOGGED: 0, FAILED: 0}
        now = datetime.now(timezone.utc)
        for delivery, digest, sub, event in rows:
            try:
                if digest is not None:
                    payload = build_payload_for_digest(
                        title=digest.title,
                        event_count=digest.event_count,
                        base_url=settings.APP_BASE_URL,
                        digest_id=str(digest.id),
                    )
                else:
                    payload = build_payload_for_event(
                        title=event.title if event else "Followed event",
                        summary=event.summary or "" if event else "",
                        event_id=str(event.id) if event else str(delivery.event_id),
                        base_url=settings.APP_BASE_URL,
                    )
                await sender.send(
                    endpoint=sub.endpoint,
                    p256dh=sub.p256dh,
                    auth=sub.auth,
                    payload=payload,
                )
                delivery.delivery_status = SENT if settings.PUSH_ENABLED else LOGGED
                delivery.sent_at = now
                delivery.error_text = None
            except PushGoneError as exc:
                # The push service no longer knows this endpoint (404/410 Gone).
                # Retrying it forever would be noise; forget the device, which
                # also removes every still-pending delivery for it (the
                # subscription FK cascades).
                await self._db.execute(
                    PushSubscription.__table__.delete().where(PushSubscription.id == sub.id)
                )
                logger.info("Push device gone (410/404); removed subscription %s: %s", sub.id, exc)
                continue
            except Exception as exc:
                delivery.delivery_status = FAILED
                delivery.error_text = str(exc)
            counts[delivery.delivery_status] = counts.get(delivery.delivery_status, 0) + 1
            await self._db.flush()
        await self._db.commit()
        counts[PENDING] = len(await self.pending_deliveries(limit))
        return counts

    async def delivery_counts(self) -> dict:
        result = await self._db.execute(
            select(PushDelivery.delivery_status, func.count(PushDelivery.id))
            .group_by(PushDelivery.delivery_status)
        )
        counts = {PENDING: 0, SENT: 0, LOGGED: 0, FAILED: 0}
        for status_name, count in result.all():
            counts[status_name] = count
        return counts


async def run_push_worker() -> None:
    """Background drain: flush pending pushes on a configurable cadence.

    Started from the lifespan whenever DIGEST_ENABLED or PUSH_ENABLED is on
    (with both off there can be no digest to push and no relay to use). Fires
    once immediately on boot, then sleeps PUSH_POLL_SECONDS between drains.
    """
    poll_seconds = max(5, int(getattr(settings, "PUSH_POLL_SECONDS", 60) or 60))
    from src.core.database import get_db

    while True:
        try:
            async for db in get_db():
                try:
                    counts = await PushService(db).flush_pending_deliveries()
                    if any(counts.get(k) for k in (SENT, LOGGED, FAILED)):
                        logger.info("Push flush: %s", counts)
                finally:
                    await db.close()
                break
        except Exception:
            logger.error("Push flush failed", exc_info=True)
        await asyncio.sleep(poll_seconds)