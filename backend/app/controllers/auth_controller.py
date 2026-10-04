"""
GlobeLens AI — AuthController
POST /auth/register | /auth/login | /auth/logout | /auth/refresh
GET  /auth/me
"""
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.entities.models import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import (
    UserRegister,
    UserLogin,
    UserResponse,
    TokenResponse
)
from app.services import audio_signing
from app.services.auth_service import AuthService
from app.services.cache_service import cache_service

router = APIRouter()
security = HTTPBearer(auto_error=False)
# Same scheme instance, exported so routes that accept either a bearer token or a
# signed playback URL can resolve both without declaring the scheme twice.
bearer = security


class PlaybackListener:
    """Identity carried by a signed playback URL instead of a bearer token.

    A media element cannot send an Authorization header, so the narration routes
    also accept a short-lived signed link. The fields mirror the ones those routes
    read off a User, so the visibility rules stay identical either way.
    """

    __slots__ = ("id", "role")

    def __init__(self, user_id: uuid.UUID, role: str) -> None:
        self.id = user_id
        self.role = role


async def resolve_listener(
    resource: str,
    sig: Optional[str],
    credentials: Optional[HTTPAuthorizationCredentials],
    db: AsyncSession,
):
    """Return who is asking for `resource`, from a signed URL or a bearer token.

    `resource` is scoped by the route ("article:<uuid>"), so a link minted for one
    briefing cannot be edited to fetch another. A signature that fails verification
    is rejected outright rather than quietly falling back to anonymous access.
    """
    if sig:
        verified = audio_signing.verify(resource, sig)
        if verified is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Playback link is invalid or has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return PlaybackListener(verified["user_id"], verified["role"])
    return await get_current_user(credentials, db)


# ── Dependency for fetching current authenticated user ─────────────────────────
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Dependency to validate access token and return current active user.
    """
    # HTTPBearer's built-in rejection of a missing header is a 403, which tells
    # the client "you are forbidden" rather than "authenticate first". RFC 7235
    # wants 401 + WWW-Authenticate, so the missing case is handled here.
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id = uuid.UUID(user_id_str)
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_repo = UserRepository(db)
    user = await user_repo.find_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Logout revokes a token rather than waiting for it to expire. Without this
    # check the blacklist would only stop the /refresh exchange while the
    # original token kept working on every other endpoint.
    if await cache_service.get(f"auth:revoked:{hashlib.sha256(token.encode()).hexdigest()}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been logged out",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account has been blocked"
        )
    return user


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Return the caller when a valid token is present, otherwise None.

    Some read endpoints are public but expose extra data to their owner — an
    author previewing an unpublished draft, for instance. Reusing
    get_current_user and swallowing its 401 keeps the two paths consistent
    (revocation, block checks, role resolution) instead of duplicating them.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


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
    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and obtain JWT"
)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    """
    Authenticate user, returning a signed HS256 JWT containing sub, email, and role.
    """
    user_repo = UserRepository(db)
    auth_service = AuthService(user_repo)
    user = await auth_service.authenticate_user(payload)

    # Issue access token
    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role
    }
    access_token = auth_service.create_access_token(token_data)
    return TokenResponse(access_token=access_token)


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


@router.post("/logout", summary="Invalidate session / blacklist token")
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    """
    Revoke the presented token until it would have expired anyway.

    This previously answered "Logged out successfully" and left the token
    fully valid, so on a shared machine the previous user stayed logged in
    until the 30 minute expiry. The revocation entry lives in Redis with a TTL
    equal to the token's remaining lifetime, so the list cannot grow stale.
    """
    # This route depends on `security` directly rather than get_current_user, so
    # the missing-header case has to be handled here too. auto_error=False lets
    # None through, and touching .credentials on it would raise a 500.
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    payload = _decode_token(token)

    # Seconds until the token would have expired on its own. A token with no
    # exp is treated as already expired rather than revoking forever.
    exp = payload.get("exp")
    ttl = int(exp - datetime.now(timezone.utc).timestamp()) if exp else 0
    if ttl <= 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has already expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    digest = hashlib.sha256(token.encode()).hexdigest()
    revoked = await cache_service.set(f"auth:revoked:{digest}", "1", ttl_seconds=ttl)
    if not revoked:
        # Do not claim a logout that did not happen.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not record session revocation; token remains valid",
        )
    return {"message": "Logged out successfully", "revoked_until": exp}


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Issue new access token using refresh token"
)
async def refresh_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    """
    Exchange a still-valid, non-revoked access token for a fresh one.

    Previously returned the literal string "new_placeholder_jwt_token". A
    client that trusted it would store a value that is not a JWT and be
    silently signed out on the next request, with no error to explain why.
    """
    # Same guard as /logout: auto_error=False lets a missing header through as
    # None, and dereferencing it would be a 500 rather than the expected 401.
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    payload = _decode_token(token)

    digest = hashlib.sha256(token.encode()).hexdigest()
    if await cache_service.get(f"auth:revoked:{digest}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await db.get(User, user_uuid)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is blocked",
        )

    auth_service = AuthService(UserRepository(db))
    return TokenResponse(access_token=auth_service.create_access_token({"sub": str(user.id)}))


def _decode_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

