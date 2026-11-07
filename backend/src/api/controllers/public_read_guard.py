"""
GlobeLens AI — PublicReadGuard
==============================
Scope gate + metering for the otherwise-anonymous read surface (/events,
/sources, /digests, /search). This is what makes `read:events` and
`read:sources` real: a machine can present a `glb_` API key and its reads are
accepted *and metered*, while a reader with no credential keeps the exact
anonymous behavior those endpoints have always had.

Verdicts, per request:

  * no Bearer credential      -> anonymous public read (unchanged, unmetered)
  * access-token JWT session  -> the session's account; no scope gate (a JWT
                                 already is the whole account) and no api_request
                                 meter (this is the interactive frontend, not a
                                 programmatic consumer)
  * API key                   -> must satisfy every listed read scope, else 403;
                                 the request is recorded against the
                                 `api_request` meter so key traffic is visible
                                 in /usage/me and the admin summary.

Scope mapping for the anonymous surface:
  * read:events  -> /events/*, /search/*, /digests/* (digests are briefs
                    composed of events; search returns events)
  * read:sources -> /sources/*
"""
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import AuthIdentity, get_auth_identity_optional
from src.core.database import get_db
from src.data.entities.models import UsageEvent


def require_public_read(*required: str):
    """FastAPI dependency: admit anonymous reads and JWT sessions, gate API
    keys on `required` scopes, and meter key-authenticated requests."""

    async def _dep(
        request: Request,
        identity: Optional[AuthIdentity] = Depends(get_auth_identity_optional),
        db: AsyncSession = Depends(get_db),
    ) -> Optional[AuthIdentity]:
        if identity is None or identity.auth_kind != "api_key":
            return identity
        missing = set(required) - identity.scopes
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This API key is not permitted to read that resource. "
                    f"It requires scope(s): {', '.join(sorted(missing))}"
                ),
            )
        await _meter_api_request(db, request, identity.user.id)
        return identity

    return _dep


async def _meter_api_request(db: AsyncSession, request: Request, user_id) -> None:
    """Append one `api_request` usage row for a key-authenticated read.

    Commits here, on the guard's own session, so the meter survives even if a
    downstream handler blows up. Meta records the path so operators can tell
    which surface the traffic came in on. The meter key is taken from
    entitlement_service (the frozen vocabulary owner) rather than re-declared.
    """
    from src.services.entitlement_service import METER_API_REQUEST

    db.add(
        UsageEvent(
            user_id=user_id,
            meter=METER_API_REQUEST,
            quantity=1,
            meta={"path": request.url.path, "auth_kind": "api_key"},
        )
    )
    await db.commit()


__all__ = ["require_public_read"]