"""
GlobeLens AI — audio playback URLs
==================================
A media element cannot send an Authorization header, so <audio src> cannot call
the narration routes directly. The previous workaround — fetch with a bearer
token, wrap the whole response in a Blob, then play the object URL — forced the
listener to wait for every byte before hearing anything, which on a cold clip
meant a minute of silence for a three-minute briefing.

Issuing a short-lived signed URL for one resource lets the browser stream straight
from the API, so playback starts while the audio is still being synthesised,
without dropping the account requirement or opening the routes to anonymous
callers.

The signature covers the listener's identity as well as the resource and the
expiry, so a URL cannot be edited to reach another article, reused after it
expires, or replayed by a different account. Because the identity travels with the
signature, the route can still apply the same owner/admin visibility rule it
applies to a signed-in request without a database lookup.

The signing key is derived from SECRET_KEY, so there is no second secret to
provision or rotate.
"""
import base64
import hmac
import time
import uuid
from hashlib import sha256
from typing import Dict, Optional

from app.core.config import settings

# Ten minutes: long enough for a listener to start and finish a briefing, short
# enough that a leaked link is worthless almost immediately.
DEFAULT_TTL_SECONDS = 600


def _signing_key() -> bytes:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), b"globelens-audio-url-v1", sha256
    ).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _mac(payload: str) -> bytes:
    return hmac.new(_signing_key(), payload.encode("utf-8"), sha256).digest()


def issue(
    resource: str, user_id: uuid.UUID, role: str, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> Dict[str, object]:
    """Mint a playback token for one resource on behalf of one listener.

    Returns the token and its absolute expiry so a client can build the URL itself
    rather than having to resolve a path this service knows nothing about.
    """
    expires_at = int(time.time()) + max(30, int(ttl_seconds))
    payload = f"{resource}|{user_id}|{role}|{expires_at}"
    token = f"{_b64(payload.encode('utf-8'))}.{_b64(_mac(payload))}"
    return {"token": token, "expires_at": expires_at}


def verify(resource: str, token: str) -> Optional[Dict[str, object]]:
    """Return the listener a token was minted for, or None if it is not valid.

    Every rejection path returns None rather than raising: a caller cannot tell a
    forged token from an expired one, and the route answers both the same way.
    """
    if not token or "." not in token:
        return None
    body, signature = token.split(".", 1)
    try:
        payload = _unb64(body).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(signature, _b64(_mac(payload))):
        return None

    parts = payload.split("|")
    if len(parts) != 4:
        return None
    signed_resource, user_id, role, expires_at = parts
    if not hmac.compare_digest(signed_resource, resource):
        return None
    try:
        if int(expires_at) < int(time.time()):
            return None
        return {"user_id": uuid.UUID(user_id), "role": role}
    except (ValueError, TypeError, AttributeError):
        return None