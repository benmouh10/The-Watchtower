"""
GlobeLens AI — Enterprise org dashboard (teams, seats, usage rollup).

Organizations group users for enterprise plans. OrgService owns membership
(owner/admin/member, seat-capped via ORG_MAX_SEATS), competitive intelligence
topics and an org-wide usage rollup derived from per-user UsageEvent rows.
"""
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.data.entities.models import CompetitorTopic, OrgMember, Organization, UsageEvent, User

ORG_ROLE_OWNER = "owner"
ORG_ROLE_ADMIN = "admin"
ORG_ROLE_MEMBER = "member"
ORG_ROLES = (ORG_ROLE_OWNER, ORG_ROLE_ADMIN, ORG_ROLE_MEMBER)

WRITE_ROLES = (ORG_ROLE_OWNER, ORG_ROLE_ADMIN)


class OrgSeatLimitReached(Exception):
    pass


class OrgService:
    def __init__(self, db: AsyncSession):
        self._db = db

    # ── membership ──────────────────────────────────────────────────────────
    async def org_for_user(self, user_id) -> Optional[Organization]:
        row = (
            await self._db.execute(
                select(Organization, OrgMember)
                .join(OrgMember, OrgMember.organization_id == Organization.id)
                .where(OrgMember.user_id == user_id)
            )
        ).first()
        return row[0] if row else None

    async def member_role(self, user_id) -> Optional[str]:
        row = (
            await self._db.execute(
                select(OrgMember)
                .join(Organization, OrgMember.organization_id == Organization.id)
                .where(OrgMember.user_id == user_id)
            )
        ).scalars().first()
        return row.role if row else None

    async def create_org(self, name: str, owner_user_id) -> Organization:
        existing = await self.org_for_user(owner_user_id)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already belongs to an organization",
            )
        org = Organization(name=name, created_by=owner_user_id)
        self._db.add(org)
        await self._db.flush()
        self._db.add(
            OrgMember(organization_id=org.id, user_id=owner_user_id, role=ORG_ROLE_OWNER)
        )
        await self._db.commit()
        await self._db.refresh(org)
        return org

    async def add_member(self, org: Organization, user_id, role: str = ORG_ROLE_MEMBER) -> OrgMember:
        seat_count = await self._db.scalar(
            select(func.count(OrgMember.id)).where(OrgMember.organization_id == org.id)
        )
        if role not in ORG_ROLES:
            raise HTTPException(status_code=422, detail=f"role must be one of {ORG_ROLES}")
        if org.plan == "ENTERPRISE" and seat_count is not None and seat_count >= settings.ORG_MAX_SEATS:
            raise OrgSeatLimitReached(f"organization seat limit of {settings.ORG_MAX_SEATS} reached")
        existing = (
            await self._db.execute(
                select(OrgMember).where(
                    OrgMember.organization_id == org.id,
                    OrgMember.user_id == user_id,
                )
            )
        ).scalars().first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="User is already a member")
        member = OrgMember(organization_id=org.id, user_id=user_id, role=role)
        self._db.add(member)
        await self._db.commit()
        await self._db.refresh(member)
        return member

    async def remove_member(self, org: Organization, user_id) -> bool:
        member = (
            await self._db.execute(
                select(OrgMember).where(
                    OrgMember.organization_id == org.id,
                    OrgMember.user_id == user_id,
                )
            )
        ).scalars().first()
        if member is None:
            return False
        if member.role == ORG_ROLE_OWNER:
            raise HTTPException(status_code=422, detail="The owner cannot be removed")
        await self._db.delete(member)
        await self._db.commit()
        return True

    async def set_member_role(self, org: Organization, user_id, role: str) -> bool:
        if role not in ORG_ROLES:
            raise HTTPException(status_code=422, detail=f"role must be one of {ORG_ROLES}")
        member = (
            await self._db.execute(
                select(OrgMember).where(
                    OrgMember.organization_id == org.id,
                    OrgMember.user_id == user_id,
                )
            )
        ).scalars().first()
        if member is None:
            return False
        if member.role == ORG_ROLE_OWNER and role != ORG_ROLE_OWNER:
            raise HTTPException(status_code=422, detail="The owner cannot be demoted")
        member.role = role
        await self._db.commit()
        return True

    async def members(self, org: Organization) -> List[Dict[str, Any]]:
        rows = (await self._db.execute(
            select(OrgMember, User)
            .join(User, OrgMember.user_id == User.id)
            .where(OrgMember.organization_id == org.id)
            .order_by(OrgMember.created_at)
        )).all()
        return [
            {
                "user_id": str(member.user_id),
                "name": user.name,
                "email": user.email,
                "role": member.role,
                "created_at": member.created_at.isoformat() if member.created_at else None,
            }
            for member, user in rows
        ]

    async def seat_usage(self, org: Organization) -> Dict[str, Any]:
        seat_count = await self._db.scalar(
            select(func.count(OrgMember.id)).where(OrgMember.organization_id == org.id)
        )
        limit = settings.ORG_MAX_SEATS if org.plan == "ENTERPRISE" else None
        return {
            "used": int(seat_count or 0),
            "limit": limit,
            "plan": org.plan,
        }

    # ── usage rollup ─────────────────────────────────────────────────────────
    async def org_usage(self, org: Organization) -> Dict[str, Any]:
        rows = (await self._db.execute(
            select(UsageEvent)
            .join(OrgMember, OrgMember.user_id == UsageEvent.user_id)
            .where(OrgMember.organization_id == org.id)
        )).all()
        totals: Dict[str, int] = {}
        for (event,) in rows:
            meter = event.meter or "unknown"
            totals[meter] = totals.get(meter, 0) + (event.quantity or 1)
        return {"members": len(set(r[0].user_id for r in rows)), "meters": totals}

    # ── competitive intelligence ─────────────────────────────────────────────
    async def list_competitors(self, org: Organization) -> List[Dict[str, Any]]:
        rows = (await self._db.execute(
            select(CompetitorTopic)
            .where(CompetitorTopic.organization_id == org.id)
            .order_by(CompetitorTopic.created_at)
        )).scalars().all()
        return [
            {
                "id": str(c.id),
                "competitor_name": c.competitor_name,
                "keywords": c.keywords,
                "is_active": c.is_active,
            }
            for c in rows
        ]

    async def upsert_competitor(
        self, org: Organization, competitor_name: str, keywords: List[str], is_active: bool = True
    ) -> CompetitorTopic:
        existing = (
            await self._db.execute(
                select(CompetitorTopic).where(
                    CompetitorTopic.organization_id == org.id,
                    CompetitorTopic.competitor_name == competitor_name,
                )
            )
        ).scalars().first()
        if existing is None:
            existing = CompetitorTopic(
                organization_id=org.id,
                competitor_name=competitor_name,
            )
            self._db.add(existing)
        existing.keywords = keywords
        existing.is_active = is_active
        await self._db.commit()
        await self._db.refresh(existing)
        return existing

    async def remove_competitor(self, org: Organization, competitor_id) -> bool:
        result = await self._db.execute(
            CompetitorTopic.__table__.delete().where(
                CompetitorTopic.organization_id == org.id,
                CompetitorTopic.id == competitor_id,
            )
        )
        await self._db.commit()
        return (result.rowcount or 0) > 0