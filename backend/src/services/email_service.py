"""Email delivery for the scheduled digest newsletter.

Two senders, chosen purely from config: EMAIL_ENABLED=False (the default)
logs the full would-be message rather than emailing it, and the delivery row
is marked 'logged' so it is unambiguous that nothing actually went out. With
EMAIL_ENABLED=True the SMTP sender relays the message. SMTP is synchronous, so
sends run inside asyncio.to_thread to keep the event loop free.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any, Dict, Optional

from src.core.config import settings

logger = logging.getLogger("src.services.email_service")


class EmailSender:
    """Delivery abstraction: send(recipient, subject, html, plain) -> None.

    A raise means the delivery failed (the queue records 'failed' with the
    message); a normal return means the message was delivered or captured,
    depending on the mode.
    """

    name: str = "base"

    async def send(
        self,
        recipient_name: str,
        recipient_email: str,
        subject: str,
        html: str,
        plain: str,
        list_unsubscribe_url: Optional[str] = None,
    ) -> None:
        raise NotImplementedError


class LogEmailSender(EmailSender):
    """Records the would-be message to the application log. Honest: the
    delivery row is later marked 'logged', never 'sent'."""

    name = "log"

    async def send(self, recipient_name, recipient_email, subject, html, plain,
                   list_unsubscribe_url=None):
        logger.info(
            "NEWSLETTER (log mode, not emailed): to=%s <%s> subject=%r html_bytes=%d",
            recipient_name, recipient_email, subject, len(html.encode("utf-8")),
        )


class SmtpEmailSender(EmailSender):
    """Real SMTP relay via smtplib, offloaded to a worker thread."""

    name = "smtp"

    def __init__(self, host: str, port: int, username: Optional[str], password: Optional[str],
                 from_email: str, starttls: bool = True):
        self._host = host
        self._port = port
        self._username = username or ""
        self._password = password or ""
        self._from_email = from_email
        self._starttls = starttls

    async def send(self, recipient_name, recipient_email, subject, html, plain,
                   list_unsubscribe_url=None):
        def _blocking():
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = self._from_email
            message["To"] = f"{recipient_name} <{recipient_email}>" if recipient_name else recipient_email
            if list_unsubscribe_url:
                # RFC 8058 one-click unsubscribe: the header advertises both the
                # URL (List-Unsubscribe) and that posting an empty POST body to
                # it performs the unsubscribe (List-Unsubscribe-Post).
                message["List-Unsubscribe"] = f"<{list_unsubscribe_url}>"
                message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            message.attach(MIMEText(plain, "plain", "utf-8"))
            message.attach(MIMEText(html, "html", "utf-8"))
            with smtplib.SMTP(self._host, self._port, timeout=30) as server:
                if self._starttls:
                    server.starttls()
                if self._username:
                    server.login(self._username, self._password)
                server.send_message(message)

        await asyncio_to_thread(_blocking)

    def _describe_config(self) -> str:
        masked = "****" if self._password else "(none)"
        return f"host={self._host}:{self._port} from={self._from_email} user={self._username or '(none)'} pass={masked} starttls={self._starttls}"


async def asyncio_to_thread(func):
    import asyncio
    return await asyncio.to_thread(func)


def build_email_sender(overrides: Optional[Dict[str, Any]] = None) -> EmailSender:
    """Config-gated sender factory, tested with an injectable dict."""
    cfg = settings
    if overrides:
        cfg = settings.model_copy(update=overrides)

    if not cfg.EMAIL_ENABLED:
        return LogEmailSender()

    if not cfg.EMAIL_SMTP_HOST:
        raise ValueError(
            "EMAIL_ENABLED is true but EMAIL_SMTP_HOST is not set. "
            f"SMTP relay needs a host; refusing to guess."
        )
    sender = SmtpEmailSender(
        host=cfg.EMAIL_SMTP_HOST,
        port=cfg.EMAIL_SMTP_PORT,
        username=cfg.EMAIL_SMTP_USERNAME,
        password=cfg.EMAIL_SMTP_PASSWORD,
        from_email=cfg.EMAIL_FROM_EMAIL or "newsletter@globelens.local",
        starttls=cfg.EMAIL_SMTP_STARTTLS,
    )
    logger.info("Email sender enabled (smtp): %s", sender._describe_config())
    return sender


def digest_email_subject(digest_title: str) -> str:
    return f"GlobeLens AI — {digest_title}"


def digest_email_body(
    *,
    recipient_name: str,
    digest_title: str,
    content_html: str,
    base_url: Optional[str],
    deliverable: bool,
    unsubscribe_url: Optional[str] = None,
    reengagement: bool = False,
) -> Dict[str, str]:
    """Build (html, plain) for a digest newsletter from its stored brief."""
    greeting = f"Hello {recipient_name.strip()},"
    headline = f"Your scheduled comprehensive brief is ready: {digest_title}"
    lead_text = (
        "This email would be delivered by GlobeLens AI."
        if not deliverable
        else "Your GlobeLens AI comprehensive brief is ready."
    )

    reengage_html = ""
    reengage_plain = ""
    if reengagement:
        reengage_html = (
            '<p style="background:#fef3c7;color:#78350f;padding:10px 12px;'
            'border-radius:6px;">We&rsquo;ve missed you — this is your first brief '
            "in a while.</p>"
        )
        reengage_plain = "We've missed you — this is your first brief in a while."

    unsub_html = ""
    unsub_plain = ""
    if base_url and unsubscribe_url:
        unsub_html = (
            f'<p class="unsub">You are receiving this because you are subscribed to the GlobeLens AI '
            f'comprehensive brief. <a href="{escape(unsubscribe_url)}">Unsubscribe</a> '
            f'or sign in and toggle the digest subscription.</p>'
        )
        unsub_plain = (
            "You are receiving this because you subscribed to the GlobeLens AI comprehensive brief. "
            f"Unsubscribe at {unsubscribe_url} or sign in and toggle the digest subscription."
        )
    elif base_url:
        unsub_html = (
            f'<p class="unsub">You are receiving this because you are subscribed to the GlobeLens AI '
            f'comprehensive brief. <a href="{escape(base_url)}/newsletter/unsubscribe">Unsubscribe</a> '
            f'or sign in and toggle the digest subscription.</p>'
        )
        unsub_plain = (
            "You are receiving this because you subscribed to the GlobeLens AI comprehensive brief. "
            f"Unsubscribe at {base_url}/newsletter/unsubscribe or sign in and toggle the digest subscription."
        )
    else:
        unsub_html = (
            '<p class="unsub">You are receiving this because you are subscribed to the GlobeLens AI '
            'comprehensive brief. To stop these emails, sign in and disable the digest subscription.</p>'
        )
        unsub_plain = (
            "You are receiving this because you subscribed to the GlobeLens AI comprehensive brief. "
            "To stop these emails, sign in and disable the digest subscription."
        )

    html = (
        f"<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{escape(digest_title)}</title></head><body style=\"font-family: Arial, sans-serif; "
        f"max-width: 680px; margin: 0 auto; color: #222; line-height: 1.5;\">"
        f"<p>{escape(greeting)}</p>"
        f"<h1 style=\"font-size: 18pt;\">{escape(headline)}</h1>"
        f"<p>{escape(lead_text)}</p>"
        f"{reengage_html}"
        f"{content_html}"
        f"{unsub_html}"
        f"</body></html>"
    )
    plain = f"{greeting}\n\n{headline}\n\n{lead_text}\n\n{reengage_plain}\n\n{unsub_plain}"
    return {"html": html, "plain": plain}