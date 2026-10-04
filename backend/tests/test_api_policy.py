"""
Enforce the guest/account access matrix across every registered route.

This is the guardrail behind "GUEST can look but cannot act": the test walks the
real application and fails if a route is reachable anonymously while not being on
the explicit guest allowlist. A new endpoint is therefore deny-by-default until
someone classifies it, instead of silently exposing itself.
"""
from fastapi.security import HTTPBearer

from app.controllers.auth_controller import get_current_user
from app.entities.models import UserRole
from app.main import app

# Routes an anonymous visitor is intentionally allowed to call: the public
# reading surface plus the account/newsletter bootstrap endpoints. Anything not
# listed here must carry an authentication or role gate.
GUEST_ALLOWED = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/favicon.ico"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("GET", "/api/v1/events"),
    ("GET", "/api/v1/events/trending"),
    ("GET", "/api/v1/events/latest"),
    ("GET", "/api/v1/events/map"),
    ("GET", "/api/v1/events/country/{country}"),
    ("GET", "/api/v1/events/topic/{topic}"),
    ("GET", "/api/v1/events/{event_id}"),
    ("GET", "/api/v1/events/{event_id}/related"),
    ("POST", "/api/v1/events/{event_id}/follow"),
    ("GET", "/api/v1/events/{event_id}/comments"),
    ("GET", "/api/v1/articles/authored"),
    ("GET", "/api/v1/articles/{article_id}"),
    ("GET", "/api/v1/articles/{article_id}/comments"),
    # GET /api/v1/articles/{article_id}/audio is deliberately absent: speech
    # synthesis is billed per call, so narration requires an account.
    ("GET", "/api/v1/search/query"),
    ("GET", "/api/v1/search/autocomplete"),
    ("POST", "/api/v1/newsletter/subscribe"),
    ("GET", "/api/v1/newsletter/confirm"),
    ("GET", "/api/v1/newsletter/unsubscribe"),
    ("GET", "/api/v1/push/vapid-public-key"),
}


def _is_gated(route) -> bool:
    """True when a top-level dependency authenticates or enforces a role tier."""
    for dependency in route.dependant.dependencies:
        call = dependency.call
        if call is get_current_user:
            return True
        if isinstance(call, HTTPBearer):
            # Routes that read the raw bearer token themselves (logout/refresh).
            return True
        minimum = getattr(call, "__rbac_min__", None)
        if minimum is not None and minimum != UserRole.GUEST:
            return True
    return False


def _anonymous_reachable_routes() -> set:
    found = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods or not hasattr(route, "dependant"):
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            if not _is_gated(route):
                found.add((method, route.path))
    return found


def test_every_route_is_gated_or_explicitly_guest():
    found = _anonymous_reachable_routes()
    undocumented = found - GUEST_ALLOWED
    stale = GUEST_ALLOWED - found
    assert not undocumented, (
        "Anonymous-reachable routes missing from GUEST_ALLOWED: "
        f"{sorted(undocumented)}"
    )
    assert not stale, (
        "GUEST_ALLOWED entries that are actually gated or no longer exist: "
        f"{sorted(stale)}"
    )


def test_narration_is_never_public():
    """Playback costs CPU per call, so no narration route may skip authentication.

    Worth stating separately from the allowlist above: a signed playback URL is only
    as private as the route that mints it, so both halves have to stay gated.
    """
    reachable = _anonymous_reachable_routes()
    exposed = sorted(path for _, path in reachable if path.endswith("/audio") or path.endswith("/audio/url"))
    assert not exposed, f"narration routes became anonymous: {exposed}"
