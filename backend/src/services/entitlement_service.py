"""
GlobeLens AI — Entitlements & Usage Metering
=============================================
Plan tiers, per-plan quotas, append-only usage metering, and a payment-provider
interface that is currently a no-op because no billing is wired.

Design notes:
  * Metering is an append-only log (`usage_events`). Quotas and billing are
    derived from that log at read time; nothing is ever written back into it.
  * Enforcement is config-gated by `ENFORCE_PLAN_QUOTAS` (default off). When it
    is off, `require_action_quota()` is a cheap no-op so today's endpoints keep
    their exact current behavior while still being metered. When it is on, an
    account that has used its plan's monthly allowance for a metered action is
    refused with 429.
  * NoopPaymentProvider exists so billing can be *wired* (the /billing router
    and the /usage responses reference it) without pretending a payment method
    exists. Every account registers FREE; a plan can only be changed by an
    administrator (see PlanService), so PRO/ENTERPRISE are operational
    assignments, not purchasable tiers.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_db
from src.api.controllers.auth_controller import get_current_user
from src.data.entities.models import PlanTier, UsageEvent, User

# ── Meter identifiers ─────────────────────────────────────────────────────────
# Stable string keys written into usage_events.meter. Renaming one orphans old
# rows from aggregation, so treat these as a frozen vocabulary.
METER_FACT_CHECK = "fact_check"
METER_CHAT = "chat"
METER_EXPORT = "export"
METER_API_REQUEST = "api_request"
METER_MEDIA = "media"

ALL_METERS: List[str] = [
    METER_FACT_CHECK,
    METER_CHAT,
    METER_EXPORT,
    METER_API_REQUEST,
    METER_MEDIA,
]

# Per-plan monthly allowances, in usage_events.quantity units. `None` means
# unlimited. Accounts are FREE on creation; an administrator can assign
# PRO/ENTERPRISE (PlanService), at which point these limits take effect.
PLAN_QUOTAS: Dict[PlanTier, Dict[str, Optional[int]]] = {
    PlanTier.FREE: {
        METER_FACT_CHECK: 20,
        METER_CHAT: 50,
        METER_EXPORT: None,
        METER_API_REQUEST: None,
        METER_MEDIA: 30,
    },
    PlanTier.PRO: {
        METER_FACT_CHECK: 200,
        METER_CHAT: 1000,
        METER_EXPORT: None,
        METER_API_REQUEST: None,
        METER_MEDIA: 500,
    },
    PlanTier.ENTERPRISE: {
        METER_FACT_CHECK: None,
        METER_CHAT: None,
        METER_EXPORT: None,
        METER_API_REQUEST: None,
        METER_MEDIA: None,
    },
}


def _month_start() -> datetime:
    return datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def quota_for(plan: PlanTier, meter: str) -> Optional[int]:
    """Return the monthly allowance for a meter on a plan; None = unlimited."""
    return PLAN_QUOTAS.get(plan, PLAN_QUOTAS[PlanTier.FREE]).get(meter)


# ── Payment provider ──────────────────────────────────────────────────────────
class PaymentProvider(Protocol):
    """What a billing integration must answer. Default is Noop below."""

    name: str

    def plan_for(self, user: User) -> PlanTier:
        ...

    def has_payment_method(self, user: User) -> bool:
        ...


class NoopPaymentProvider:
    """
    The wiring point for billing, with no backend attached.
    `plan_for` returns the account's stored plan untouched and
    `has_payment_method` is always False, so no code path can assume a live
    subscription exists. This is deliberate: without it, a future billing
    branch could be merged half-wired and report a Free account as paid.
    """

    name = "none"

    def plan_for(self, user: User) -> PlanTier:
        return user.plan

    def has_payment_method(self, user: User) -> bool:
        return False


class StripePaymentProvider:
    """
    Stripe-backed provider, selected by build_payment_provider() when
    STRIPE_SECRET_KEY is set. The stripe SDK is imported lazily so the rest of
    the app runs without it when billing is off. Plan changes are NOT made
    here: the billing webhook maps a price/customer onto PlanService so every
    upgrade stays in the audited plan_change log.
    """

    name = "stripe"

    def __init__(
        self,
        api_key: str,
        price_pro: str = "",
        price_enterprise: str = "",
    ) -> None:
        self._api_key = api_key
        self._prices = {
            price_pro: PlanTier.PRO,
            price_enterprise: PlanTier.ENTERPRISE,
        }

    @property
    def api_key(self) -> str:
        return self._api_key

    def plan_for_price(self, price_id: str) -> Optional[PlanTier]:
        return self._prices.get(price_id)

    def plan_for(self, user: User) -> PlanTier:
        return PlanTier(user.plan)

    def has_payment_method(self, user: User) -> bool:
        if not getattr(user, "stripe_customer_id", None):
            return False
        try:
            import stripe

            stripe.api_key = self._api_key
            customer = stripe.Customer.retrieve(user.stripe_customer_id)
            return bool(
                customer.get("default_source") or customer.get("invoice_settings", {}).get("default_payment_method")
            )
        except Exception:
            # A billing outage must not be reported as "no payment method".
            return False

    def customer_for(self, user: User) -> str:
        """Return the Stripe customer id for this account, creating one lazily."""
        import stripe

        stripe.api_key = self._api_key
        if getattr(user, "stripe_customer_id", None):
            return user.stripe_customer_id
        customer = stripe.Customer.create(
            email=user.email,
            name=user.name,
            metadata={"user_id": str(user.id)},
        )
        user.stripe_customer_id = customer.id
        return customer.id


def build_payment_provider() -> PaymentProvider:
    """Stripe when STRIPE_SECRET_KEY is set, else the honest Noop provider."""
    if settings.STRIPE_SECRET_KEY:
        return StripePaymentProvider(
            api_key=settings.STRIPE_SECRET_KEY,
            price_pro=settings.STRIPE_PRICE_PRO,
            price_enterprise=settings.STRIPE_PRICE_ENTERPRISE,
        )
    return NoopPaymentProvider()


# ── Usage service ─────────────────────────────────────────────────────────────
class UsageService:
    """Write meters and derive quotas. Reads/writes the shared request session."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        user_id: Optional[Any],
        meter: str,
        quantity: int = 1,
        meta: Optional[dict] = None,
    ) -> UsageEvent:
        """Append a metered action. Caller commits (no commit here)."""
        event = UsageEvent(user_id=user_id, meter=meter, quantity=quantity, meta=meta)
        self.db.add(event)
        return event

    async def window_total(
        self, user_id: Any, meter: str, since: datetime
    ) -> int:
        total = await self.db.scalar(
            select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
                UsageEvent.user_id == user_id,
                UsageEvent.meter == meter,
                UsageEvent.created_at >= since,
            )
        )
        return int(total or 0)

    async def monthly_total(self, user_id: Any, meter: str) -> int:
        return await self.window_total(user_id, meter, _month_start())

    async def check(self, user: User, meter: str) -> tuple:
        """
        (over_quota, used, limit) for a meter on this account.
        limit is None (unlimited) → over_quota is always False.
        """
        limit = quota_for(user.plan, meter)
        used = await self.monthly_total(user.id, meter)
        over = bool(settings.ENFORCE_PLAN_QUOTAS and limit is not None and used >= limit)
        return over, used, limit

    async def history_for(
        self, user_id: Any, since: Optional[datetime] = None, until: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Per-meter totals and a daily per-meter series inside a time window."""
        clauses = [UsageEvent.user_id == user_id]
        if since is not None:
            clauses.append(UsageEvent.created_at >= since)
        if until is not None:
            clauses.append(UsageEvent.created_at <= until)

        totals_row = await self.db.execute(
            select(UsageEvent.meter, func.coalesce(func.sum(UsageEvent.quantity), 0))
            .where(*clauses)
            .group_by(UsageEvent.meter)
        )
        totals = {meter: int(total) for meter, total in totals_row.all()}
        for meter in ALL_METERS:
            totals.setdefault(meter, 0)

        day = func.date_trunc("day", UsageEvent.created_at).label("day")
        daily_row = await self.db.execute(
            select(day, UsageEvent.meter, func.coalesce(func.sum(UsageEvent.quantity), 0))
            .where(*clauses)
            .group_by(day, UsageEvent.meter)
            .order_by(day)
        )
        series: Dict[str, List[Dict[str, object]]] = {}
        for bucket, meter, qty in daily_row.all():
            series.setdefault(meter, []).append(
                {"date": bucket.date().isoformat(), "quantity": int(qty)}
            )

        return {
            "from": since.isoformat() if since else None,
            "to": until.isoformat() if until else None,
            "totals": totals,
            "daily": series,
        }

    async def over_quota(self, user: User, meter: str) -> bool:
        over, _used, _limit = await self.check(user, meter)
        return over

    async def summary_for(self, user: User) -> Dict[str, dict]:
        """Per-meter used/limit for one account, for /usage/me."""
        return {
            meter: {
                "used": await self.monthly_total(user.id, meter),
                "limit": quota_for(user.plan, meter),
            }
            for meter in ALL_METERS
        }

    async def global_totals(self, since: Optional[datetime] = None) -> Dict[str, dict]:
        """Meter totals across all accounts (admin)."""
        stmt = select(
            UsageEvent.meter,
            func.sum(UsageEvent.quantity),
            func.count(UsageEvent.id),
        ).group_by(UsageEvent.meter)
        if since is not None:
            stmt = stmt.where(UsageEvent.created_at >= since)
        rows = (await self.db.execute(stmt)).all()
        return {
            meter: {"quantity": int(quantity or 0), "events": int(count)}
            for meter, quantity, count in rows
        }

    async def by_plan_totals(self, since: Optional[datetime] = None) -> List[dict]:
        """Meter totals grouped by account plan (admin). plan null = deleted account."""
        stmt = (
            select(User.plan, UsageEvent.meter, func.sum(UsageEvent.quantity))
            .join(User, User.id == UsageEvent.user_id, isouter=True)
            .group_by(User.plan, UsageEvent.meter)
        )
        if since is not None:
            stmt = stmt.where(UsageEvent.created_at >= since)
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "plan": plan.value if plan else None,
                "meter": meter,
                "quantity": int(quantity or 0),
            }
            for plan, meter, quantity in rows
        ]

    async def top_users(self, limit: int = 10, since: Optional[datetime] = None) -> List[dict]:
        """Highest-total accounts by metered quantity (admin)."""
        stmt = (
            select(
                User.id,
                User.email,
                User.plan,
                func.sum(UsageEvent.quantity).label("total_quantity"),
            )
            .join(User, User.id == UsageEvent.user_id)
            .group_by(User.id, User.email, User.plan)
            .order_by(func.sum(UsageEvent.quantity).desc())
            .limit(limit)
        )
        if since is not None:
            stmt = stmt.where(UsageEvent.created_at >= since)
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "user_id": str(user_id),
                "email": email,
                "plan": plan.value,
                "total_quantity": int(total or 0),
            }
            for user_id, email, plan, total in rows
        ]

    async def recent_events(self, limit: int = 25) -> List[dict]:
        rows = (await self.db.execute(
            select(UsageEvent).order_by(UsageEvent.created_at.desc()).limit(limit)
        )).scalars().all()
        return [
            {
                "id": str(ev.id),
                "user_id": str(ev.user_id) if ev.user_id else None,
                "meter": ev.meter,
                "quantity": ev.quantity,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            }
            for ev in rows
        ]


# ── Feature-gate dependency ────────────────────────────────────────────────────
def require_action_quota(meter: str):
    """
    Build a FastAPI dependency that refuels per-meter usage on the caller's
    plan. A no-op while ENFORCE_PLAN_QUOTAS is off; 429 once the allowance is
    spent when it is on. Applied to the endpoints that actually spend budget.
    """

    async def _guard(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        if not settings.ENFORCE_PLAN_QUOTAS:
            return
        service = UsageService(db)
        over, used, limit = await service.check(current_user, meter)
        if over:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"{meter} allowance for {current_user.plan.value} exceeded "
                    f"({used}/{limit} this month)."
                ),
                headers={"Retry-After": "2592000"},
            )

    return _guard


__all__ = [
    "ALL_METERS",
    "METER_API_REQUEST",
    "METER_CHAT",
    "METER_EXPORT",
    "METER_FACT_CHECK",
    "NoopPaymentProvider",
    "PaymentProvider",
    "PlanTier",
    "UsageService",
    "quota_for",
    "require_action_quota",
]