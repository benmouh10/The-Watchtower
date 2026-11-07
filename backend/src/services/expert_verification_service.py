"""
GlobeLens AI — ExpertVerificationService
========================================
The expert-badge workflow behind comment authority signals.

An administrator grants a user an expert title (e.g. "Verified economist").
Only the single most recent active row per user counts; granting again
revokes the previous badge and writes a fresh one, so the badge is always
the administrator's latest judgement rather than a stack of stale titles.
Revoking sets is_active=false with a revoked_at timestamp so the audit trail
(points to the actor, when, and what) survives the change.
"""
from datetime import datetime, timezone
from typing import Dict, List, Mapping, Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.entities.models import ExpertVerification, User


async def active_badges_for_user_ids(
    session: AsyncSession, user_ids: List[UUID]
) -> Dict[UUID, str]:
    """Map user_id -> badge_title for active expert rows (batch, no N+1)."""
    if not user_ids:
        return {}
    stmt = (
        select(ExpertVerification.user_id, ExpertVerification.badge_title)
        .where(
            ExpertVerification.user_id.in_(user_ids),
            ExpertVerification.is_active.is_(True),
        )
    )
    rows = await session.execute(stmt)
    return {str(user_id): badge_title for user_id, badge_title in rows.all()}


async def is_expert(session: AsyncSession, user_id: UUID) -> Optional[Mapping[str, object]]:
    """The active expert badge for one user, or None."""
    stmt = (
        select(ExpertVerification)
        .where(
            ExpertVerification.user_id == user_id,
            ExpertVerification.is_active.is_(True),
        )
        .order_by(ExpertVerification.verified_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    return {
        "user_id": str(row.user_id),
        "badge_title": row.badge_title,
        "verified_by": str(row.verified_by) if row.verified_by else None,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
    }


async def active_rows(session: AsyncSession) -> List[Mapping[str, object]]:
    """All active badges joined with the bearer name, newest first."""
    stmt = (
        select(ExpertVerification, User)
        .join(User, User.id == ExpertVerification.user_id)
        .where(ExpertVerification.is_active.is_(True))
        .order_by(ExpertVerification.verified_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "user_id": str(ev.user_id),
            "name": user.name,
            "badge_title": ev.badge_title,
            "verified_by": str(ev.verified_by) if ev.verified_by else None,
            "verified_at": ev.verified_at.isoformat() if ev.verified_at else None,
        }
        for ev, user in rows
    ]


async def grant(
    session: AsyncSession, *, user_id: UUID, badge_title: str, granted_by: Optional[UUID]
) -> ExpertVerification:
    """Make `badge_title` the user's active expert badge, revoking any prior one."""
    target = (
        await session.execute(
            select(User).where(User.id == user_id)
        )
    ).scalars().first()
    if target is None:
        raise ValueError("user not found")

    now = datetime.now(timezone.utc)
    await session.execute(
        update(ExpertVerification)
        .where(
            ExpertVerification.user_id == user_id,
            ExpertVerification.is_active.is_(True),
        )
        .values(is_active=False, revoked_at=now)
    )
    verification = ExpertVerification(
        user_id=user_id,
        verified_by=granted_by,
        badge_title=badge_title.strip() or None,
        is_active=True,
        verified_at=now,
    )
    session.add(verification)
    await session.flush()
    return verification


async def revoke(session: AsyncSession, *, user_id: UUID, revoked_by: Optional[UUID]) -> bool:
    """Deactivate the user's current expert badge. Returns True if one existed."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(ExpertVerification)
        .where(
            ExpertVerification.user_id == user_id,
            ExpertVerification.is_active.is_(True),
        )
        .values(is_active=False, revoked_at=now)
    )
    return bool(result.rowcount and result.rowcount > 0)