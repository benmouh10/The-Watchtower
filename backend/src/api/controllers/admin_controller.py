"""
GlobeLens AI — AdminController (ADMIN role required for all endpoints)
GET  /admin/dashboard | /admin/users | /admin/stats
GET  /admin/moderation/comments | /admin/moderation/articles
PUT  /admin/users/{id}/role | /admin/users/{id}/plan | /admin/users/{id}/block | /admin/articles/{id}/hide
PATCH /admin/comments/{id}/visibility
DELETE /admin/users/{id} | /admin/comments/{id}
POST /admin/events/promote | /admin/sources
"""
from fastapi import APIRouter, Path, Body, Query, status, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Any, List, Optional
from sqlalchemy import select

from src.api.controllers.auth_controller import get_current_user, require_admin
from src.core.config import settings
from src.data.entities.models import User, UserRole
from src.data.repositories.article_repository import ArticleRepository
from src.services.embedding_service import EmbeddingService
from src.services.clustering_service import ClusteringService
from src.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

# Every endpoint on this router requires an ADMIN account. Enforced at the
# router so a newly added endpoint cannot forget the check.
router = APIRouter(dependencies=[Depends(require_admin)])


class UpdateRoleRequest(BaseModel):
    role: str  # GUEST | AUTH_USER | JOURNALIST | ADMIN


class UpdatePlanRequest(BaseModel):
    plan: str  # FREE | PRO | ENTERPRISE


class CreateSourceRequest(BaseModel):
    name: str
    url: str
    country: Optional[str] = None
    # Omit to leave the source unrated. Defaulting this to 0.5 asserted a
    # neutral 50% trust score for a publisher nobody had assessed.
    credibility_score: Optional[float] = None


class PromoteEventRequest(BaseModel):
    event_id: str


class VisibilityRequest(BaseModel):
    is_hidden: bool


def _parse_uuid(raw: str, what: str) -> "uuid.UUID":
    """Reject malformed ids with a 422 instead of letting a ValueError escape as a 500."""
    import uuid

    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{raw}' is not a valid {what} id",
        )


@router.get("/dashboard", summary="Admin dashboard overview (ADMIN)")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func, select
    from src.data.entities.models import Article, Comment, Event, User

    async def count(model) -> int:
        return await db.scalar(select(func.count(model.id))) or 0

    return {
        "total_users": await count(User),
        "total_events": await count(Event),
        "total_articles": await count(Article),
        "total_comments": await count(Comment),
        "hidden_articles": await db.scalar(
            select(func.count(Article.id)).where(Article.is_hidden.is_(True))
        ) or 0,
        "hidden_comments": await db.scalar(
            select(func.count(Comment.id)).where(Comment.is_hidden.is_(True))
        ) or 0,
    }


