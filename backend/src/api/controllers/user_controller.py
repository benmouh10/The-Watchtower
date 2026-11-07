"""
GlobeLens AI — UserController
GET /users/me/preferences | PUT /users/me/preferences
GET /users/{id} | PUT /users/{id} | DELETE /users/{id}
"""
from fastapi import APIRouter, Path, Body, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional

from src.api.controllers.auth_controller import get_current_user
from src.data.entities.models import User
from src.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    preferred_topics: Optional[List[str]] = None
    preferred_countries: Optional[List[str]] = None


class PreferencesRequest(BaseModel):
    preferred_topics: Optional[List[str]] = None
    preferred_countries: Optional[List[str]] = None


def _preferences_response(user: User) -> dict:
    return {
        "preferred_topics": [t for t in (user.preferred_topics or []) if t] or [],
        "preferred_countries": [c for c in (user.preferred_countries or []) if c] or [],
    }


def _clean_strings(values: Optional[List[str]], label: str, limit: int = 25) -> List[str]:
    """Normalize a list of preference strings: trim, upper, dedupe, bound."""
    cleaned = list(dict.fromkeys(
        " ".join(v.split()).upper() for v in (values or []) if isinstance(v, str) and v.strip()
    ))
    if len(cleaned) > limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} accepts at most {limit} entries",
        )
    return cleaned


@router.get("/me/preferences", summary="Read the current user's preferences")
async def get_my_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service read of topic/country preferences. Not exposed on public
    profiles; this is the only supported way to read them (plus /auth/me)."""
    await db.refresh(current_user)
    return _preferences_response(current_user)


@router.put("/me/preferences", summary="Replace the current user's preferences")
async def update_my_preferences(
    payload: PreferencesRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full replace of the caller's topic/country preferences.

    Topic follow toggles (POST/DELETE /api/v1/push/following/{topic}) are a
    more convenient way to change a single topic; this endpoint replaces the
    whole lists at once.
    """
    topics = _clean_strings(payload.preferred_topics, "preferred_topics")
    countries = _clean_strings(payload.preferred_countries, "preferred_countries")
    current_user.preferred_topics = topics
    current_user.preferred_countries = countries
    await db.commit()
    await db.refresh(current_user)
    return _preferences_response(current_user)


@router.get("/{user_id}", summary="Get user profile by ID")
async def get_user(
    user_id: str = Path(...),
    current_user: User = Depends(get_current_user),
):
    """
    Not implemented. This previously returned a hardcoded
    {"name": "Placeholder User"}, i.e. fabricated profile data. Reporting the
    gap instead of inventing a user. Self-service profile data is available
    from GET /api/v1/auth/me.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Public user profiles are not implemented. Use /api/v1/auth/me for your own profile.",
    )


@router.put("/{user_id}", summary="Update user profile and preferences")
async def update_user(
    user_id: str = Path(...),
    payload: UpdateUserRequest = Body(...),
    current_user: User = Depends(get_current_user),
):
    """
    Not implemented. This previously returned "User updated" without writing
    anything, and without requiring authentication. Preferences are now
    self-service via GET/PUT /api/v1/users/me/preferences (and topic toggles
    under /api/v1/push/following); this path updates no one.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Update your preferences via PUT /api/v1/users/me/preferences or /api/v1/push/following/{topic}; nothing was changed.",
    )


@router.delete("/{user_id}", status_code=204, summary="Delete user account")
async def delete_user(
    user_id: str = Path(...),
    current_user: User = Depends(get_current_user),
):
    """
    Not implemented. Previously a public 204 that deleted nothing. Self-service
    account deletion needs a decision on what happens to the user's comments,
    so it is left unimplemented rather than faked.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Account deletion is not implemented; nothing was changed.",
    )
