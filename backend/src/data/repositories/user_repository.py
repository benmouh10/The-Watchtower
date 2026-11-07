"""
GlobeLens AI — UserRepository
Async SQLAlchemy repository for User authentication and profile management.
"""
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.entities.models import User, UserRole
from src.api.schemas.user import UserRegister


class UserRepository:
    """Data access layer for User identity and preference management."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, user: User) -> User:
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def find_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        return await self._session.get(User, user_id)

    async def find_by_email(self, email: str) -> Optional[User]:
        """Used by AuthService to look up credentials during login."""
        result = await self._session.execute(
            select(User).where(User.email == email)
        )
        return result.scalars().first()

    async def get_by_email(self, email: str) -> Optional[User]:
        """Look up a user by their email address."""
        return await self.find_by_email(email)

    async def create(self, user_data: UserRegister, hashed_password: str) -> User:
        """Create and persist a new User entity."""
        user = User(
            name=user_data.name,
            email=user_data.email,
            password_hash=hashed_password,
            role=UserRole.AUTH_USER,
            is_blocked=False,
        )
        return await self.save(user)

    async def update(self, user: User) -> User:
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def delete(self, user: User) -> None:
        await self._session.delete(user)
        await self._session.commit()
