"""
GlobeLens AI — AuthService
==========================
Handles password hashing, credential verification, and JWT issuance.

Refresh tokens are minted here but their server-side lifecycle lives in
RefreshTokenService: issuing needs the signing key, while rotation and
revocation need database state, and splitting the two keeps this module free of
session bookkeeping.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, NamedTuple, Optional
import uuid

from fastapi import HTTPException, status
import bcrypt
from jose import jwt

from src.core.config import settings
from src.data.entities.models import User
from src.data.repositories.user_repository import UserRepository
from src.api.schemas.user import UserLogin, UserRegister


def hash_password(password: str) -> str:
    """Hash a plain text password using bcrypt."""
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain text password against a bcrypt hash."""
    plain_bytes = plain_password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(plain_bytes, hashed_bytes)



class RefreshGrant(NamedTuple):
    """
    A signed refresh token plus the identity of its stored row.

    These three values are produced together on purpose. `issue()` needs `jti`
    and `expires_at` to write the row, and the caller needs `token` to hand to
    the client. Computing them independently let the token and its record
    disagree about the expiry, and signing a token *after* its row was already
    hashed produced records that no presented token could ever match.
    """
    token: str
    jti: str
    expires_at: datetime


class AuthService:
    """Business logic service for user authentication and session tokens."""

    def __init__(self, user_repo: UserRepository) -> None:
        self._user_repo = user_repo

    async def register_user(self, data: UserRegister) -> User:
        """
        Register a new user in the system.
        Raises 400 Bad Request if the email is already in use.
        """
        existing_user = await self._user_repo.get_by_email(data.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already registered"
            )

        hashed = hash_password(data.password)
        return await self._user_repo.create(data, hashed)

    async def authenticate_user(self, data: UserLogin) -> User:
        """
        Authenticate a user by verifying their credentials.
        Raises 401 Unauthorized if verification fails.
        """
        user = await self._user_repo.get_by_email(data.email)
        if not user or not verify_password(data.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user.is_blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account has been blocked"
            )

        return user

    def create_access_token(self, data: Dict[str, Any]) -> str:
        """
        Generate a signed HS256 JWT access token.
        Token expiration is determined by ACCESS_TOKEN_EXPIRE_MINUTES.
        """
        return self._encode(data, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES), "access")

    def _encode(
        self,
        data: Dict[str, Any],
        lifetime: timedelta,
        token_type: str,
        jti: Optional[str] = None,
        explicit_exp: Optional[datetime] = None,
    ) -> str:
        """Shared signing path so both token kinds get an explicit `typ` claim."""
        to_encode = data.copy()
        to_encode.update({
            "exp": explicit_exp or (datetime.now(timezone.utc) + lifetime),
            "typ": token_type,
            # Without a unique id, two tokens minted in the same second with the
            # same claims are byte-identical, so issuing a "rotated" refresh
            # token would hand back the previous string and rotation would be a
            # no-op. jti also lets a revoked token be traced to its stored row.
            "jti": jti or uuid.uuid4().hex,
        })

        encoded_jwt = jwt.encode(
            to_encode,
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM
        )
        return encoded_jwt

    def access_token_lifetime_seconds(self) -> int:
        return settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    def prepare_refresh_token(self, data: Dict[str, Any]) -> RefreshGrant:
        """
        Sign an HS256 refresh token and report the `jti` and expiry to store with it.

        The token carries `typ: "refresh"` and a longer lifetime. The type claim
        is what stops a long-lived refresh token from being replayed as a
        short-lived access token, so it is set here and enforced in the auth
        dependency.

        There is deliberately no "sign a refresh token" method that does not also
        report what to record. The sign-now-store-later split is what let a
        caller persist one token's digest and return a different string, leaving
        every lookup unmatched. Callers pass the whole result to
        RefreshTokenService.issue(), so the digest in the database is always of
        the exact token the client holds.
        """
        jti = uuid.uuid4().hex
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
        token = self._encode(
            data,
            timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            "refresh",
            jti=jti,
            explicit_exp=expires_at,
        )
        return RefreshGrant(token=token, jti=jti, expires_at=expires_at)
