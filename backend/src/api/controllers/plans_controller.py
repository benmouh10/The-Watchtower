"""
GlobeLens AI — PlansController
=================================
Public plan catalogue: per-tier quotas (the same PLAN_QUOTAS the meter enforces)
plus a declarative feature matrix, delivered for the pricing page.

Mounted at /api/v1/plans:
    GET /api/v1/plans — tiers, quotas, features, enforcement and billing state

The response only reports facts that are true of the running deployment: quota
numbers come from the enforcement source of truth (entitlement_service), and
the billing line reflects whether a Stripe key is configured. A pricing page
built on this cannot invent allowances or payment methods.
"""
from typing import Any, Dict

from fastapi import APIRouter

from src.core.config import settings
from src.data.entities.models import PlanTier
from src.services.entitlement_service import (
    ALL_METERS,
    METER_CHAT,
    METER_EXPORT,
    METER_FACT_CHECK,
    METER_MEDIA,
    PLAN_QUOTAS,
    quota_for,
)

router = APIRouter()

# Plan feature templates: the human-readable slice of what each tier includes.
# These are declarative copy, not enforcement logic — quotas stay in
# PLAN_QUOTAS and this table never gates a request.
FEATURE_TEMPLATES: Dict[PlanTier, Dict[str, Any]] = {
    PlanTier.FREE: {
        "name": "Free",
        "tagline": "Browse the GlobeLens feed and read AI-synthesized dossiers.",
        "features": [
            "Unlimited browsing of the global event map, feed and search",
            "AI-synthesized event dossiers with per-source citations",
            "Realtime Processor events stream (when enabled)",
            f"{quota_for(PlanTier.FREE, METER_FACT_CHECK)} fact-checks / month",
            f"{quota_for(PlanTier.FREE, METER_CHAT)} AI News-Scout chats / month",
            "Unlimited reading-list exports (HTML / PDF)",
        ],
    },
    PlanTier.PRO: {
        "name": "Pro",
        "tagline": "For analysts and power readers who follow many events.",
        "features": [
            "Everything in Free",
            f"{quota_for(PlanTier.PRO, METER_FACT_CHECK)} fact-checks / month",
            f"{quota_for(PlanTier.PRO, METER_CHAT)} AI News-Scout chats / month",
            "Higher daily API rate-limit bucket",
        ],
    },
    PlanTier.ENTERPRISE: {
        "name": "Enterprise",
        "tagline": "Team deployment with uncapped AI tooling and SSO access.",
        "features": [
            "Everything in Pro",
            "Unlimited fact-checks and AI News-Scout chats",
            "No daily API rate cap",
            "Organization-wide single sign-on (when configured)",
        ],
    },
}

_METER_LABELS: Dict[str, str] = {
    METER_FACT_CHECK: "Fact-checks / month",
    METER_CHAT: "AI News-Scout chats / month",
    METER_EXPORT: "Reading-list exports",
    METER_MEDIA: "AI caption / transcription / month",
}


@router.get("", summary="Public plan catalogue with quotas and features")
async def list_plans():
    tiers = []
    for tier in (PlanTier.FREE, PlanTier.PRO, PlanTier.ENTERPRISE):
        template = FEATURE_TEMPLATES[tier]
        tiers.append(
            {
                "plan": tier.value.lower(),
                "name": template["name"],
                "tagline": template["tagline"],
                "features": template["features"],
                "quotas": {
                    meter: quota_for(tier, meter) for meter in ALL_METERS
                },
                "quota_labels": {m: _METER_LABELS.get(m, m) for m in ALL_METERS},
            }
        )
    return {
        "tiers": tiers,
        "enforcement": {
            "enabled": settings.ENFORCE_PLAN_QUOTAS,
            "note": (
                "Monthly quotas are enforced with 429 once the allowance is spent."
                if settings.ENFORCE_PLAN_QUOTAS
                else "Metering is recorded but quotas are not enforced until "
                "ENFORCE_PLAN_QUOTAS is enabled."
            ),
        },
        "billing": {
            "stripe_configured": bool(settings.STRIPE_SECRET_KEY),
            "self_serve_upgrade": bool(settings.STRIPE_SECRET_KEY),
            "note": (
                "Self-serve upgrades are available through Stripe checkout."
                if settings.STRIPE_SECRET_KEY
                else "No payment provider is configured, so this deployment "
                "does not offer self-serve upgrades yet."
            ),
        },
    }