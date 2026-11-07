"""
GlobeLens AI — AuthController
POST /auth/register | /auth/login | /auth/logout | /auth/refresh
GET  /auth/me

Refresh tokens are tracked server-side by RefreshTokenService, so rotation
genuinely invalidates the previous token and logout genuinely ends the session.
See that module for the reuse-detection rationale.
"""
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import FrozenSet, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_db
from src.data.entities.models import User, UserRole, PlanTier
from src.data.repositories.user_repository import UserRepository
from src.api.schemas.user import (
    UserRegister,
    UserLogin,
    UserResponse,
    TokenResponse,
    RefreshRequest,
    LogoutRequest,
)
from src.services.api_key_service import ALL_SCOPES
from src.services.auth_service import AuthService, hash_password
from src.services.refresh_token_service import RefreshTokenService

router = APIRouter()
logger = structlog.get_logger()
security = HTTPBearer()
# auto_error=False lets public endpoints accept "no token" without a 401, so
# anonymous readers can still browse comments.
optional_security = HTTPBearer(auto_error=False)

# Every token carries a `typ` claim. A refresh token has a multi-day lifetime,
# so accepting one as a Bearer credential would turn a low-value, high-churn
# secret into a long-lived session token. Anything without an explicit
# "access" typ is refused.
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

# Reuse detection tells the user their session was ended, because that is what
# actually happened to it. Silently returning a generic 401 would leave them
# re-logging-in and never learning a token was replayed.
REUSE_DETECTED_DETAIL = (
    "This refresh token was already used, which means either a replayed request "
    "or a stolen token. To be safe, every session from that login has been "
    "signed out. Please log in again."
)


def _client_context(request: Optional[Request]) -> tuple:
    """Best-effort user-agent / IP for the session record. Never required."""
    if request is None:
        return None, None
    agent = request.headers.get("user-agent")
    # Left to the reverse proxy's forwarded headers by default: a spoofable
    # X-Forwarded-For would otherwise let an attacker forge a plausible origin.
    ip = request.client.host if request.client else None
    return agent, ip


def _decode_token(token: str, expected_type: str) -> dict:
    """
    Decode a JWT and require a specific `typ`.

    Raises 401 on a bad signature, an expired token, a missing `sub`, or a
    token issued for the other purpose.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as exc:
        raise unauthorized from exc

    if payload.get("sub") is None:
        raise unauthorized

    token_type = payload.get("typ")
    if token_type != expected_type:
        # Distinguish a wrong-purpose token from a malformed one in logs only:
        # the client is told the same thing either way so this cannot be used
        # to probe which tokens exist.
        raise unauthorized

    return payload


# ── Dependency for fetching current authenticated user ─────────────────────────
@dataclass
class AuthIdentity:
    """Which principal made the request and under what authority.

    auth_kind is either "access_token" (a signed JWT for the account) or
    "api_key" (a hashed, scoped machine credential). Sessions carry the full
    scope set; a key carries only the scopes it was created with, which is what
    makes `require_scopes` meaningful for keys and a no-op for sessions.
    """
    user: User
    auth_kind: str
    scopes: FrozenSet[str]


async def _load_active_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Look up an account that exists and is not blocked, or 401/403."""
    user_repo = UserRepository(db)
    user = await user_repo.find_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account has been blocked",
        )
    return user


async def _resolve_api_key(db: AsyncSession, raw: str):
    """Return the ApiKey row for a raw key string, or None. Prefix-gated so an
    arbitrary non-JWT string does not trigger a hash lookup."""
    from src.data.entities.models import ApiKey
    from src.services.api_key_service import API_KEY_PREFIX, hash_key

    if not raw.startswith(API_KEY_PREFIX):
        return None
    return (await db.execute(
        select(ApiKey).where(ApiKey.key_hash == hash_key(raw))
    )).scalars().first()


# last_used_at is throttled so a busy key does not produce a write per request.
_LAST_USED_THROTTLE_SECONDS = 3600


async def _touch_api_key(key) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    if key.last_used_at is not None and (
        now - key.last_used_at
    ).total_seconds() < _LAST_USED_THROTTLE_SECONDS:
        return
    from src.core.database import AsyncSessionFactory
    async with AsyncSessionFactory() as touch_db:
        row = await touch_db.get(type(key), key.id)
        if row is not None:
            row.last_used_at = now
            await touch_db.commit()


