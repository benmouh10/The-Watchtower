"""
GlobeLens AI — RefreshTokenService
==================================
Server-side refresh token lifecycle: issue, rotate, reuse-detect, revoke.

Why this exists
---------------
A refresh token used to be a bare JWT with no server-side record. That made two
advertised behaviours untrue:

  * "The token is rotated on refresh" — the old string was handed back a second
    time and nothing was invalidated, so rotation was cosmetic.
  * "Logout" — there was no state to clear, so a stolen refresh token stayed
    usable for its full seven-day life.

Storing a row per token makes both real. The trade-off is that refresh now needs
a database round trip, which is the point: a stateless refresh cannot be revoked.

Reuse detection
---------------
When a refresh token is spent, the old row is marked revoked and linked to its
replacement. If a *revoked* token is ever presented, the only explanations are a
replay by a network attacker or a stolen token, and both are handled the same
way: the entire family is revoked. That does punish the victim whose token was
thiefed, but the alternative — leaving the chain usable — lets the attacker keep
the session alive indefinitely while the user is told everything is fine.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.entities.models import RefreshToken, User

logger = structlog.get_logger()


def hash_refresh_token(raw_token: str) -> str:
    """
    Hex SHA-256 of a refresh token, for storage and lookup.

    SHA-256 rather than bcrypt is deliberate. Passwords are low-entropy and need
    a slow key-derivation function to make guessing expensive; a refresh token
    here is a full-entropy signed JWT protected by HS256, so there is nothing to
    brute-force and a slow hash would only add latency to the hottest auth path.

    The signature is what carries the entropy: an attacker cannot guess a valid
    token, so recovering these digests from a database backup does not help them.

    This is a one-way digest: the raw token is never persisted, so read access
    to this table does not yield usable credentials.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class RefreshTokenService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue(
        self,
        user: User,
        token: str,
        jti: str,
        expires_at: datetime,
        family_id: Optional[uuid.UUID] = None,
        replaces: Optional[RefreshToken] = None,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> RefreshToken:
        """
        Persist the record for an already-signed refresh token.

        The token is signed by AuthService and passed in rather than generated
        here, because the digest stored must be of the exact string the client
        receives. An earlier version minted its own random secret, stored that
        digest, and then returned a separately signed JWT -- so every lookup
        hashed a token the database had never seen, and refresh failed for every
        session while logging no error.

        `family_id` is created on a fresh login and inherited on rotation, which
        is what binds a chain of rotated tokens to one session.

        Returns the stored row. The token itself is not recoverable afterwards.
        """
        now = datetime.now(timezone.utc)

        row = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(token),
            family_id=family_id or uuid.uuid4(),
            jti=jti,
            issued_at=now,
            expires_at=expires_at,
            user_agent=(user_agent or None) and user_agent[:512],
            ip_address=(ip_address or None) and ip_address[:64],
        )
        self._session.add(row)
        await self._session.flush()

        if replaces is not None:
            # Close the rotation link. Both writes land in one transaction, so
            # the old token can never look unspent while the new one is live.
            replaces.revoked_at = now
            replaces.replaced_by_id = row.id

        return row

    async def find_by_token(self, raw_token: str) -> Optional[RefreshToken]:
        return (await self._session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(raw_token)
            )
        )).scalars().first()

    async def revoke_family(
        self,
        family_id: uuid.UUID,
        reason: str,
        exclude_token_id: Optional[uuid.UUID] = None,
    ) -> int:
        """
        Revoke every unspent token in a rotation chain.

        Used by both logout and reuse detection. Returns how many rows were
        newly revoked, so callers can log whether a real session was ended.
        """
        now = datetime.now(timezone.utc)
        conditions = [
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        ]
        if exclude_token_id is not None:
            conditions.append(RefreshToken.id != exclude_token_id)

        result = await self._session.execute(
            update(RefreshToken)
            .where(*conditions)
            .values(revoked_at=now)
        )
        await self._session.flush()

        count = result.rowcount or 0
        if count:
            logger.info(
                "Revoked refresh token family",
                family_id=str(family_id),
                revoked=count,
                reason=reason,
            )
        return count

    async def revoke_all_for_user(self, user: User, reason: str) -> int:
        """End every session for a user. Used when an account is blocked."""
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.info("Revoked all sessions for user", user_id=str(user.id),
                        revoked=count, reason=reason)
        return count

    async def purge_expired(self, older_than_days: int = 30) -> int:
        """
        Delete rows that expired long ago.

        Revocation history is only useful while an incident is being
        investigated, and an unbounded table of spent tokens is itself a small
        liability. Kept for a retention window rather than deleted on expiry so
        a late-arriving replay is still detected as reuse, not as unknown.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        result = await self._session.execute(
            RefreshToken.__table__.delete().where(
                RefreshToken.expires_at < cutoff
            )
        )
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.info("Purged expired refresh token rows", removed=count,
                        older_than_days=older_than_days)
        return count
