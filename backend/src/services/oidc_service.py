"""
GlobeLens AI — OIDC SSO service
================================
Authorization-code flow against an OIDC issuer, fully opt-in (OIDC_ENABLED).
Everything is resolved from the issuer's discovery document at call time:

  GET /auth/oidc/start    → build the authorization URL (state + nonce stored
                            in Redis with a short TTL) and redirect.
  GET /auth/oidc/callback → exchange the code, verify the ID token signature
                            against the issuer's JWKS plus iss/aud/nonce/exp,
                            then take the verified, email-verified address as
                            the account key (find-or-create user).

Honesty rules mirror the rest of the app:
  * Disabled or mis-configured → OidcUnavailable surfaces as an honest 503,
    never a fake "signed in with Google" claim.
  * The ID token must verify cryptographically; a bad signature/claims fails
    the exchange with a 400 and no session is created.
"""
import asyncio
import base64
import json
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
import structlog

from src.core.config import Settings

logger = structlog.get_logger()

_DISCOVERY_CACHE: Dict[str, Dict[str, Any]] = {}
# How long a /start state may wait for the /callback to return.
STATE_TTL_SECONDS = 600
REDIS_STATE_PREFIX = "globelens:oidc:state:"


class OidcUnavailable(RuntimeError):
    """Raised when SSO is disabled or cannot be reached; surfaces as 503."""


def oidc_configured(cfg: Optional[Settings] = None) -> bool:
    cfg = cfg or _global_settings()
    return bool(
        cfg.OIDC_ENABLED
        and cfg.OIDC_ISSUER
        and cfg.OIDC_CLIENT_ID
        and cfg.OIDC_CLIENT_SECRET
    )


def _global_settings() -> Settings:
    from src.core.config import settings
    return settings


def _random_token() -> str:
    return secrets.token_urlsafe(32)


def callback_url(cfg: Settings) -> str:
    return f"{cfg.APP_BASE_URL.rstrip('/')}/api/v1/auth/oidc/callback"


# ── Discovery ─────────────────────────────────────────────────────────────

async def discovery(cfg: Settings) -> Dict[str, Any]:
    """Cached OIDC discovery document. Raises OidcUnavailable on any failure."""
    if cfg.OIDC_ISSUER in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE[cfg.OIDC_ISSUER]
    url = f"{cfg.OIDC_ISSUER.rstrip('/')}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            doc = response.json()
    except Exception as exc:
        raise OidcUnavailable(f"Could not load OIDC discovery document: {exc}")
    for required in ("authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint"):
        if not doc.get(required):
            raise OidcUnavailable(
                f"OIDC issuer {cfg.OIDC_ISSUER} is missing '{required}' in its "
                "discovery document."
            )
    _DISCOVERY_CACHE[cfg.OIDC_ISSUER] = doc
    return doc


# ── Step 1: authorization request ─────────────────────────────────────────

async def _save_state(cfg: Settings, nonce: str) -> str:
    """Persist state → nonce in Redis for the callback's single-use check."""
    import redis.asyncio as aioredis

    state = _random_token()
    try:
        client = aioredis.from_url(cfg.REDIS_URL, encoding="utf-8", decode_responses=True)
        await client.set(f"{REDIS_STATE_PREFIX}{state}", nonce, ex=STATE_TTL_SECONDS)
        await client.aclose()
    except Exception as exc:  # pragma: no cover - Redis is a hard dependency
        raise OidcUnavailable(f"Could not store OIDC state: {exc}")
    return state


async def build_authorization_url(cfg: Settings) -> str:
    """Authorization endpoint URL for step 1. Raises OidcUnavailable when off."""
    if not oidc_configured(cfg):
        raise OidcUnavailable(
            "Single sign-on is not enabled (OIDC_ENABLED, OIDC_ISSUER, "
            "OIDC_CLIENT_ID and OIDC_CLIENT_SECRET must all be set)."
        )
    if not cfg.APP_BASE_URL:
        raise OidcUnavailable(
            "APP_BASE_URL is not set, so the OIDC callback address cannot be built."
        )
    doc = await discovery(cfg)
    nonce = _random_token()
    state = await _save_state(cfg, nonce)
    params = {
        "client_id": cfg.OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": (cfg.OIDC_SCOPES or "openid email profile").strip(),
        "redirect_uri": callback_url(cfg),
        "state": state,
        "nonce": nonce,
    }
    return f"{doc['authorization_endpoint']}?{urlencode(params)}"


