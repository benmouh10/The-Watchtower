"""Repository for threaded comments and votes."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.data.entities.models import Comment, CommentVote


class CommentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, comment_id: uuid.UUID) -> Optional[Comment]:
        result = await self.db.execute(
            select(Comment).where(Comment.id == comment_id)
        )
        return result.scalars().first()

    async def list_for_event(
        self,
        event_id: uuid.UUID,
        include_hidden: bool = False,
    ) -> List[Comment]:
        """
        Top-level comments for an event, oldest first, with replies and votes
        eagerly loaded. Threading depth is capped at one level by the API
        (parent.parent_id must be null to reply), so a flat list is enough.
        """
        stmt = (
            select(Comment)
            .options(
                # The serializer walks every reply and reads its votes, author
                # and role, so all three must be preloaded on replies too.
                # Omitting .user here triggers a lazy load inside the async
                # serializer and raises MissingGreenlet at request time.
                selectinload(Comment.replies).selectinload(Comment.votes),
                selectinload(Comment.replies).selectinload(Comment.user),
                selectinload(Comment.votes),
                selectinload(Comment.user),
            )
            .where(Comment.event_id == event_id, Comment.parent_id.is_(None))
            .order_by(Comment.created_at.asc(), Comment.id.asc())
        )
        if not include_hidden:
            stmt = stmt.where(Comment.is_hidden.is_(False))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_for_event(self, event_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(Comment)
            .where(Comment.event_id == event_id, Comment.is_hidden.is_(False))
        )
        return int(result.scalar() or 0)

    async def create(
        self,
        *,
        event_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
        parent_id: Optional[uuid.UUID] = None,
    ) -> Comment:
        comment = Comment(
            event_id=event_id,
            user_id=user_id,
            content=content,
            parent_id=parent_id,
        )
        self.db.add(comment)
        await self.db.flush()
        return comment

    async def update_content(self, comment: Comment, content: str) -> None:
        comment.content = content
        await self.db.flush()

    async def delete(self, comment: Comment) -> None:
        await self.db.delete(comment)
        await self.db.flush()

    # ── Votes ────────────────────────────────────────────────────────────────
    async def find_vote(
        self, comment_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[CommentVote]:
        result = await self.db.execute(
            select(CommentVote).where(
                CommentVote.comment_id == comment_id,
                CommentVote.user_id == user_id,
            )
        )
        return result.scalars().first()

    async def set_vote(
        self, comment_id: uuid.UUID, user_id: uuid.UUID, value: int
    ) -> None:
        """
        Apply a vote idempotently: update in place if the user already voted,
        insert otherwise. Re-sending the same direction is a no-op rather than
        an error, which keeps the client simple and non-brittle.
        """
        existing = await self.find_vote(comment_id, user_id)
        if existing:
            existing.value = value
            await self.db.flush()
            return

        self.db.add(
            CommentVote(comment_id=comment_id, user_id=user_id, value=value)
        )
        try:
            await self.db.flush()
        except IntegrityError:
            # Two concurrent votes from the same user (double-click, two tabs)
            # both miss the SELECT and race to INSERT. The unique constraint
            # catches the loser; convert it into the update the winner implies.
            await self.db.rollback()
            retry = await self.find_vote(comment_id, user_id)
            if retry is None:
                raise
            retry.value = value
            await self.db.flush()

    async def clear_vote(self, comment_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(CommentVote).where(
                CommentVote.comment_id == comment_id,
                CommentVote.user_id == user_id,
            )
        )
        await self.db.flush()

    async def vote_tally(self, comment_id: uuid.UUID) -> Dict[str, int]:
        result = await self.db.execute(
            select(CommentVote.value, func.count())
            .where(CommentVote.comment_id == comment_id)
            .group_by(CommentVote.value)
        )
        tally = {1: 0, -1: 0}
        for value, count in result.all():
            tally[int(value)] = int(count)
        return {
            "upvotes": tally[1],
            "downvotes": tally[-1],
            "score": tally[1] - tally[-1],
        }
