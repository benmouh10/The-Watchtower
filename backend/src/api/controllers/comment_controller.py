"""
GlobeLens AI — CommentController
Threaded community discussion on Events, with quality voting and
admin-granted expert badges.

Routes (this router is mounted at /api/v1/comments):
    GET    /api/v1/comments/events/{event_id}
    POST   /api/v1/comments/events/{event_id}
    PUT    /api/v1/comments/{comment_id}
    DELETE /api/v1/comments/{comment_id}
    POST   /api/v1/comments/{comment_id}/vote
    DELETE /api/v1/comments/{comment_id}/vote

Note: the previous version registered "/events/{event_id}/comments" inside a
router that is already prefixed with /api/v1/comments, producing the wrong
final path /api/v1/comments/events/{id}/comments. The {id}/comments segment
now lives in main.py's prefix for this router.
"""
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import get_current_user, get_current_user_optional
from src.core.database import get_db
from src.data.entities.models import Comment, User, UserRole
from src.data.repositories.comment_repository import CommentRepository
from src.data.repositories.event_repository import EventRepository

router = APIRouter()

MAX_COMMENT_LENGTH = 5000
MAX_REPLY_LENGTH = 2000

# Badges are derived from the account role only. Roles are never accepted from
# a client payload (UserRegister has no role field and UserRepository.create
# hardcodes AUTH_USER), so JOURNALIST can only be granted by an administrator
# directly in the database. That makes the badge a real, non-self-asserted
# signal — it must never be described as an editorial vetting process.
ROLE_BADGES: Dict[str, Dict[str, str]] = {
    UserRole.JOURNALIST.value: {
        "label": "Journalist",
        "tone": "cyan",
        "detail": "Account role granted by an administrator",
    },
    UserRole.ADMIN.value: {
        "label": "Moderator",
        "tone": "amber",
        "detail": "GlobeLens staff",
    },
}


class CreateCommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_COMMENT_LENGTH)
    parent_id: Optional[str] = Field(
        None, description="Comment being replied to. Threading is one level deep."
    )


class UpdateCommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_COMMENT_LENGTH)


class VoteRequest(BaseModel):
    value: int = Field(..., description="1 for upvote, -1 for downvote")


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} format",
        )


def _is_moderator(user: Optional[User]) -> bool:
    return bool(user and user.role == UserRole.ADMIN)


def _serialize_comment(
    comment: Comment,
    *,
    viewer: Optional[User],
    viewer_votes: Dict[uuid.UUID, int],
    depth: int = 0,
    expert_badges: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    votes = comment.votes or []
    upvotes = sum(1 for v in votes if v.value == 1)
    downvotes = sum(1 for v in votes if v.value == -1)

    user = comment.user
    badge = ROLE_BADGES.get(user.role.value) if user and user.role else None
    expert_badge = None
    if user and expert_badges:
        badge_title = expert_badges.get(str(user.id))
        if badge_title:
            expert_badge = {
                "label": f"Expert·{badge_title}",
                "tone": "violet",
                "detail": "Verified expert, granted by an administrator",
            }
    viewer_id = viewer.id if viewer else None
    is_author = bool(viewer_id and user and user.id == viewer_id)
    # Moderator rights come from the viewer, never from the comment's author.
    may_moderate = _is_moderator(viewer)

    return {
        "id": str(comment.id),
        "content": comment.content,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
        "updated_at": comment.updated_at.isoformat() if comment.updated_at else None,
        "is_edited": bool(comment.updated_at and comment.created_at
                          and comment.updated_at > comment.created_at),
        "is_hidden": comment.is_hidden,
        "parent_id": str(comment.parent_id) if comment.parent_id else None,
        "author": {
            "id": str(user.id) if user else None,
            "name": user.name if user else "Unknown",
            "is_author_self": is_author,
        },
        "badge": badge,
        "expert_badge": expert_badge,
        "votes": {
            "upvotes": upvotes,
            "downvotes": downvotes,
            "score": upvotes - downvotes,
        },
        "viewer_vote": viewer_votes.get(comment.id, 0),
        "can_edit": is_author,
        "can_delete": is_author or may_moderate,
        # Threading is one level deep: create_comment rejects a parent that
        # already has a parent. Only the top-level comment's replies are
        # eager-loaded, so recursing further would lazy-load `replies` inside
        # the async serializer and raise MissingGreenlet. A reply therefore
        # always reports an empty child list.
        "replies": [
            _serialize_comment(
                reply, viewer=viewer, viewer_votes=viewer_votes,
                depth=depth + 1, expert_badges=expert_badges,
            )
            for reply in (comment.replies or [])
            if not reply.is_hidden
        ] if depth == 0 else [],
    }


async def _load_event_or_404(db: AsyncSession, event_id: str) -> Any:
    event_uuid = _parse_uuid(event_id, "event id")
    event = await EventRepository(db).find_by_id(event_uuid)
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Event not found"
        )
    return event