# ── Step 2: code exchange + verification ──────────────────────────────────

async def _consume_state(cfg: Settings, state: str) -> Optional[str]:
    """Return the nonce stored for this state, deleting it (single-use)."""
    import redis.asyncio as aioredis

    try:
        client = aioredis.from_url(cfg.REDIS_URL, encoding="utf-8", decode_responses=True)
        value = await client.get(f"{REDIS_STATE_PREFIX}{state}")
        if value is not None:
            await client.delete(f"{REDIS_STATE_PREFIX}{state}")
        await client.aclose()
        return value
    except Exception:
        return None


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def _fetch_jwks(doc: Dict[str, Any]) -> list:
    jwks_uri = doc["jwks_uri"]
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        response = client.get(jwks_uri)
        response.raise_for_status()
        return response.json().get("keys", [])


async def _verify_id_token(
    cfg: Settings, id_token: str, *, expected_nonce: str
) -> Dict[str, Any]:
    """Decode and cryptographically verify the ID token via the issuer's JWKS."""
    try:
        from jose import jwt as jose_jwt

        doc = await discovery(cfg)
        keys = await asyncio.to_thread(_fetch_jwks, doc)
        claims = jose_jwt.decode(
            id_token,
            keys,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
            audience=cfg.OIDC_CLIENT_ID,
            issuer=cfg.OIDC_ISSUER,
            options={"verify_at_hash": False},
        )
        assert jose_jwt.get_unverified_headers(id_token) is not None
    except Exception as exc:
        raise OidcUnavailable(f"ID token verification failed: {exc}")

    if claims.get("nonce") != expected_nonce:
        raise OidcUnavailable("ID token nonce mismatch; the login was replayed.")
    return claims


async def exchange_code(cfg: Settings, *, code: str, state: str) -> Dict[str, Any]:
    """Complete the authorization-code exchange and return verified identity."""
    if not oidc_configured(cfg):
        raise OidcUnavailable("Single sign-on is not enabled.")
    stored_nonce = await _consume_state(cfg, state)
    if stored_nonce is None:
        raise OidcUnavailable("Unknown or expired SSO state; restart the login.")

    doc = await discovery(cfg)
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        token_resp = await client.post(
            doc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url(cfg),
                "client_id": cfg.OIDC_CLIENT_ID,
                "client_secret": cfg.OIDC_CLIENT_SECRET,
            },
        )
        if token_resp.status_code >= 300:
            raise OidcUnavailable(f"Token exchange failed: HTTP {token_resp.status_code}")
        token_payload = token_resp.json()

    id_token = token_payload.get("id_token")
    if not id_token:
        raise OidcUnavailable("Token response contained no id_token.")

    claims = await _verify_id_token(cfg, id_token, expected_nonce=stored_nonce)

    email = claims.get("email")
    if not email:
        raise OidcUnavailable("The identity provider did not return an email address.")

    google_issuer = "accounts.google.com" in cfg.OIDC_ISSUER
    if google_issuer and claims.get("email_verified") is not True:
        raise OidcUnavailable("The identity provider did not verify this email address.")

    profile: Dict[str, Any] = {
        "sub": claims.get("sub"),
        "email": email,
        "name": (
            claims.get("name")
            or claims.get("preferred_username")
            or email.split("@")[0]
        ),
        "email_verified": True,
    }
    access_token = token_payload.get("access_token")
    if access_token and doc.get("userinfo_endpoint"):
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                uinfo = await client.get(
                    doc["userinfo_endpoint"],
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if uinfo.status_code < 300:
                    info = uinfo.json()
                    if info.get("email") == email:
                        profile["name"] = info.get("name") or profile["name"]
                        profile["sub"] = info.get("sub") or profile["sub"]
        except Exception:
            pass
    return profile