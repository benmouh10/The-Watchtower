"""
GlobeLens AI — OrganizationController (enterprise dashboard)

    POST /api/v1/org                       create an organization (becomes owner)
    GET  /api/v1/org                       my org: info, seats, members, usage
    GET  /api/v1/org/members               list members (read-only for all)
    POST /api/v1/org/members               add by email        (owner/admin)
    PUT  /api/v1/org/members/{user_id}     change role         (owner/admin)
    DELETE /api/v1/org/members/{user_id}   remove member       (owner/admin)
    GET  /api/v1/org/usage                 org-wide meter rollup
    GET  /api/v1/org/competitors           list competitor topics
    POST /api/v1/org/competitors           upsert competitor topic (owner/admin)
    DELETE /api/v1/org/competitors/{id}    delete competitor topic (owner/admin)

Members with role owner/admin may mutate; plain members read. Creating an org
is open to any authenticated user; adding members beyond ORG_MAX_SEATS seats
returns 409.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import get_current_user
from src.core.database import get_db
from src.data.entities.models import Organization, User
from src.services.org_service import (
    ORG_ROLES,
    WRITE_ROLES,
    OrgSeatLimitReached,
    OrgService,
)

router = APIRouter()


class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)


class AddMemberRequest(BaseModel):
    email: str
    role: str = "member"


class MemberRoleRequest(BaseModel):
    role: str


class UpsertCompetitorRequest(BaseModel):
    competitor_name: str = Field(min_length=1, max_length=255)
    keywords: List[str] = Field(default_factory=list, max_length=50)
    is_active: bool = True


async def _my_org_or_404(db: AsyncSession, user_id) -> Organization:
    org = await OrgService(db).org_for_user(user_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You do not belong to an organization yet",
        )
    return org


async def _require_write(db: AsyncSession, user_id) -> Organization:
    org = await _my_org_or_404(db, user_id)
    role = await OrgService(db).member_role(user_id)
    if role not in WRITE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners and admins can modify the organization",
        )
    return org


def _parse_user_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user id")


@router.post("", status_code=201, summary="Create your organization")
async def create_org(
    body: CreateOrgRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await OrgService(db).create_org(body.name, current_user.id)
    return {"id": str(org.id), "name": org.name, "plan": org.plan, "role": "owner"}


@router.get("", summary="Your organization dashboard")
async def my_org(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = OrgService(db)
    org = await _my_org_or_404(db, current_user.id)
    members = await service.members(org)
    competitors = await service.list_competitors(org)
    return {
        "id": str(org.id),
        "name": org.name,
        "plan": org.plan,
        "created_at": org.created_at.isoformat() if org.created_at else None,
        "seats": await service.seat_usage(org),
        "members": members,
        "competitors": competitors,
        "current_user_id": str(current_user.id),
    }


@router.get("/members", summary="List organization members")
async def list_members(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await _my_org_or_404(db, current_user.id)
    members = await OrgService(db).members(org)
    return {"members": members, "count": len(members)}


@router.post("/members", status_code=201, summary="Add a member by email")
async def add_member(
    body: AddMemberRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.role not in ORG_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {ORG_ROLES}")
    org = await _require_write(db, current_user.id)
    user = (
        await db.execute(select(User).where(User.email.ilike(body.email)))
    ).scalars().first()
    if user is None:
        raise HTTPException(status_code=404, detail="No user with that email")
    try:
        await OrgService(db).add_member(org, user.id, role=body.role)
    except OrgSeatLimitReached as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"user_id": str(user.id), "email": user.email, "role": body.role}


@router.put("/members/{user_id}", summary="Change a member's role")
async def change_member_role(
    body: MemberRoleRequest,
    user_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.role not in ORG_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {ORG_ROLES}")
    org = await _require_write(db, current_user.id)
    target = _parse_user_id(user_id)
    changed = await OrgService(db).set_member_role(org, target, body.role)
    if not changed:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"user_id": str(target), "role": body.role, "changed": True}


@router.delete("/members/{user_id}", summary="Remove a member")
async def remove_member(
    user_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await _require_write(db, current_user.id)
    target = _parse_user_id(user_id)
    removed = await OrgService(db).remove_member(org, target)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"user_id": str(target), "removed": True}


@router.get("/usage", summary="Organization-wide usage rollup")
async def org_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await _my_org_or_404(db, current_user.id)
    usage = await OrgService(db).org_usage(org)
    return {"seats": await OrgService(db).seat_usage(org), **usage}


@router.get("/competitors", summary="List competitive intelligence topics")
async def list_competitors(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await _my_org_or_404(db, current_user.id)
    return {"competitors": await OrgService(db).list_competitors(org)}


@router.post("/competitors", status_code=201, summary="Upsert a competitor topic")
async def upsert_competitor(
    body: UpsertCompetitorRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await _require_write(db, current_user.id)
    item = await OrgService(db).upsert_competitor(
        org, body.competitor_name, body.keywords, body.is_active
    )
    return {
        "id": str(item.id),
        "competitor_name": item.competitor_name,
        "keywords": item.keywords,
        "is_active": item.is_active,
    }


@router.delete("/competitors/{competitor_id}", summary="Delete a competitor topic")
async def delete_competitor(
    competitor_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await _require_write(db, current_user.id)
    try:
        parsed = uuid.UUID(str(competitor_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid competitor id")
    removed = await OrgService(db).remove_competitor(org, parsed)
    if not removed:
        raise HTTPException(status_code=404, detail="Competitor topic not found")
    return {"id": str(parsed), "removed": True}