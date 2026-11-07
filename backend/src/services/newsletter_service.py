"""Newsletter automation on top of the digital-comprehensive-brief engine.

Subscriptions are one row per user (unsubscribe ends rather than deletes, so
resubscribe restores the same row). Every digest run enqueues one delivery row
per *currently active* subscriber; the (digest_id, user_id) unique constraint
makes the queue immune to double-sends. A delivery worker (or an admin-triggered
flush) drains pending rows through the config-gated email sender.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from jose import jwt
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.data.entities.models import Digest, Event, NewsletterDelivery, NewsletterSubscription, User
from src.services.email_service import (
    build_email_sender,
    digest_email_body,
    digest_email_subject,
)

logger = logging.getLogger("src.services.newsletter_service")

PENDING = "pending"
SENT = "sent"
LOGGED = "logged"
FAILED = "failed"

FREQUENCIES = ("daily", "weekly", "off")

UNSUBSCRIBE_TOKEN_DAYS = 30


def next_digest_occurrence(now: datetime, hour: int) -> datetime:
    """The next UTC instant at which the subscriber's preferred hour fires.

    Returns the strictly-future occurrence (never `now` itself), so a digest
    generated mid-day lands tomorrow at the reader's hour instead of arriving
    whenever the batch happened to run.
    """
    now = now.astimezone(timezone.utc)
    candidate = now.replace(hour=int(hour) % 24, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def build_unsubscribe_token(user_id) -> str:
    """A signed one-click-unsubscribe credential for `user_id`.

    The emailed unsubscribe link carries this token instead of the user's
    session: the reader is unauthenticated, so the bearer token itself is the
    credential. Short-lived and single-purpose so a leak cannot escalate into
    any other account action.
    """
    payload = {
        "sub": str(user_id),
        "purpose": "newsletter_unsubscribe",
        "exp": datetime.now(timezone.utc) + timedelta(days=UNSUBSCRIBE_TOKEN_DAYS),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_unsubscribe_token(token: str):
    """Resolve a valid unsubscribe token to its user id (UUID).

    Raises ValueError on a tampered, expired, or mis-purposed token so the
    caller can answer 401 instead of taking action on an unverified identity.
    """
    from jose import JWTError

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise ValueError("invalid or expired unsubscribe token")
    if payload.get("purpose") != "newsletter_unsubscribe":
        raise ValueError("unsubscribe token has the wrong purpose")
    raw = payload.get("sub")
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        raise ValueError("unsubscribe token names an unknown user")


class NewsletterService:
    def __init__(self, db: AsyncSession):
        self._db = db

    # ── subscriptions ─────────────────────────────────────────────────────────
    async def subscribe(self, user_id) -> NewsletterSubscription:
        subscription = (
            await self._db.execute(
                select(NewsletterSubscription).where(NewsletterSubscription.user_id == user_id)
            )
        ).scalars().first()
        if subscription is None:
            subscription = NewsletterSubscription(user_id=user_id, unsubscribed_at=None)
            self._db.add(subscription)
        else:
            subscription.unsubscribed_at = None
        await self._db.commit()
        await self._db.refresh(subscription)
        return subscription

    async def unsubscribe(self, user_id) -> bool:
        """End a subscription and cancel its still-pending deliveries.

        Returns True when there was an active subscription to end. Pending
        (never-sent) deliveries are removed so an unsubscribe actually stops
        the queued emails; already-sent rows stay as audit.
        """
        subscription = (
            await self._db.execute(
                select(NewsletterSubscription).where(
                    NewsletterSubscription.user_id == user_id,
                    NewsletterSubscription.unsubscribed_at.is_(None),
                )
            )
        ).scalars().first()
        if subscription is None:
            return False
        subscription.unsubscribed_at = datetime.now(timezone.utc)
        await self._db.execute(
            NewsletterDelivery.__table__.delete().where(
                NewsletterDelivery.user_id == user_id,
                NewsletterDelivery.delivery_status == PENDING,
            )
        )
        await self._db.commit()
        return True

    async def subscription_for(self, user_id) -> Optional[NewsletterSubscription]:
        result = await self._db.execute(
            select(NewsletterSubscription).where(NewsletterSubscription.user_id == user_id)
        )
        return result.scalars().first()

    async def set_preferences(
        self,
        user_id,
        *,
        frequency: Optional[str] = None,
        topics: Optional[List[str]] = None,
        preferred_digest_hour: Optional[int] = None,
    ) -> NewsletterSubscription:
        """Update a user's newsletter preferences.

        Preferences live on the subscription row (one per user). Setting them
        for a reader who has no row subscribes them (default daily); setting
        them for an unsubscribed reader re-activates the subscription, because
        adjusting delivery preferences is itself an opt-in signal.
        """
        sub = await self.subscription_for(user_id)
        if sub is None:
            sub = NewsletterSubscription(user_id=user_id)
            self._db.add(sub)
        if frequency is not None:
            if frequency not in FREQUENCIES:
                raise ValueError(f"invalid digest_frequency: {frequency!r}")
            sub.digest_frequency = frequency
        if topics is not None:
            cleaned = sorted({t.strip().upper() for t in topics if t and t.strip()})
            sub.topics = cleaned or None
        if preferred_digest_hour is not None:
            hour = int(preferred_digest_hour)
            if not (0 <= hour <= 23):
                raise ValueError("preferred_digest_hour must be 0-23")
            sub.preferred_digest_hour = hour
        if sub.unsubscribed_at is not None:
            sub.unsubscribed_at = None
        await self._db.commit()
        await self._db.refresh(sub)
        return sub

    async def active_subscriber_ids(self) -> List:
        result = await self._db.execute(
            select(NewsletterSubscription.user_id).where(
                NewsletterSubscription.unsubscribed_at.is_(None)
            )
        )
        return list(result.scalars())

    async def targeted_subscriber_ids_for(self, digest: Digest) -> List:
        """The users who should receive `digest` by email.

        Two gates compose:

        * Frequency. A 'daily' digest goes only to subscribers whose frequency
          is 'daily'; a 'weekly' deep-dive goes to 'daily' and 'weekly'
          subscribers (the deep-dive is the richer supplement). 'off' receives
          neither.
        * Topics. A subscriber with no preferences receives every matching
          digest by default. A subscriber who *has* topic interests only
          receives a digest when it covers at least one of them
          (case-insensitive), so preferences always narrow — they can never
          widen the blast radius. The subscription's own `topics` win over the
          account-level `User.preferred_topics` when both are set.
        """
        ids = await self.active_subscriber_ids()
        if not ids:
            return []
        sub_rows = await self._db.execute(
            select(NewsletterSubscription.user_id, NewsletterSubscription.digest_frequency,
                   NewsletterSubscription.topics)
            .where(NewsletterSubscription.user_id.in_(ids))
        )
        sub_id = {}
        sub_topics = {}
        for uid, frequency, topics in sub_rows.all():
            sub_id[uid] = frequency or "daily"
            sub_topics[uid] = [t for t in (topics or []) if t]

        user_fallback_rows = await self._db.execute(
            select(User.id, User.preferred_topics).where(User.id.in_(ids))
        )
        user_fallback = {
            uid: [t for t in (topics or []) if t] for uid, topics in user_fallback_rows.all()
        }

        wanted_frequencies = {"daily"} if digest.frequency == "daily" else {"daily", "weekly"}

        event_topics = set()
        raw_ids = digest.event_ids if isinstance(digest.event_ids, list) else (digest.event_ids or [])
        if raw_ids:
            parsed = []
            for item in raw_ids:
                try:
                    parsed.append(uuid.UUID(str(item)))
                except (ValueError, TypeError):
                    continue
            if parsed:
                topic_rows = await self._db.execute(
                    select(Event.topic).where(Event.id.in_(parsed))
                )
                event_topics = {t.upper() for (t,) in topic_rows.all() if t}

        def _wants(uid) -> bool:
            if sub_id.get(uid) not in wanted_frequencies:
                return False
            prefs = {t.upper() for t in (sub_topics.get(uid) or user_fallback.get(uid) or [])}
            if not prefs:
                return True
            return bool(event_topics and (prefs & event_topics))

        return [uid for uid in ids if _wants(uid)]

    # ── enqueue / flush ────────────────────────────────────────────────────────
    async def enqueue_digest(
        self, digest: Digest, *, now: Optional[datetime] = None
    ) -> int:
        """Queue one delivery per targeted subscriber for `digest`.

        Idempotent by construction: the (digest_id, user_id) unique constraint
        makes a duplicate insert a no-op. Each delivery is stamped with
        scheduled_for = the next occurrence of that subscriber's
        preferred_digest_hour (default 6), so a reader's brief waits for their
        chosen UTC hour instead of shipping at batch time. Returns how many
        rows this call created.
        """
        now = now or datetime.now(timezone.utc)
        subscriber_ids = await self.targeted_subscriber_ids_for(digest)
        if not subscriber_ids:
            return 0
        pref_rows = await self._db.execute(
            select(NewsletterSubscription.user_id, NewsletterSubscription.preferred_digest_hour)
            .where(NewsletterSubscription.user_id.in_(subscriber_ids))
        )
        hours = {
            uid: (int(hour) if hour is not None else 6)
            for uid, hour in pref_rows.all()
        }
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(NewsletterDelivery).values(
            [
                {
                    "digest_id": digest.id,
                    "user_id": uid,
                    "scheduled_for": next_digest_occurrence(now, hours.get(uid, 6)),
                }
                for uid in subscriber_ids
            ]
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["digest_id", "user_id"])
        result = await self._db.execute(stmt.returning(NewsletterDelivery.id))
        created = len(list(result.all()))
        await self._db.commit()
        return created

    async def pending_deliveries(
        self,
        limit: int = 50,
        *,
        now: Optional[datetime] = None,
    ) -> List[Tuple[NewsletterDelivery, Digest, User, Optional[NewsletterSubscription]]]:
        """The next *due* un-sent sends with their digest content, subscriber, and prefs.

        A row is due when it has no scheduled_for (legacy rows sent
        immediately) or its scheduled_for has arrived. Future-dated rows stay
        in the queue untouched, which is what defers a brief to the
        subscriber's preferred hour.
        """
        now = now or datetime.now(timezone.utc)
        result = await self._db.execute(
            select(NewsletterDelivery, Digest, User, NewsletterSubscription)
            .join(Digest, NewsletterDelivery.digest_id == Digest.id)
            .join(User, NewsletterDelivery.user_id == User.id)
            .join(NewsletterSubscription, NewsletterDelivery.user_id == NewsletterSubscription.user_id)
            .where(
                NewsletterDelivery.delivery_status == PENDING,
                or_(
                    NewsletterDelivery.scheduled_for.is_(None),
                    NewsletterDelivery.scheduled_for <= now,
                ),
            )
            .order_by(NewsletterDelivery.scheduled_for.asc().nulls_first(),
                      NewsletterDelivery.created_at.asc(), NewsletterDelivery.id.asc())
            .limit(limit)
        )
        return list(result.all())

    async def flush_pending_deliveries(self, limit: int = 50, *, now: Optional[datetime] = None) -> dict:
        """Deliver (or, in log mode, capture) up to `limit` *due* pending sends.

        Failures are recorded on the row ('failed' + error_text) and the loop
        keeps going, so one bad relay cannot block the rest of the queue.
        """
        sender = build_email_sender()
        rows = await self.pending_deliveries(limit, now=now)
        counts = {PENDING: 0, SENT: 0, LOGGED: 0, FAILED: 0}
        now = datetime.now(timezone.utc)
        reengage_days = max(1, int(getattr(settings, "REENGAGEMENT_DAYS", 30) or 30))
        reengagement_enabled = bool(getattr(settings, "DIGEST_REENGAGEMENT_ENABLED", False))
        for delivery, digest, user, subscription in rows:
            if user.is_blocked:
                delivery.delivery_status = FAILED
                delivery.error_text = "recipient account is blocked"
                await self._db.flush()
                counts[FAILED] = counts.get(FAILED, 0) + 1
                continue
            try:
                unsubscribe_url = None
                if settings.APP_BASE_URL:
                    token = build_unsubscribe_token(user.id)
                    unsubscribe_url = (
                        f"{settings.APP_BASE_URL.rstrip('/')}/newsletter/unsubscribe?token={token}"
                    )
                # A subscriber whose last successful send predates the window is
                # marked for the win-back line. Purely a function of our own
                # delivery history — no open-tracking involved.
                last_sent = subscription.last_email_sent_at if subscription else None
                reengagement = (
                    reengagement_enabled
                    and (last_sent is None or (now - last_sent).days >= reengage_days)
                )
                body = digest_email_body(
                    recipient_name=user.name or "Reader",
                    digest_title=digest.title,
                    content_html=digest.content_html,
                    base_url=settings.APP_BASE_URL,
                    deliverable=settings.EMAIL_ENABLED,
                    unsubscribe_url=unsubscribe_url,
                    reengagement=reengagement,
                )
                await sender.send(
                    recipient_name=user.name or "Reader",
                    recipient_email=user.email,
                    subject=digest_email_subject(digest.title),
                    html=body["html"],
                    plain=body["plain"],
                    list_unsubscribe_url=unsubscribe_url if settings.EMAIL_ENABLED else None,
                )
                delivery.delivery_status = SENT if settings.EMAIL_ENABLED else LOGGED
                delivery.sent_at = now
                delivery.error_text = None
                if subscription is not None:
                    subscription.last_email_sent_at = now
            except Exception as exc:
                delivery.delivery_status = FAILED
                delivery.error_text = str(exc)
            counts[delivery.delivery_status] = counts.get(delivery.delivery_status, 0) + 1
            await self._db.flush()
        await self._db.commit()
        counts[PENDING] = len(await self.pending_deliveries(limit, now=now))
        return counts

    async def delivery_counts(self) -> dict:
        result = await self._db.execute(
            select(NewsletterDelivery.delivery_status, func.count(NewsletterDelivery.id))
            .group_by(NewsletterDelivery.delivery_status)
        )
        counts = {PENDING: 0, SENT: 0, LOGGED: 0, FAILED: 0}
        for status_name, count in result.all():
            counts[status_name] = count
        return counts


async def run_newsletter_worker() -> None:
    """Background drain: flush pending deliveries on a configurable cadence.

    Started from the lifespan whenever DIGEST_ENABLED or EMAIL_ENABLED is on
    (with both off there can be no digest to send and no SMTP to relay). Fires
    once immediately on boot, then sleeps EMAIL_POLL_SECONDS between drains.
    """
    poll_seconds = max(5, int(getattr(settings, "EMAIL_POLL_SECONDS", 60) or 60))
    from src.core.database import get_db

    while True:
        try:
            async for db in get_db():
                try:
                    counts = await NewsletterService(db).flush_pending_deliveries()
                    if any(counts.get(k) for k in (SENT, LOGGED, FAILED)):
                        logger.info("Newsletter flush: %s", counts)
                finally:
                    await db.close()
                break
        except Exception:
            logger.error("Newsletter flush failed", exc_info=True)
        await asyncio.sleep(poll_seconds)