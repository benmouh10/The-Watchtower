"""
GlobeLens AI — User Pydantic Schemas
=====================================
Validates incoming registration/login payloads and filters API serialization.
"""
import uuid
from typing import List, Optional
from pydantic import BaseModel, EmailStr, Field

from src.data.entities.models import PlanTier, UserRole


class UserRegister(BaseModel):
    """Payload to register a new User account."""
    name: str = Field(..., min_length=2, max_length=255, description="Full name of the user")
    email: EmailStr = Field(..., description="Unique email address")
    password: str = Field(..., min_length=8, max_length=128, description="Secure account password")


class UserLogin(BaseModel):
    """Payload to request user authentication."""
    email: EmailStr = Field(..., description="Account email address")
    password: str = Field(..., description="Account password")


class UserResponse(BaseModel):
    """Sanitized User details returned in public API responses (excludes password_hash)."""
    id: uuid.UUID
    name: str
    email: EmailStr
    role: UserRole
    plan: PlanTier
    is_blocked: bool
    # Notification preferences. Exposed so the settings UI can read exactly
    # what the server stores; writes go through /users/me/preferences.
    preferred_topics: Optional[List[str]] = None
    preferred_countries: Optional[List[str]] = None

    model_config = {
        "from_attributes": True
    }


class TokenResponse(BaseModel):
    """Token payload returned upon successful authentication."""
    access_token: str
    token_type: str = "bearer"
    # Long-lived credential used only against POST /auth/refresh. It is not a
    # Bearer credential and the auth dependency rejects it outright.
    refresh_token: Optional[str] = None
    # Seconds until access_token expires, so a client can refresh proactively
    # instead of discovering expiry as a failed request.
    expires_in: Optional[int] = None


class RefreshRequest(BaseModel):
    """Body for POST /auth/refresh."""
    refresh_token: str = Field(..., description="A token whose `typ` claim is 'refresh'")


class LogoutRequest(BaseModel):
    """
    Body for POST /auth/logout.

    Optional on the endpoint: without a refresh token there is no server-side
    session to end, and the response says so rather than claiming a revocation
    that did not happen.
    """
    refresh_token: Optional[str] = Field(
        None,
        description=(
            "The refresh token to revoke. When supplied, every unspent token in "
            "the same rotation family is revoked too."
        ),
    )
