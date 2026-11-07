"""
GlobeLens AI — PlanService
==========================
Plan assignment and its audit trail.

Plans were enforcement-ready labels with no way to actually move an account onto
one: registration defaulted every account to FREE and nothing could change it.
This service makes *assignment* a real, audited operation.

  * `assign()` flips user.plan and appends the audit row in the caller's
    transaction (the caller commits), so a move is never recorded separately
    from the change it describes.
  * The audit log is append-only, like usage_events: previous plans survive
    later changes, and user_id/changed_by are SET NULL on account deletion so
    the log stays truthful when an account or an administrator is removed.
  * Assignment is administrative: no payment provider is wired, so there is no
    self-serve path yet. Registration records the opening FREE row so every
    account's history begins at a known point.
"""
import uuid
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.data.entities.models import PlanChange, PlanTier, User

logger = structlog.get_logger()


class PlanService:
    """Write plan changes to the shared request session and manage their side effects."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record(
        self,
        user_id,
        to_plan: PlanTier,
        from_plan: Optional[PlanTier] = None,
        changed_by: Optional[uuid.UUID] = None,
        note: Optional[str] = None,
    ) -> PlanChange:
        """Append an audit row. Caller commits (no commit here).

        from_plan is None for the opening row at registration, meaning "the
        account was created onto to_plan" rather than "moved from nothing".
        """
        change = PlanChange(
            user_id=user_id,
            from_plan=from_plan,
            to_plan=to_plan,
            changed_by=changed_by,
            note=note,
        )
        self.db.add(change)
        return change

    async def assign(
        self,
        user: User,
        plan: PlanTier,
        changed_by: Optional[uuid.UUID] = None,
        note: Optional[str] = None,
    ) -> PlanChange:
        """Set a user's plan and record the move in one transaction.

        The caller commits, so the audit row and the column change land
        atomically. An assignment to the current plan records nothing: that
        describes no change, and re-affirmation is already visible in the audit
        log's most recent row.
        """
        from_plan = user.plan
        user.plan = plan
        return await self.record(
            user.id,
            to_plan=plan,
            from_plan=from_plan,
            changed_by=changed_by,
            note=note,
        )

    async def invalidate_rate_limit_cache(self, user_id) -> None:
        """Drop the Redis plan cache for a user and their API keys.

        The middleware caches the resolved plan for five minutes, so without
        this a freshly assigned PRO account could keep being rate-limited as
        FREE (and vice versa) until the cache expires. Only runs when
        ENFORCE_RATE_LIMITS is on: enforcement off never writes those keys, and
        touching Redis then would add an outage where none existed. A Redis
        failure here is logged, not fatal — the assignment is already committed
        and the stale cache simply ages out.
        """
        if not settings.ENFORCE_RATE_LIMITS:
            return
        from src.api.middleware.rate_limit_middleware import invalidate_user_plan_cache

        try:
            await invalidate_user_plan_cache(str(user_id))
        except Exception as exc:  # pragma: no cover - depends on Redis health
            logger.warning(
                "Could not invalidate plan rate-limit cache; stale plan may persist up to 5 minutes",
                user_id=str(user_id),
                error=str(exc),
            )


__all__ = ["PlanService"]