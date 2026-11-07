"""
GlobeLens AI — ApiKeyController
================================
Manage long-lived bearer API keys. The raw key is returned exactly once at
creation and only its SHA-256 + a display prefix are stored.

    POST   /api/v1/api-keys         create (returns raw key once)
    GET    /api/v1/api-keys         list your keys (no raw keys)
    DELETE /api/v1/api-keys/{id}    revoke one of your keys
    GET    /api/v1/api-keys/self    describe the credential you used

Scopes are validated against the frozen vocabulary in api_key_service; a typo
is refused at creation rather than minting a key that gates nothing.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import get_auth_identity, get_current_user
from src.core.database import get_db
from src.data.entities.models import ApiKey, User
from src.services.api_key_service import (
    display_prefix,
    generate_raw_key,
    hash_key,
    parse_scopes,
)

router = APIRouter()


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Human label, e.g. 'CI nightly export'")
    scopes: Optional[List[str]] = Field(
        None, description="Scopes that bound this key. Omit for a key that can "
        "only manage itself (create/list/revoke); the narrowest key is the "
        "safest key."
    )
    expires_at: Optional[datetime] = Field(
        None, description="Optional expiry. Must be in the future."
    )


def _serialize(key: ApiKey) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.key_prefix,
        "scopes": sorted(key.scopes or []),
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
        "is_active": key.revoked_at is None and (key.expires_at is None or key.expires_at > now),
    }


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key (raw secret returned once)",
)
async def create_api_key(
    payload: CreateApiKeyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A key name is required")

    scopes, scope_error = parse_scopes(payload.scopes)
    if scope_error:
        raise HTTPException(status_code=400, detail=scope_error)

    if payload.expires_at is not None:
        if payload.expires_at.tzinfo is None:
            payload.expires_at = payload.expires_at.replace(tzinfo=timezone.utc)
        if payload.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="expires_at must be in the future")

    raw_key = generate_raw_key()
    key = ApiKey(
        user_id=current_user.id,
        name=name,
        key_hash=hash_key(raw_key),
        key_prefix=display_prefix(raw_key),
        scopes=scopes,
        expires_at=payload.expires_at,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)

    return {
        **_serialize(key),
        "raw_key": raw_key,
        "warning": (
            "This is the only time the raw key is shown. It is stored as a "
            "SHA-256 hash; if lost, create a new key."
        ),
    }


@router.get("", summary="List your API keys (raw secrets never returned)")
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    keys = (await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == current_user.id)
        .order_by(ApiKey.created_at.desc())
    )).scalars().all()
    return {"items": [_serialize(k) for k in keys], "count": len(keys)}


@router.delete("/{key_id}", summary="Revoke one of your API keys")
async def revoke_api_key(
    key_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import uuid
    try:
        key_uuid = uuid.UUID(key_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid API key id")

    key = (await db.execute(
        select(ApiKey).where(
            ApiKey.id == key_uuid,
            ApiKey.user_id == current_user.id,
        )
    )).scalars().first()
    if key is None:
        # Owning key probe should not reveal existence, same as reading lists.
        raise HTTPException(status_code=404, detail="API key not found")

    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        await db.commit()
        return {"id": str(key_uuid), "name": key.name, "revoked": True}
    return {"id": str(key_uuid), "name": key.name, "revoked": False}


@router.get("/self", summary="Describe the credential you used for this request")
async def describe_credential(identity=Depends(get_auth_identity)):
    return {
        "auth_kind": identity.auth_kind,
        "user_id": str(identity.user.id),
        "email": identity.user.email,
        "role": identity.user.role.value,
        "scopes": sorted(identity.scopes),
    }