@router.get("/users", summary="List all users (ADMIN)")
async def list_users(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from src.data.entities.models import User

    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    return {
        "users": [
            {
                "id": str(u.id),
                "name": u.name,
                "email": u.email,
                "role": u.role.value,
                "plan": u.plan.value,
                "is_blocked": u.is_blocked,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    }


@router.put("/users/{user_id}/role", summary="Change user role (ADMIN)")
async def update_user_role(
    user_id: str = Path(...),
    payload: UpdateRoleRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from src.data.entities.models import UserRole as _Roles
    from src.data.repositories.user_repository import UserRepository

    try:
        new_role = _Roles(payload.role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown role '{payload.role}'. Valid roles: {[r.value for r in _Roles]}",
        )

    target_uuid = _parse_uuid(user_id, "user")
    if target_uuid == current_user.id and new_role != _Roles.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove your own administrator role",
        )

    user_repo = UserRepository(db)
    user = await user_repo.find_by_id(target_uuid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.role = new_role
    await user_repo.update(user)
    return {"message": "Role updated", "user_id": str(user.id), "role": new_role.value}


@router.put("/users/{user_id}/plan", summary="Assign a plan to a user (ADMIN)")
async def update_user_plan(
    user_id: str = Path(...),
    payload: UpdatePlanRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Set an account's plan tier and record the move in the plan_changes audit
    log. This is the only way a plan changes today: there is no payment
    provider, so PRO/ENTERPRISE are operational assignments made here, not
    purchases. Assigning the plan the account already has is a no-op that
    records nothing, so a client retry cannot fabricate audit noise.
    """
    from src.data.entities.models import PlanTier as _Plans
    from src.data.repositories.user_repository import UserRepository
    from src.services.plan_service import PlanService

    try:
        new_plan = _Plans(payload.plan)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown plan '{payload.plan}'. Valid plans: {[p.value for p in _Plans]}",
        )

    target_uuid = _parse_uuid(user_id, "user")

    user_repo = UserRepository(db)
    user = await user_repo.find_by_id(target_uuid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.plan == new_plan:
        return {
            "message": f"Account is already on {new_plan.value}",
            "user_id": str(user.id),
            "from_plan": user.plan.value,
            "plan": user.plan.value,
            "changed": False,
        }

    previous_plan = user.plan
    service = PlanService(db)
    await service.assign(user, new_plan, changed_by=current_user.id)
    await db.commit()
    await service.invalidate_rate_limit_cache(user.id)

    return {
        "message": "Plan updated",
        "user_id": str(user.id),
        "from_plan": previous_plan.value,
        "plan": user.plan.value,
        "changed_by": str(current_user.id),
        "changed": True,
    }


@router.put("/users/{user_id}/block", summary="Block / unblock a user (ADMIN)")
async def toggle_block_user(
    user_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from src.data.repositories.user_repository import UserRepository

    target_uuid = _parse_uuid(user_id, "user")
    if target_uuid == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot block your own account",
        )

    user_repo = UserRepository(db)
    user = await user_repo.find_by_id(target_uuid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_blocked = not user.is_blocked
    await user_repo.update(user)
    return {"message": "User updated", "user_id": str(user.id), "is_blocked": user.is_blocked}


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete user (ADMIN)")
async def admin_delete_user(
    user_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from sqlalchemy import func, select
    from src.data.entities.models import Comment, FactCheckRequest
    from src.data.repositories.user_repository import UserRepository

    target_uuid = _parse_uuid(user_id, "user")
    if target_uuid == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )

    user_repo = UserRepository(db)
    user = await user_repo.find_by_id(target_uuid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # comments.user_id has no ON DELETE CASCADE, so deleting an author who has
    # posted would raise an IntegrityError. Report the real blocker instead.
    comment_count = await db.scalar(
        select(func.count(Comment.id)).where(Comment.user_id == target_uuid)
    ) or 0
    if comment_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This user has {comment_count} comment(s) and cannot be deleted. "
                "Block the account instead, or remove their comments first."
            ),
        )

    await user_repo.delete(user)
    return None


@router.get("/moderation/comments", summary="Moderation queue including hidden comments (ADMIN)")
async def list_comments_for_moderation(
    include_hidden: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from src.api.controllers.comment_controller import ROLE_BADGES
    from src.data.entities.models import Comment

    stmt = select(Comment).options(
        selectinload(Comment.user), selectinload(Comment.replies)
    ).order_by(Comment.created_at.desc())

    if not include_hidden:
        stmt = stmt.where(Comment.is_hidden.is_(False))

    comments = (await db.execute(stmt)).scalars().unique().all()
    return {
        "comments": [
            {
                "id": str(c.id),
                "event_id": str(c.event_id),
                "content": c.content,
                "is_hidden": c.is_hidden,
                "parent_id": str(c.parent_id) if c.parent_id else None,
                "reply_count": len(c.replies),
                "author": c.user.name if c.user else None,
                # Same vocabulary as the public comment serialiser, so the
                # moderation queue shows a "Journalist"/"Moderator" badge instead
                # of a raw enum value. author_role stays for compatibility.
                "author_role": c.user.role.value if c.user else None,
                "author_badge": (
                    ROLE_BADGES.get(c.user.role.value)
                    if c.user and c.user.role and c.user.role.value in ROLE_BADGES
                    else None
                ),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in comments
        ]
    }


@router.patch("/comments/{comment_id}/visibility", summary="Hide / unhide a comment (ADMIN)")
async def set_comment_visibility(
    comment_id: str = Path(...),
    payload: VisibilityRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from src.data.entities.models import Comment

    comment = await db.get(Comment, _parse_uuid(comment_id, "comment"))
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    comment.is_hidden = payload.is_hidden
    await db.commit()
    return {"id": str(comment.id), "is_hidden": comment.is_hidden}


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a comment (ADMIN)")
async def admin_delete_comment(
    comment_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from src.data.entities.models import Comment

    comment = await db.get(Comment, _parse_uuid(comment_id, "comment"))
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    await db.delete(comment)
    await db.commit()
    return None


class GrantExpertRequest(BaseModel):
    user_id: str
    badge_title: str = "Verified expert"


@router.get("/expert-verifications", summary="List active expert badges (ADMIN)")
async def list_expert_verifications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from src.services.expert_verification_service import active_rows

    verifications = await active_rows(db)
    return {"total": len(verifications), "verifications": verifications}


@router.post("/expert-verifications", status_code=status.HTTP_201_CREATED, summary="Grant an expert badge (ADMIN)")
async def grant_expert_verification(
    payload: GrantExpertRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from src.services.expert_verification_service import grant

    badge_title = payload.badge_title.strip()
    if not badge_title:
        raise HTTPException(status_code=422, detail="badge_title cannot be empty")

    try:
        verification = await grant(
            db,
            user_id=_parse_uuid(payload.user_id, "user"),
            badge_title=badge_title,
            granted_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await db.commit()
    return {
        "user_id": str(verification.user_id),
        "badge_title": verification.badge_title,
        "verified_at": verification.verified_at.isoformat(),
    }


@router.delete("/expert-verifications/{user_id}", status_code=status.HTTP_200_OK, summary="Revoke an expert badge (ADMIN)")
async def revoke_expert_verification(
    user_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from src.services.expert_verification_service import revoke

    revoked = await revoke(db, user_id=_parse_uuid(user_id, "user"), revoked_by=current_user.id)
    await db.commit()
    return {"user_id": user_id, "revoked": revoked}


@router.post("/events/promote", status_code=status.HTTP_200_OK, summary="Promote an event (ADMIN)")
async def promote_event(
    payload: PromoteEventRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Not implemented: the events table has no featured/promoted column.
    Reported honestly instead of returning a success message that changed
    nothing. Requires a schema decision before it can exist.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Event promotion is not implemented: the events table has no featured flag.",
    )


@router.get("/moderation/articles", summary="Moderation queue including hidden articles (ADMIN)")
async def list_articles_for_moderation(
    include_hidden: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    repo = ArticleRepository(db)
    articles = await repo.list_for_moderation(include_hidden=include_hidden, limit=limit)
    return {
        "articles": [
            {
                "id": str(a.id),
                "title": a.title,
                "url": a.url,
                "is_hidden": a.is_hidden,
                "event_id": str(a.event_id) if a.event_id else None,
                "event_title": a.event.title if a.event else None,
                "source_id": str(a.source_id) if a.source_id else None,
                "source_name": a.source.name if a.source else None,
                "processing_status": a.processing_status.value if a.processing_status else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in articles
        ]
    }


@router.put("/articles/{article_id}/hide", summary="Hide / unhide an article (ADMIN)")
async def hide_article(
    article_id: str = Path(...),
    payload: VisibilityRequest = Body(default_factory=lambda: VisibilityRequest(is_hidden=True)),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from src.data.entities.models import Article

    article = await db.get(Article, _parse_uuid(article_id, "article"))
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    article.is_hidden = payload.is_hidden
    await db.commit()
    return {"id": str(article.id), "is_hidden": article.is_hidden}


@router.post("/sources", status_code=status.HTTP_201_CREATED, summary="Add a new media source (ADMIN)")
async def create_source(
    payload: CreateSourceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from src.data.entities.models import Source

    existing = (await db.execute(select(Source.name).where(Source.name == payload.name))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A source named '{payload.name}' already exists",
        )

    # bias_lean stays NULL unless an admin explicitly assesses it. Defaulting a
    # new publisher to CENTER would fabricate a rating that nobody made.
    source = Source(
        name=payload.name,
        url=payload.url,
        country=payload.country,
        credibility_score=payload.credibility_score,
        bias_lean=None,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    return {
        "id": str(source.id),
        "message": "Source added",
        "name": source.name,
        "bias_lean": source.bias_lean.value if source.bias_lean else None,
    }


@router.get("/stats", summary="System-wide analytics (ADMIN)")
async def get_stats(
    from_: Optional[str] = Query(
        None, alias="from",
        description="Inclusive window start (ISO 8601). Defaults to the beginning of time.",
    ),
    to: Optional[str] = Query(
        None,
        description="Inclusive window end (ISO 8601). Defaults to now.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns system-wide stats, including live ingestion funnel counts,
    media source split, topic distribution, and bias spectrum.

    ``from`` / ``to`` bound every aggregate to a time window. Both are optional;
    a start after an end is swapped (never a silent empty dashboard).
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can access system stats"
        )

    def _parse(value: Optional[str], name: str) -> Optional[datetime]:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {name} timestamp; use ISO 8601 (e.g. 2026-09-01)",
            )

    window_start = _parse(from_, "from")
    window_end = _parse(to, "to")
    if window_start and window_end and window_start > window_end:
        window_start, window_end = window_end, window_start

    def _windowed(column: Any, start: Optional[datetime], end: Optional[datetime]) -> List[Any]:
        clauses = []
        if start is not None:
            clauses.append(column >= start)
        if end is not None:
            clauses.append(column <= end)
        return clauses

    from sqlalchemy import select, func
    from src.data.entities.models import Article, Event, Source, ProcessingStatus

    # 1. Ingestion Funnel Counts
    scraped_count = await db.scalar(
        select(func.count(Article.id)).where(
            Article.processing_status == ProcessingStatus.SCRAPED,
            *_windowed(Article.created_at, window_start, window_end),
        )
    ) or 0
    vectorized_count = await db.scalar(
        select(func.count(Article.id)).where(
            Article.processing_status == ProcessingStatus.EMBEDDED,
            *_windowed(Article.created_at, window_start, window_end),
        )
    ) or 0
    clustered_count = await db.scalar(
        select(func.count(Article.id)).where(
            Article.processing_status == ProcessingStatus.CLUSTERED,
            *_windowed(Article.created_at, window_start, window_end),
        )
    ) or 0
    enriched_count = await db.scalar(
        select(func.count(Event.id)).where(
            Event.status == "PROCESSED",
            *_windowed(Event.created_at, window_start, window_end),
        )
    ) or 0
    
    # 2. Media Source Split
    media_query = (
        select(Source.name, func.count(Article.id))
        .join(Article)
        .where(*_windowed(Article.created_at, window_start, window_end))
        .group_by(Source.name)
    )
    media_result = await db.execute(media_query)
    media_split = {row[0]: row[1] for row in media_result.all()}
    
    # 3. Topic Distribution
    topic_query = (
        select(Event.topic, func.count(Event.id))
        .where(
            Event.topic.isnot(None),
            *_windowed(Event.created_at, window_start, window_end),
        )
        .group_by(Event.topic)
    )
    topic_result = await db.execute(topic_query)
    topic_dist = {row[0]: row[1] for row in topic_result.all()}
    
    # 4. Bias Leaning Distribution
    bias_query = (
        select(Event.bias_lean, func.count(Event.id))
        .where(
            Event.bias_lean.isnot(None),
            *_windowed(Event.created_at, window_start, window_end),
        )
        .group_by(Event.bias_lean)
    )
    bias_result = await db.execute(bias_query)
    bias_dist = {row[0].name: row[1] for row in bias_result.all()}
    
    # Empty buckets are reported as an explicit empty list rather than filled
    # with named publishers holding a count of zero. Naming "BBC News", "CNN" and
    # "Al Jazeera" when the table is empty asserts those are the outlets, which
    # is a fabrication and also hides the real answer: nothing has been ingested.
    # The topic and bias zero-fills below are different — a bucket with no rows
    # genuinely has a count of zero, which is a true statement.
    media_split_empty = not media_split
    if media_split_empty:
        media_split = {}

    for topic in ["POLITICS", "ECONOMY", "TECHNOLOGY", "SPORTS", "HEALTH", "WORLD"]:
        if topic not in topic_dist:
            topic_dist[topic] = 0
            
    for bias in ["LEFT", "CENTER_LEFT", "CENTER", "CENTER_RIGHT", "RIGHT"]:
        if bias not in bias_dist:
            bias_dist[bias] = 0

    return {
        "window": {
            "from": window_start.isoformat() if window_start else None,
            "to": window_end.isoformat() if window_end else None,
        },
        "funnel": {
            "scraped": scraped_count,
            "vectorized": vectorized_count,
            "clustered": clustered_count,
            "enriched": enriched_count
        },
        "media_split": media_split,
        # Lets the client distinguish "no articles yet" from "these outlets have
        # articles", instead of reading a dict of zeroed names.
        "media_split_empty": media_split_empty,
        "topic_distribution": topic_dist,
        "bias_distribution": bias_dist
    }


@router.post("/embed/process", status_code=status.HTTP_202_ACCEPTED, summary="Trigger batch article embedding process (ADMIN)")
async def process_embeddings(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """
    Trigger batch processing of article embeddings.
    Only users with the ADMIN role can execute this action.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can trigger the embedding pipeline"
        )
        
    embedding_service = EmbeddingService()
    background_tasks.add_task(embedding_service.process_scraped_batch)
    return {"status": "embedding_batch_initiated"}


@router.post("/cluster/process", status_code=status.HTTP_202_ACCEPTED, summary="Trigger batch article clustering process (ADMIN)")
async def process_clustering(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """
    Trigger batch processing of article clustering.
    Only users with the ADMIN role can execute this action.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can trigger the clustering pipeline"
        )
        
    clustering_service = ClusteringService()
    background_tasks.add_task(clustering_service.cluster_unassigned_articles)
    return {"status": "clustering_process_initiated"}


@router.post("/llm/process", status_code=status.HTTP_202_ACCEPTED, summary="Trigger batch LLM event intelligence processing (ADMIN)")
async def process_llm(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """
    Trigger batch processing of LLM event intelligence.
    Only users with the ADMIN role can execute this action.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can trigger the LLM processing pipeline"
        )
        
    from src.services.llm_service import LLMService
    llm_service = LLMService()
    background_tasks.add_task(llm_service.process_pending_events)
    return {"status": "llm_processing_initiated"}


@router.post("/search/sync", status_code=status.HTTP_202_ACCEPTED, summary="Trigger bulk search synchronization (ADMIN)")
async def sync_search_index(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """
    Trigger bulk synchronization of all PROCESSED events into Elasticsearch.
    Only users with the ADMIN role can execute this action.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can trigger search synchronization"
        )
        
    from src.services.search_service import SearchService
    search_service = SearchService()
    background_tasks.add_task(search_service.sync_all_processed_events)
    return {"status": "search_sync_initiated"}


@router.post("/digest/generate", status_code=status.HTTP_201_CREATED, summary="Generate a comprehensive brief now (ADMIN)")
async def generate_digest_now(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Run the digest engine immediately and persist the resulting comprehensive
    brief, bypassing the cron schedule. This is how an operator backfills a
    brief after enabling the feature (or after a long outage) instead of
    waiting for the next scheduled slot. Runs synchronously: selection is a
    plain DB read plus an offline HTML render, so it is far too fast to need a
    background task.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can trigger a digest run",
        )

    from src.services.digest_service import DigestService
    from src.services.newsletter_service import NewsletterService
    from src.services.push_service import PushService

    try:
        digest = await DigestService(db).generate_digest(title="Manual comprehensive brief")
        try:
            enqueued = await NewsletterService(db).enqueue_digest(digest)
        except Exception:
            enqueued = 0
        try:
            pushes_enqueued = await PushService(db).enqueue_digest(digest)
        except Exception:
            pushes_enqueued = 0
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Digest generation failed: {exc}",
        )

    return {
        "digest_id": str(digest.id),
        "title": digest.title,
        "event_count": digest.event_count,
        "range_start": digest.range_start.isoformat() if digest.range_start else None,
        "range_end": digest.range_end.isoformat() if digest.range_end else None,
        "created_at": digest.created_at.isoformat() if digest.created_at else None,
        "newsletter_enqueued": enqueued,
        "pushes_enqueued": pushes_enqueued,
    }


@router.post("/newsletter/deliver", summary="Flush pending newsletter deliveries now (ADMIN)")
async def flush_newsletter_deliveries(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Drain the pending newsletter queue immediately instead of waiting for the
    background worker's next poll. In log mode (EMAIL_ENABLED off) this records
    each send as 'logged'; with an SMTP relay configured it actually emails.
    Does not squash double-sends: the (digest_id, user_id) uniqueness means a
    row stays pending only until the first successful drain.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can flush newsletter deliveries",
        )

    from src.services.newsletter_service import NewsletterService

    return {"mode": "smtp" if settings.EMAIL_ENABLED else "log", **await NewsletterService(db).flush_pending_deliveries()}


@router.get("/newsletter/stats", summary="Newsletter delivery analytics (ADMIN)")
async def newsletter_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delivery health for the digest newsletter: subscriber count, queue
    breakdown by status, and the most recent brief — enough to spot a digest
    that silently stopped emailing without touching the database."""
    from sqlalchemy import func, select

    from src.data.entities.models import Digest, NewsletterDelivery, NewsletterSubscription
    from src.services.newsletter_service import NewsletterService

    service = NewsletterService(db)
    counts = await service.delivery_counts()
    subscriber_count = await db.scalar(
        select(func.count(NewsletterSubscription.id)).where(
            NewsletterSubscription.unsubscribed_at.is_(None)
        )
    ) or 0
    digest = (
        await db.execute(select(Digest).order_by(Digest.created_at.desc()).limit(1))
    ).scalars().first()
    return {
        "worker": "smtp" if settings.EMAIL_ENABLED else "log",
        "subscriber_count": subscriber_count,
        "delivery_counts": counts,
        "last_digest": {
            "id": str(digest.id),
            "title": digest.title,
            "event_count": digest.event_count,
            "generated_at": digest.created_at.isoformat() if digest.created_at else None,
        } if digest else None,
    }


@router.post("/push/deliver", summary="Flush pending Web Push deliveries now (ADMIN)")
async def flush_push_deliveries(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Drain the pending push queue immediately instead of waiting for the
    background worker's next poll. In log mode (PUSH_ENABLED off) this records
    each send as 'logged'; with VAPID configured it actually relays to the
    browsers' push services. The (digest_id, subscription_id) uniqueness means
    a row stays pending only until the first successful drain.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can flush push deliveries",
        )

    from src.services.push_service import PushService

    return {"mode": "vapid" if settings.PUSH_ENABLED else "log", **await PushService(db).flush_pending_deliveries()}


@router.post("/webhooks/deliver", summary="Flush pending outbound webhook deliveries now (ADMIN)")
async def flush_webhook_deliveries(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Drain the pending webhook queue immediately instead of waiting for the
    background worker's next poll. In log mode (WEBHOOK_ENABLED off) this
    records each delivery as 'logged'; with real delivery enabled it POSTs the
    payloads to the registered endpoints.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can flush webhook deliveries",
        )

    from src.services.webhook_service import WebhookService

    return {"mode": "live" if settings.WEBHOOK_ENABLED else "log", **await WebhookService(db).flush_pending_deliveries()}


@router.post("/media/process", summary="Generate AI captions for media articles in batch (ADMIN)")
async def batch_media_captions(
    limit: int = Query(10, ge=1, le=50, description="Max articles to process in this run"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Caption the oldest articles that have a stored image but no AI caption yet
    (image_ai_caption IS NULL), oldest first, up to `limit` per run. Each
    article is handled independently so one provider failure never aborts the
    batch. Refuses with 503 when no VISION_PROVIDER is configured, so a run on
    an un-wired deployment says so instead of pretending captions appeared.
    """
    from src.data.entities.models import Article
    from src.services.media_providers import MediaProviderUnavailable, build_vision_provider

    provider = build_vision_provider()
    if not provider.is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "No vision provider is configured (VISION_PROVIDER empty or its "
                "credential unset), so AI captioning is unavailable."
            ),
        )

    rows = (
        await db.execute(
            select(Article)
            .where(
                Article.image_url.is_not(None),
                Article.image_ai_caption.is_(None),
            )
            .order_by(Article.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()

    if not rows:
        return {"processed": 0, "captioned": 0, "skipped": 0, "failed": 0, "provider": provider.name, "detail": "Nothing to caption."}

    captioned: List[str] = []
    failed: List[dict] = []
    for article in rows:
        if not article.image_url:
            continue
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                media_resp = await client.get(article.image_url)
                media_resp.raise_for_status()
                image_bytes = media_resp.content
        except Exception as exc:
            failed.append({"id": str(article.id), "title": article.title, "error": f"fetch: {exc}"})
            continue
        if not image_bytes:
            failed.append({"id": str(article.id), "title": article.title, "error": "empty image response"})
            continue

        try:
            result = await provider.caption_image(
                image_bytes,
                media_resp.headers.get("content-type", "image/jpeg"),
                hint=article.image_caption or article.title,
            )
        except Exception as exc:
            failed.append({"id": str(article.id), "title": article.title, "error": str(exc)})
            continue

        article.image_ai_caption = result.caption
        article.image_ai_caption_model = result.model
        article.image_ai_caption_at = datetime.now(timezone.utc)
        captioned.append(str(article.id))

    await db.commit()
    return {
        "processed": len(rows),
        "captioned": len(captioned),
        "skipped": len(rows) - len(captioned) - len(failed),
        "failed": failed,
        "provider": provider.name,
    }