async def get_auth_identity(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> AuthIdentity:
    """
    Resolve a Bearer credential into an AuthIdentity.

    Order matters: try the access-token JWT first (the fast path for normal
    sessions), then fall back to an API key. Both still require an active,
    unblocked account. A credential that is neither is refused with the same
    401 as before, so clients cannot distinguish the failure modes.
    """
    return await _resolve_credentials(db, credentials.credentials)


async def _resolve_credentials(db: AsyncSession, raw: str) -> AuthIdentity:
    """The shared resolution below, minus the "present Bearer" guarantee.

    Used by get_auth_identity (credential guaranteed) and by the anonymous
    read surface (get_auth_identity_optional calls this only when a credential
    is actually present, so a *present but invalid* key still refuses instead
    of silently reading as an anonymous caller).
    """

    # Phase 1: access-token JWT. Only token-decoding failures (signature, typ,
    # malformed) fall through to the API-key path. Account-level verdicts from
    # _load_active_user (blocked/unknown) must NOT be swallowed: a blocked
    # user's valid JWT must keep its 403.
    payload = None
    try:
        payload = _decode_token(raw, ACCESS_TOKEN_TYPE)
        sub = payload["sub"]
    except (HTTPException, JWTError):
        payload = None

    if payload is not None:
        try:
            user_id = uuid.UUID(sub)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        # A refresh token presented as a Bearer credential is rejected by the
        # typ check inside _decode_token; reaching here is a valid access token.
        return AuthIdentity(
            user=await _load_active_user(db, user_id),
            auth_kind="access_token",
            scopes=ALL_SCOPES,
        )

    # Phase 2: API key. Not a JWT, so it only ever lands here.
    key = await _resolve_api_key(db, raw)
    if key is not None:
        now = datetime.now(timezone.utc)
        if key.revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if key.expires_at is not None and key.expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = await _load_active_user(db, key.user_id)
        await _touch_api_key(key)
        return AuthIdentity(
            user=user,
            auth_kind="api_key",
            scopes=frozenset(key.scopes or []),
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_auth_identity_optional(
    credentials: HTTPAuthorizationCredentials = Depends(optional_security),
    db: AsyncSession = Depends(get_db),
) -> Optional[AuthIdentity]:
    """Identity resolution for the anonymous read surface.

    A request with no Bearer credential is anonymous (returns None) so the
    public feed keeps working exactly as today. Anything *present* resolves
    through the same path as a normal request — a malformed, revoked, or
    expired key still refuses with 401/403, so a broken credential cannot be
    silently treated as an anonymous caller and escape its key owner's
    accounting.
    """
    if credentials is None:
        return None
    return await _resolve_credentials(db, credentials.credentials)


async def get_current_user(identity: AuthIdentity = Depends(get_auth_identity)) -> User:
    """Dependency that yields the account behind a valid JWT or API key."""
    return identity.user


def require_scopes(*required: str):
    """
    Build a dependency that admits a JWT session unconditionally but demands
    the listed scopes from an API key. Sessions already carry the whole
    account, so scope gates must not bind them; keys are the bounded surface.
    """

    async def _dep(identity: AuthIdentity = Depends(get_auth_identity)) -> User:
        if identity.auth_kind == "api_key" and not set(required).issubset(identity.scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This API key is not permitted to perform that action. "
                    f"It requires scope(s): {', '.join(required)}"
                ),
            )
        return identity.user

    return _dep


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """
    Dependency that admits only ADMIN accounts.

    Applied as a router-level dependency on the admin router so that every
    endpoint beneath it is protected by construction. Individual endpoints
    must not rely on remembering to add the check themselves: an endpoint that
    forgets silently becomes a public write API.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges are required for this action",
        )
    return current_user


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user"
)
async def register(payload: UserRegister, db: AsyncSession = Depends(get_db)):
    """
    Register a new user.
    Hashes password, saves to DB, returns user info (excl. password_hash).
    """
    user_repo = UserRepository(db)
    auth_service = AuthService(user_repo)
    user = await auth_service.register_user(payload)

    # Open the account's audit trail with its creation row: it was created
    # onto the default FREE plan. from_plan=None means "created onto", not
    # "moved from", and changed_by=None because no administrator did it.
    from src.services.plan_service import PlanService

    await PlanService(db).record(
        user.id, to_plan=user.plan, from_plan=None, note="initial registration"
    )
    await db.commit()
    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and obtain JWT"
)
async def login(
    payload: UserLogin,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate user, returning a signed HS256 JWT containing sub, email, and role.

    A refresh token is issued and recorded server-side, starting a new rotation
    family. The raw token is returned exactly once and only its digest is kept.
    """
    user_repo = UserRepository(db)
    auth_service = AuthService(user_repo)
    user = await auth_service.authenticate_user(payload)

    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role
    }
    access_token = auth_service.create_access_token(token_data)

    user_agent, ip_address = _client_context(request)
    # A fresh family_id: this login is a new session, not a continuation.
    grant = auth_service.prepare_refresh_token({
        "sub": str(user.id), "email": user.email,
    })
    await RefreshTokenService(db).issue(
        user,
        token=grant.token,
        jti=grant.jti,
        expires_at=grant.expires_at,
        family_id=None,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=grant.token,
        expires_in=auth_service.access_token_lifetime_seconds(),
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get currently authenticated user"
)
async def me(current_user: User = Depends(get_current_user)):
    """
    Get profile details of the currently authenticated user.
    """
    return current_user