@router.get("/events/{event_id}", summary="List threaded comments for an event")
async def list_comments(
    event_id: str = Path(...),
    include_hidden: bool = Query(
        False, description="Moderators only: include hidden comments"
    ),
    viewer: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    event = await _load_event_or_404(db, event_id)
    if include_hidden and not _is_moderator(viewer):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only moderators can view hidden comments",
        )

    repo = CommentRepository(db)
    comments = await repo.list_for_event(
        event.id, include_hidden=include_hidden
    )

    viewer_id = viewer.id if viewer else None
    viewer_votes: Dict[uuid.UUID, int] = {}
    if viewer_id:
        for comment in comments:
            existing = await repo.find_vote(comment.id, viewer_id)
            if existing:
                viewer_votes[comment.id] = existing.value

    # Batch-load active expert badges for every author in the thread (top-level
    # and replies) so rendering is one query, not one per comment.
    from src.services.expert_verification_service import active_badges_for_user_ids

    author_ids = list({
        comment.user_id for comment in comments
    } | {
        reply.user_id for comment in comments for reply in (comment.replies or [])
    })
    expert_badges = await active_badges_for_user_ids(db, author_ids)

    return {
        "event_id": str(event.id),
        "total": await repo.count_for_event(event.id),
        "comments": [
            _serialize_comment(
                c, viewer=viewer, viewer_votes=viewer_votes,
                expert_badges=expert_badges,
            )
            for c in comments
        ],
    }


@router.post(
    "/events/{event_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Post a comment or reply on an event",
)
async def create_comment(
    payload: CreateCommentRequest,
    event_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is blocked"
        )

    event = await _load_event_or_404(db, event_id)
    repo = CommentRepository(db)

    parent = None
    if payload.parent_id:
        parent = await repo.find_by_id(_parse_uuid(payload.parent_id, "parent id"))
        if not parent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent comment not found",
            )
        # A reply must belong to the same event, otherwise a comment could be
        # grafted from another event's thread.
        if parent.event_id != event.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent comment belongs to a different event",
            )
        # Threading is one level deep by design. Without this guard a user
        # could nest arbitrarily, and since the listing only walks two levels
        # deep the deeper replies would silently disappear.
        if parent.parent_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Replies are limited to one level; reply to the top-level comment instead",
            )

    content = payload.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Comment cannot be empty"
        )
    limit = MAX_REPLY_LENGTH if parent else MAX_COMMENT_LENGTH
    if len(content) > limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Comment exceeds {limit} characters",
        )

    comment = await repo.create(
        event_id=event.id,
        user_id=current_user.id,
        content=content,
        parent_id=parent.id if parent else None,
    )
    await db.commit()
    await db.refresh(comment, attribute_names=["user", "votes", "replies", "created_at", "updated_at"])

    from src.services.expert_verification_service import is_expert

    expert = await is_expert(db, current_user.id)
    expert_badges = {str(current_user.id): expert["badge_title"]} if expert and expert["badge_title"] else None

    return _serialize_comment(
        comment, viewer=current_user, viewer_votes={comment.id: 0},
        expert_badges=expert_badges,
    )


@router.put("/{comment_id}", summary="Edit a comment (author only)")
async def update_comment(
    payload: UpdateCommentRequest,
    comment_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CommentRepository(db)
    comment = await repo.find_by_id(_parse_uuid(comment_id, "comment id"))
    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found"
        )
    if comment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own comments",
        )

    content = payload.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Comment cannot be empty"
        )

    limit = MAX_REPLY_LENGTH if comment.parent_id else MAX_COMMENT_LENGTH
    if len(content) > limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Comment exceeds {limit} characters",
        )

    await repo.update_content(comment, content)
    await db.commit()
    return {"id": str(comment.id), "content": comment.content, "is_edited": True}


@router.delete(
    "/{comment_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a comment"
)
async def delete_comment(
    comment_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CommentRepository(db)
    comment = await repo.find_by_id(_parse_uuid(comment_id, "comment id"))
    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found"
        )
    if comment.user_id != current_user.id and not _is_moderator(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own comments",
        )

    await repo.delete(comment)
    await db.commit()
    return None


@router.post("/{comment_id}/vote", summary="Upvote or downvote a comment")
async def vote_comment(
    payload: VoteRequest,
    comment_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.value not in (1, -1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Vote value must be 1 or -1"
        )

    repo = CommentRepository(db)
    comment = await repo.find_by_id(_parse_uuid(comment_id, "comment id"))
    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found"
        )
    if comment.is_hidden:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found"
        )
    # Self-voting makes the quality signal meaningless and is trivially
    # abusable, so it is rejected rather than merely discouraged.
    if comment.user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot vote on your own comment",
        )

    await repo.set_vote(comment.id, current_user.id, payload.value)
    await db.commit()
    return {"id": str(comment.id), **await repo.vote_tally(comment.id)}


@router.delete("/{comment_id}/vote", summary="Retract your vote on a comment")
async def retract_vote(
    comment_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CommentRepository(db)
    comment = await repo.find_by_id(_parse_uuid(comment_id, "comment id"))
    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found"
        )
    await repo.clear_vote(comment.id, current_user.id)
    await db.commit()
    return {"id": str(comment.id), **await repo.vote_tally(comment.id)}
