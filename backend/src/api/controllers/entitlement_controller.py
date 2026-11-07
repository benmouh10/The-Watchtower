"""
GlobeLens AI — EntitlementController
=====================================
Self-serve usage (any authenticated account) and admin usage summary, plus the
billing surface.

Usage (mounted at /api/v1/usage):
    GET /api/v1/usage/me             — own plan + per-meter used/allowance
    GET /api/v1/usage/me/history     — per-meter totals + daily series in a window
    GET /api/v1/usage/summary        — platform-wide metering (ADMIN)

Billing (mounted at /api/v1/billing):
    GET /api/v1/billing/subscription — plan + payment-provider state
    POST /api/v1/billing/checkout    — Stripe checkout session (503 if no provider)
    POST /api/v1/billing/webhook     — Stripe webhook, signature-verified

Nothing here invents a payment method: with no STRIPE_SECRET_KEY the provider
factory answers NoopPaymentProvider (plan_for() → stored plan,
has_payment_method() → False) and checkout refuses with an honest 503, so the
"/subscription" response cannot claim a live subscription.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import get_current_user, require_admin, require_scopes
from src.core.config import settings
from src.core.database import get_db
from src.data.entities.models import PlanTier, User
from src.services.api_key_service import KEY_SCOPE_USAGE
from src.services.entitlement_service import (
    StripePaymentProvider,
    UsageService,
    build_payment_provider,
)

usage_router = APIRouter()
billing_router = APIRouter()

_PLAN_NAMES = {t.value.lower(): PlanTier for t in PlanTier}


def _month_start() -> datetime:
    return datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


@usage_router.get(
    "/me",
    summary="Your current plan and metered usage for this month",
)
async def my_usage(
    current_user: User = Depends(require_scopes(KEY_SCOPE_USAGE)),
    db: AsyncSession = Depends(get_db),
):
    service = UsageService(db)
    return {
        "plan": current_user.plan.value,
        "reset_at": (_month_start() + timedelta(days=32)).replace(day=1).isoformat(),
        "meters": await service.summary_for(current_user),
        "note": (
            "Quota enforcement is enabled; a metered action is refused with "
            "429 once its monthly allowance is spent."
            if settings.ENFORCE_PLAN_QUOTAS
            else "Metering is recorded; quota enforcement is disabled until "
            "ENFORCE_PLAN_QUOTAS is enabled."
        ),
    }


@usage_router.get(
    "/me/history",
    summary="Your metered usage over a time window (daily series)",
)
async def my_usage_history(
    from_: Optional[str] = Query(
        None, alias="from",
        description="Inclusive window start (ISO 8601). Defaults to the month start.",
    ),
    to: Optional[str] = Query(
        None,
        description="Inclusive window end (ISO 8601). Defaults to now.",
    ),
    current_user: User = Depends(require_scopes(KEY_SCOPE_USAGE)),
    db: AsyncSession = Depends(get_db),
):
    def _parse(value: Optional[str], name: str) -> Optional[datetime]:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid {name} timestamp; use ISO 8601"
            )

    since = _parse(from_, "from") or _month_start()
    until = _parse(to, "to")
    if until is not None and since > until:
        since, until = until, since

    service = UsageService(db)
    return {
        "plan": current_user.plan.value,
        **await service.history_for(current_user.id, since=since, until=until),
    }


@usage_router.get(
    "/summary",
    summary="Platform-wide usage summary (ADMIN)",
)
async def usage_summary(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    service = UsageService(db)
    month = _month_start()
    return {
        "month": month.isoformat(),
        "month_totals": await service.global_totals(since=month),
        "all_time_totals": await service.global_totals(),
        "by_plan_this_month": await service.by_plan_totals(since=month),
        "top_users": await service.top_users(),
        "recent_events": await service.recent_events(),
    }


@billing_router.get(
    "/subscription",
    summary="Your subscription state",
)
async def subscription(
    current_user: User = Depends(get_current_user),
):
    provider = build_payment_provider()
    plan = provider.plan_for(current_user).value
    if isinstance(provider, StripePaymentProvider):
        provider_name = "stripe"
        message = (
            f"Billing is wired to Stripe; this account runs on the {plan} plan."
        )
    else:
        provider_name = None
        message = (
            "Billing is not wired to a payment provider (STRIPE_SECRET_KEY is "
            f"not configured), so no payment method is stored. This account "
            f"runs on the {plan} plan."
        )
    return {
        "plan": plan,
        "payment_provider": provider_name,
        "has_payment_method": provider.has_payment_method(current_user),
        "message": message,
    }


@billing_router.post(
    "/checkout",
    summary="Start a Stripe checkout session for a paid plan",
)
async def create_checkout(
    payload: dict,
    current_user: User = Depends(get_current_user),
):
    """
    Body: {"plan": "pro" | "enterprise"}.
    Returns a checkout URL. Refuses with 503 (honest) when no payment
    provider is configured, 400 for an unknown plan, and 422 when
    APP_BASE_URL is missing (redirect targets cannot be invented).
    """
    provider = build_payment_provider()
    plan_name = (payload or {}).get("plan")
    target = _PLAN_NAMES.get(str(plan_name or "").lower())
    if target is None or target == PlanTier.FREE:
        raise HTTPException(
            status_code=400,
            detail="Plan must be one of: pro, enterprise.",
        )
    if not isinstance(provider, StripePaymentProvider):
        raise HTTPException(
            status_code=503,
            detail=(
                "No payment provider is configured (STRIPE_SECRET_KEY is empty), "
                "so self-serve upgrades are unavailable. Contact the operator."
            ),
        )

    price_id = (
        settings.STRIPE_PRICE_PRO
        if target == PlanTier.PRO
        else settings.STRIPE_PRICE_ENTERPRISE
    )
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=(
                f"No Stripe price ID is configured for the {target.value} plan "
                "(STRIPE_PRICE_PRO / STRIPE_PRICE_ENTERPRISE)."
            ),
        )
    if not settings.APP_BASE_URL:
        raise HTTPException(
            status_code=422,
            detail=(
                "APP_BASE_URL is not configured, so checkout redirect targets "
                "cannot be built. Set it to the public frontend origin."
            ),
        )

    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY
    customer_id = provider.customer_for(current_user)
    customer = stripe.Customer.retrieve(customer_id)
    session = stripe.checkout.Session.create(
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        client_reference_id=str(current_user.id),
        customer_email=customer.get("email"),
        success_url=f"{settings.APP_BASE_URL}/pricing?checkout=success",
        cancel_url=f"{settings.APP_BASE_URL}/pricing?checkout=cancelled",
        metadata={"user_id": str(current_user.id), "plan": target.value},
    )
    return {
        "url": session.get("url"),
        "session_id": session.get("id"),
        "plan": target.value,
    }


@billing_router.post(
    "/webhook",
    summary="Stripe webhook for subscription lifecycle events",
)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Signature-verified (STRIPE_WEBHOOK_SECRET). Handles:
      checkout.session.completed      → assign the plan bought for the price
      customer.subscription.deleted   → downgrade to FREE
    Refuses with 503 when Stripe is not wired, 400 on a bad signature.
    """
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503,
            detail=(
                "Billing is not configured (STRIPE_SECRET_KEY / "
                "STRIPE_WEBHOOK_SECRET are empty)."
            ),
        )

    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        import stripe

        stripe.api_key = settings.STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(
            payload, signature, settings.STRIPE_WEBHOOK_SECRET
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature.")

    from src.services.plan_service import PlanService

    provider = build_payment_provider()
    assert isinstance(provider, StripePaymentProvider)
    object_ = event.get("data", {}).get("object", {})
    event_type = event.get("type", "")

    if event_type == "checkout.session.completed":
        user_id = object_.get("client_reference_id") or (object_.get("metadata") or {}).get("user_id")
        if not user_id:
            return {"received": True, "ignored": "no user reference"}
        price_id = ""
        for line in object_.get("display_items") or []:
            price_id = (line.get("price") or {}).get("id", "")
            break
        if not price_id:
            price_id = (object_.get("metadata") or {}).get("plan", "")
        target = provider.plan_for_price(price_id) or _PLAN_NAMES.get(
            (object_.get("metadata") or {}).get("plan", "").lower()
        )
        if target is None or target == PlanTier.FREE:
            return {"received": True, "ignored": "price not mapped to a plan"}
        user_obj = await db.get(User, user_id)
        if user_obj is not None:
            user_obj.stripe_customer_id = object_.get("customer") or user_obj.stripe_customer_id
            await PlanService(db).assign(user_obj, target)
            await db.commit()
        return {"received": True, "event": event_type, "plan": target.value}

    if event_type == "customer.subscription.deleted":
        user = None
        user_id = (object_.get("metadata") or {}).get("user_id")
        if user_id:
            user = await db.get(User, user_id)
        else:
            # Fall back to the customer id, which checkout stored on the account.
            customer_id = object_.get("customer")
            if customer_id:
                for u in (await db.execute(
                    select(User).where(User.stripe_customer_id == customer_id)
                )).scalars():
                    user = u
                    break
        if user is not None and user.plan != PlanTier.FREE:
            await PlanService(db).assign(user, PlanTier.FREE)
            await db.commit()
        return {"received": True, "event": event_type}

    return {"received": True, "event": event_type, "ignored": True}