@router.post(
    "/logout",
    summary="Revoke the session behind a refresh token",
)
async def logout(
    payload: Optional[LogoutRequest] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    """
    End a session by revoking its refresh token family.

    This is a real revocation now, not an acknowledgement. Presenting a refresh
    token revokes every unspent token descended from the same login, so a copy
    captured earlier stops working immediately.

    Two honest limits remain, and the response says which applies rather than
    claiming more than it did:

      * An *access* token cannot be revoked. It is stateless and self-expiring,
        so it keeps working until its short expiry. Revoking it would require a
        server-side denylist consulted on every authenticated request, which
        makes a cache a hard dependency of authentication. That is a deliberate
        design decision, not an oversight.
      * With no refresh token supplied there is nothing to revoke, and the
        response reports `revoked: false` instead of pretending otherwise.

    The access token is still validated, so calling logout with a bad or
    wrong-purpose credential is a 401 rather than a cheerful acknowledgement.
    """
    _decode_token(credentials.credentials, ACCESS_TOKEN_TYPE)

    if payload is None or not payload.refresh_token:
        return {
            "revoked": False,
            "sessions_revoked": 0,
            "message": (
                "No refresh token was supplied, so there was no session to end. "
                "Access tokens are stateless and remain valid until they expire; "
                "discard it on the client. Send the refresh token to revoke the "
                "session immediately."
            ),
        }

    # Verify the refresh token's signature and purpose before touching state, so
    # a garbage value cannot be used to probe which tokens exist.
    try:
        _decode_token(payload.refresh_token, REFRESH_TOKEN_TYPE)
    except HTTPException:
        return {
            "revoked": False,
            "sessions_revoked": 0,
            "message": "The supplied refresh token is not valid; nothing was revoked.",
        }

    service = RefreshTokenService(db)
    row = await service.find_by_token(payload.refresh_token)

    if row is None:
        # A correctly signed token with no record. Can happen if the row was
        # purged as expired. Reported as a no-op rather than a success.
        return {
            "revoked": False,
            "sessions_revoked": 0,
            "message": "No stored session matched that refresh token.",
        }

    revoked = await service.revoke_family(row.family_id, reason="logout")
    await db.commit()

    return {
        "revoked": revoked > 0,
        "sessions_revoked": revoked,
        "message": (
            f"Revoked {revoked} active refresh token(s) for this session."
            if revoked
            else "That session was already revoked."
        ),
    }


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a valid refresh token for a new access token"
)
async def refresh_token(
    payload: RefreshRequest,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Issue a new access token from a valid refresh token, rotating the refresh
    token at the same time.

    The previous version returned the literal string "new_placeholder_jwt_token"
    as if it were a signed credential. It was not a JWT, so every caller that
    trusted it was holding a fake session.

    Rotation is now real: the presented token's row is marked revoked and linked
    to its replacement in the same transaction. Replaying a spent token is
    treated as compromise, and the whole family is revoked rather than just the
    offending token -- a thief holding a later token in the chain would
    otherwise keep the session alive while the user believed it was closed.

    Only a token whose `typ` is "refresh" is accepted here, and only a refresh
    token is issued back.
    """
    claims = _decode_token(payload.refresh_token, REFRESH_TOKEN_TYPE)

    try:
        user_id = uuid.UUID(claims["sub"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_repo = UserRepository(db)
    user = await user_repo.find_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.is_blocked:
        # A block must not be escapable by holding a live refresh token, so the
        # whole session set is torn down before the 403.
        await RefreshTokenService(db).revoke_all_for_user(user, reason="account_blocked")
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account has been blocked",
        )

    service = RefreshTokenService(db)
    row = await service.find_by_token(payload.refresh_token)

    if row is None:
        # Signature verified, but no stored record: a legacy stateless token
        # issued before this table existed, or a row already purged.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "This refresh token is no longer recognised. Log in again to "
                "start a new session."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    if row.revoked_at is not None:
        # Reuse of an already-spent token. Revoke the family, then refuse.
        revoked = await service.revoke_family(row.family_id, reason="reuse_detected")
        await db.commit()
        logger.warning(
            "Refresh token reuse detected; session family revoked",
            user_id=str(user.id),
            family_id=str(row.family_id),
            revoked=revoked,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=REUSE_DETECTED_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired. Log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # The row must belong to the user the token names. Checking this explicitly
    # means a valid token signed for one account cannot be redeemed against
    # another, even if the two ever diverged in storage.
    if row.user_id != user.id:
        logger.error(
            "Refresh token user mismatch", token_user_id=str(row.user_id),
            claim_user_id=str(user.id),
        )
        await service.revoke_family(row.family_id, reason="user_mismatch")
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_service = AuthService(user_repo)
    # The role is re-read from the database rather than trusted from the token,
    # so an admin grant or block takes effect on the next refresh.
    identity = {"sub": str(user.id), "email": user.email, "role": user.role}

    user_agent, ip_address = _client_context(request)
    # Same family: the rotation chain continues rather than starting over, which
    # is what lets a later replay of any ancestor token be detected.
    grant = auth_service.prepare_refresh_token({
        "sub": str(user.id), "email": user.email,
    })
    await service.issue(
        user,
        token=grant.token,
        jti=grant.jti,
        expires_at=grant.expires_at,
        family_id=row.family_id,
        replaces=row,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await db.commit()

    return TokenResponse(
        access_token=auth_service.create_access_token(identity),
        refresh_token=grant.token,
        expires_in=auth_service.access_token_lifetime_seconds(),
    )


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """
    Same as get_current_user, but returns None instead of raising when no valid
    token is present. Used by endpoints that are public but render personalised
    state (e.g. showing which comments the viewer has already upvoted).

    A token that is present but invalid is still rejected with 401 — silently
    downgrading a bad token to "anonymous" would hide a real client bug.
    """
    if credentials is None:
        return None

    # Same `typ` enforcement as get_current_user: a refresh token is not a
    # substitute for a missing session on a public endpoint.
    payload = _decode_token(credentials.credentials, ACCESS_TOKEN_TYPE)

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_repo = UserRepository(db)
    user = await user_repo.find_by_id(user_id)
    if not user or user.is_blocked:
        return None
    return user


@router.get(
    "/login/options",
    summary="Public sign-in options (used by the login page)",
)
async def login_options():
    """
    UI capabilities for the sign-in screen. SSO is deliberately absent until the
    operator opts in, so the frontend never renders a dead "continue with
    Google" button: it shows one only when oidc_enabled is true.
    """
    from src.services.oidc_service import oidc_configured

    available = oidc_configured()
    return {
        "oidc_enabled": available,
        "oidc_name": settings.OIDC_NAME if available else None,
    }


@router.get(
    "/oidc/start",
    summary="Start the OIDC (single sign-on) authorization-code flow",
)
async def oidc_start():
    """
    Redirect the browser to the identity provider's authorization endpoint.
    Returns an honest 503 when SSO is disabled or mis-configured.
    """
    from src.services.oidc_service import OidcUnavailable, build_authorization_url

    try:
        authorization_url = await build_authorization_url(settings)
    except OidcUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    return RedirectResponse(
        authorization_url, status_code=status.HTTP_302_FOUND
    )


@router.get(
    "/oidc/callback",
    summary="OIDC callback — exchange the code and sign in",
)
async def oidc_callback(
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    state: Optional[str] = None,
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """
    Exchange the authorization code, cryptographically verify the ID token,
    then sign the user in (find-or-create by the verified email address). The
    tokens are handed to the SPA via the login URL's query string.
    """
    from urllib.parse import quote

    from src.services.oidc_service import OidcUnavailable, exchange_code

    app_login = f"{settings.APP_BASE_URL.rstrip('/')}/login" if settings.APP_BASE_URL else "/login"

    if error:
        detail = error_description or error
        return RedirectResponse(
            f"{app_login}?oidc_error={quote(detail)}",
            status_code=status.HTTP_302_FOUND,
        )
    if not state or not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing SSO state or authorization code",
        )

    try:
        profile = await exchange_code(settings, code=code, state=state)
    except OidcUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_email(profile["email"])
    if user is None:
        user = User(
            name=profile["name"] or profile["email"].split("@")[0],
            email=profile["email"],
            password_hash=hash_password(secrets.token_urlsafe(48)),
            role=UserRole.AUTH_USER,
            plan=PlanTier.FREE,
        )
        db.add(user)
        await db.flush()
    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is blocked",
        )

    token_data = {"sub": str(user.id), "email": user.email, "role": user.role}
    auth_service = AuthService(user_repo)
    access_token = auth_service.create_access_token(token_data)

    user_agent, ip_address = _client_context(request)
    # A fresh family_id: this SSO login is a new session, not a continuation.
    grant = auth_service.prepare_refresh_token({
        "sub": str(user.id), "email": user.email,
    })
    await RefreshTokenService(db).issue(
        user,
        token=grant.token,
        jti=grant.jti,
        expires_at=grant.expires_at,
        family_id=None,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await db.commit()

    return RedirectResponse(
        f"{app_login}?oidc_access={quote(access_token)}"
        f"&oidc_refresh={quote(grant.token)}"
        f"&oidc_expires={auth_service.access_token_lifetime_seconds()}",
        status_code=status.HTTP_302_FOUND,
    )

