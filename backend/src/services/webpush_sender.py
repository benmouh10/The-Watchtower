"""Web Push delivery on top of the scheduled-digest engine.

Two senders, chosen purely from config: PUSH_ENABLED=False (the default) logs
the full would-be payload and the delivery row is later marked 'logged' so it
is unambiguous that nothing actually went out. With PUSH_ENABLED=True the VAPID
sender relays each notification through the browser's push service using the
pywebpush encryption stack. pywebpush is imported lazily so the rest of the app
works fine without it installed.
"""
import json
import logging
from typing import Any, Dict, Optional

from src.core.config import settings

logger = logging.getLogger("src.services.webpush_sender")


class PushGoneError(RuntimeError):
    """The push service no longer knows this subscription (404/410 Gone).

    Raised by the VAPID sender when the relay answers 410 (endpoint expired)
    or 404 (unknown). The queue treats this as "forget the device", not as a
    transient failure to retry forever.
    """


class PushSender:
    """Delivery abstraction: send(subscription, payload) -> None.

    A raise means delivery failed (the queue records 'failed' with the
    message); a normal return means the notification was delivered or
    captured, depending on the mode.
    """

    name: str = "base"

    async def send(self, endpoint: str, p256dh: str, auth: str, payload: Dict[str, Any]) -> None:
        raise NotImplementedError


class LogPushSender(PushSender):
    """Records the would-be notification to the application log. Honest: the
    delivery row is later marked 'logged', never 'sent'."""

    name = "log"

    async def send(self, endpoint, p256dh, auth, payload):
        logger.info(
            "PUSH (log mode, not sent): endpoint=%r payload=%r",
            endpoint, payload,
        )


class VapidPushSender(PushSender):
    """Real Web Push relay, offloaded to a worker thread (sync pywebpush)."""

    name = "vapid"

    def __init__(self, vapid_public_key: str, vapid_private_key: str,
                 vapid_contact_email: str, subject: str):
        self._vapid_pub = vapid_public_key
        self._vapid_priv = vapid_private_key
        self._contact = vapid_contact_email
        self._subject = subject

    async def send(self, endpoint, p256dh, auth, payload):
        def _blocking():
            from pywebpush import WebPushException, webpush

            try:
                webpush(
                    subscription_info={
                        "endpoint": endpoint,
                        "keys": {"p256dh": p256dh, "auth": auth},
                    },
                    data=json.dumps(payload).encode("utf-8"),
                    vapid_private_key=self._vapid_priv,
                    vapid_claims={
                        "sub": f"mailto:{self._contact}",
                    },
                    ttl=24 * 60 * 60,
                )
            except WebPushException as exc:
                    if exc.status_code in (404, 410):
                        raise PushGoneError(
                            f"push service no longer knows this endpoint "
                            f"({exc.status_code} Gone): {exc.message}"
                        ) from exc
                    raise RuntimeError(f"push relay failed ({exc.status_code}): {exc.message}")

        import asyncio
        return await asyncio.to_thread(_blocking)


def build_push_sender(overrides: Optional[Dict[str, Any]] = None) -> PushSender:
    """Config-gated sender factory, tested with an injectable dict."""
    cfg = settings
    if overrides:
        cfg = settings.model_copy(update=overrides)

    if not cfg.PUSH_ENABLED:
        return LogPushSender()

    if not (cfg.VAPID_PUBLIC_KEY and cfg.VAPID_PRIVATE_KEY and cfg.VAPID_CONTACT_EMAIL):
        raise ValueError(
            "PUSH_ENABLED is true but the VAPID keypair/contact are not set. "
            "Web Push needs VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY and "
            "VAPID_CONTACT_EMAIL; refusing to guess."
        )

    sender = VapidPushSender(
        vapid_public_key=cfg.VAPID_PUBLIC_KEY,
        vapid_private_key=cfg.VAPID_PRIVATE_KEY,
        vapid_contact_email=cfg.VAPID_CONTACT_EMAIL,
        subject=cfg.PUSH_SUBJECT,
    )
    logger.info("Push sender enabled (vapid): subject=%r contact=%r", cfg.PUSH_SUBJECT, cfg.VAPID_CONTACT_EMAIL)
    return sender


def build_payload_for_digest(*, title: str, event_count: int, base_url: Optional[str], digest_id: str) -> Dict[str, Any]:
    """The notification payload for a completed digest."""
    payload = {
        "title": f"{settings.PUSH_SUBJECT}: {title}",
        "body": (
            "Your scheduled comprehensive brief is ready."
            if event_count == 0
            else f"{event_count} event(s) covered. Open to read the brief."
        ),
        "badge": "",
        "tag": f"digest-{digest_id}",
    }
    if base_url:
        payload["data"] = {"url": f"{base_url.rstrip('/')}/digests/{digest_id}"}
    return payload


def build_payload_for_event(*, title: str, summary: str, event_id: str,
                            base_url: Optional[str]) -> Dict[str, Any]:
    """The notification payload for a followed event going live."""
    body = (summary or "").strip()
    payload = {
        "title": f"{settings.PUSH_SUBJECT}: {title}",
        "body": body[:180] or "The event you are following has been processed.",
        "badge": "",
        "tag": f"event-{event_id}",
    }
    if base_url:
        payload["data"] = {"url": f"{base_url.rstrip('/')}/events/{event_id}"}
    return payload