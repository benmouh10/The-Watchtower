"""
GlobeLens AI — AdminController (ADMIN role required for all endpoints)
GET  /admin/dashboard | /admin/users | /admin/stats
PUT  /admin/users/{id}/role | /admin/users/{id}/block | /admin/articles/{id}/hide
DELETE /admin/users/{id} | /admin/comments/{id}
POST /admin/events/promote | /admin/sources
"""
from fastapi import APIRouter, Path, status, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional

from app.controllers.auth_controller import get_current_user
from app.core.config import settings
from app.core.passwords import validate_password_strength
from app.core.rbac import require_role
from app.entities.models import User, UserRole
from app.services.embedding_service import EmbeddingService
from app.services.clustering_service import ClusteringService
from app.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

# Admin-only tier. The hierarchy lives in app.core.rbac so every controller
# speaks the same role vocabulary; the name is kept for existing importers
# (e.g. newsletter_controller). require_role(ADMIN) answers anonymous callers
# with 401 and lower-ranked accounts with 403.
require_admin = require_role(UserRole.ADMIN)


# The guard lives on the router, so a moderation route added later is protected
# by default rather than by remembering to attach a dependency. The handful of
# routes below that also need the user object take it as a parameter as well.
router = APIRouter(dependencies=[Depends(require_admin)])


class UpdateRoleRequest(BaseModel):
    role: str  # GUEST | AUTH_USER | JOURNALIST | ADMIN


class CreateUserRequest(BaseModel):
    """Provision an account with an operator-chosen role."""

    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: str  # GUEST | AUTH_USER | JOURNALIST | ADMIN

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return validate_password_strength(value)


class CreateSourceRequest(BaseModel):
    name: str
    url: str
    country: Optional[str] = None
    credibility_score: Optional[float] = 0.5


class PromoteEventRequest(BaseModel):
    event_id: str


def _parse_uuid(raw: str, label: str):
    """Path segments are strings; a malformed id must not become a 500."""
    import uuid as _uuid

    try:
        return _uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {label} UUID")


@router.get("/dashboard", summary="Admin dashboard overview (ADMIN)")
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Live counts from the database.

    Previously hardcoded zeros, so the admin dashboard reported an empty
    system while 101 events were loaded.
    """
    from sqlalchemy import func, select
    from app.entities.models import Article, Comment, Event, Source

    async def count(model) -> int:
        result = await db.execute(select(func.count()).select_from(model))
        return int(result.scalar_one())

    return {
        "total_users": await count(User),
        "total_events": await count(Event),
        "total_articles": await count(Article),
        "total_comments": await count(Comment),
        "total_sources": await count(Source),
        "hidden_articles": int(
            (await db.execute(
                select(func.count()).select_from(Article).where(Article.is_hidden.is_(True))
            )).scalar_one()
        ),
        "blocked_users": int(
            (await db.execute(
                select(func.count()).select_from(User).where(User.is_blocked.is_(True))
            )).scalar_one()
        ),
    }


@router.get("/users", summary="List all users (ADMIN)")
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Real user list. Previously returned an empty list for everyone."""
    from sqlalchemy import select
    from app.entities.models import User as UserModel

    result = await db.execute(select(UserModel).order_by(UserModel.created_at.desc()))
    users = result.scalars().all()
    return {
        "users": [
            {
                "id": str(u.id),
                "name": u.name,
                "email": u.email,
                "role": u.role.value if hasattr(u.role, "value") else u.role,
                "is_blocked": u.is_blocked,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    }


@router.post("/users", status_code=status.HTTP_201_CREATED, summary="Create an account with any role (ADMIN)")
async def create_user(
    payload: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Provision an account directly, bypassing public self-registration so an
    operator can assign the role (JOURNALIST, ADMIN, ...) at creation time.
    """
    from sqlalchemy import select
    from app.entities.models import User as UserModel
    from app.services.auth_service import hash_password

    try:
        new_role = UserRole(payload.role.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role '{payload.role}'. Valid roles: "
                   f"{', '.join(r.value for r in UserRole)}",
        )

    email = str(payload.email).strip().lower()
    existing = await db.execute(select(UserModel).where(UserModel.email == email))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Email is already registered")

    user = UserModel(
        name=payload.name.strip(),
        email=email,
        password_hash=hash_password(payload.password),
        role=new_role,
        is_blocked=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return {
        "message": "Account created",
        "user": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "role": user.role.value if hasattr(user.role, "value") else user.role,
            "is_blocked": user.is_blocked,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
    }


@router.put("/users/{user_id}/role", summary="Change user role (ADMIN)")
async def update_user_role(
    user_id: str = Path(...),
    payload: UpdateRoleRequest = ...,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Persist the new role.

    Previously echoed "Role updated to X" for any role string without writing
    anything, so an operator could believe an escalation had been applied.
"""
    from app.entities.models import User as UserModel

    target_uuid = _parse_uuid(user_id, "user")
    try:
        new_role = UserRole(payload.role.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role '{payload.role}'. Valid roles: "
                   f"{', '.join(r.value for r in UserRole)}",
        )

    user = await db.get(UserModel, target_uuid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    previous = user.role.value if hasattr(user.role, "value") else user.role
    user.role = new_role
    await db.commit()
    await db.refresh(user)

    return {
        "message": "Role updated",
        "user_id": str(user.id),
        "previous_role": previous,
        "role": new_role.value,
    }


@router.put("/users/{user_id}/block", summary="Block / unblock a user (ADMIN)")
async def toggle_block_user(
    user_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Flip the block flag and report the resulting state.

    Previously claimed a toggle happened while leaving is_blocked untouched.
    Blocking an admin's own account is refused: an operator can lock themselves
    and every other admin out with no way back in through the API.
    """
    from app.entities.models import User as UserModel

    target_uuid = _parse_uuid(user_id, "user")
    if target_uuid == admin.id:
        raise HTTPException(
            status_code=400,
            detail="An admin cannot block their own account",
        )

    user = await db.get(UserModel, target_uuid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_blocked = not user.is_blocked
    await db.commit()
    await db.refresh(user)

    return {
        "message": "User block status updated",
        "user_id": str(user.id),
        "is_blocked": user.is_blocked,
    }


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete user (ADMIN)")
async def admin_delete_user(
    user_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Actually delete. Previously returned 204 without removing anything."""
    from app.entities.models import User as UserModel

    target_uuid = _parse_uuid(user_id, "user")
    if target_uuid == admin.id:
        raise HTTPException(
            status_code=400,
            detail="An admin cannot delete their own account",
        )

    user = await db.get(UserModel, target_uuid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(user)
    await db.commit()
    return None


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Moderate / delete comment (ADMIN)")
async def admin_delete_comment(
    comment_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Actually remove the comment, or say clearly that it does not exist."""
    from app.entities.models import Comment

    comment_uuid = _parse_uuid(comment_id, "comment")
    comment = await db.get(Comment, comment_uuid)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    await db.delete(comment)
    await db.commit()
    return None


@router.post("/events/promote", status_code=status.HTTP_200_OK, summary="Promote an event to featured (ADMIN)")
async def promote_event(
    payload: PromoteEventRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Persist is_promoted. Previously claimed success without any write."""
    from app.entities.models import Event

    event_uuid = _parse_uuid(payload.event_id, "event")
    event = await db.get(Event, event_uuid)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    event.is_promoted = True
    await db.commit()
    await db.refresh(event)

    return {
        "message": "Event promoted",
        "event_id": str(event.id),
        "is_promoted": event.is_promoted,
    }


@router.put("/articles/{article_id}/hide", summary="Hide / mask article from public (ADMIN)")
async def hide_article(
    article_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Persist is_hidden.

    Previously answered "Article hidden" while leaving the article public, which
    is the most damaging direction for this failure: an editor moderating a
    retracted or defamatory story would be told it was hidden and it would
    still be served.
    """
    from app.entities.models import Article

    article_uuid = _parse_uuid(article_id, "article")
    article = await db.get(Article, article_uuid)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    article.is_hidden = not article.is_hidden
    await db.commit()
    await db.refresh(article)

    return {
        "message": "Article visibility updated",
        "article_id": str(article.id),
        "is_hidden": article.is_hidden,
    }


@router.post("/sources", status_code=status.HTTP_201_CREATED, summary="Add a new media source (ADMIN)")
async def create_source(
    payload: CreateSourceRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Insert the source row.

    Previously returned 201 "Source added" and stored nothing, so the source
    list silently stopped matching what the API claimed to have registered.
    """
    from sqlalchemy import select
    from app.entities.models import Source

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Source name cannot be empty")
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Source url must be http(s)")
    if payload.credibility_score is not None and not 0.0 <= payload.credibility_score <= 1.0:
        raise HTTPException(
            status_code=400, detail="credibility_score must be between 0 and 1"
        )

    existing = await db.execute(select(Source).where(Source.name == name))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Source '{name}' already exists")

    source = Source(
        name=name,
        url=payload.url,
        country=payload.country,
        credibility_score=payload.credibility_score,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    return {
        "message": "Source added",
        "id": str(source.id),
        "name": source.name,
        "url": source.url,
    }


@router.get("/stats", summary="System-wide analytics (ADMIN)")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns system-wide stats, including live ingestion funnel counts,
    media source split, topic distribution, and bias spectrum.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can access system stats"
        )
        
    from sqlalchemy import select, func
    from app.entities.models import Article, Event, Source, ProcessingStatus
    
    # 1. Ingestion Funnel Counts
    # The funnel is cumulative: each stage counts every article that reached it
    # OR went further. Counting only the exact current status makes the funnel
    # drain to zero as soon as the pipeline finishes, since a completed article
    # sits in PROCESSED and leaves all three earlier buckets. Stage order is
    # taken from the ProcessingStatus enum so added stages stay correct.
    pipeline_order = list(ProcessingStatus)

    async def count_reaching(stage: ProcessingStatus) -> int:
        reached = pipeline_order[pipeline_order.index(stage):]
        return await db.scalar(
            select(func.count(Article.id)).where(Article.processing_status.in_(reached))
        ) or 0

    scraped_count = await count_reaching(ProcessingStatus.SCRAPED)
    vectorized_count = await count_reaching(ProcessingStatus.EMBEDDED)
    clustered_count = await count_reaching(ProcessingStatus.CLUSTERED)
    enriched_count = await db.scalar(
        select(func.count(Event.id)).where(Event.status == "PROCESSED")
    ) or 0
    
    # 2. Media Source Split
    media_query = (
        select(Source.name, func.count(Article.id))
        .join(Article)
        .group_by(Source.name)
    )
    media_result = await db.execute(media_query)
    media_split = {row[0]: row[1] for row in media_result.all()}
    
    # 3. Topic Distribution
    topic_query = (
        select(Event.topic, func.count(Event.id))
        .where(Event.topic.isnot(None))
        .group_by(Event.topic)
    )
    topic_result = await db.execute(topic_query)
    topic_dist = {row[0]: row[1] for row in topic_result.all()}
    
    # 4. Bias Leaning Distribution
    bias_query = (
        select(Event.bias_lean, func.count(Event.id))
        .where(Event.bias_lean.isnot(None))
        .group_by(Event.bias_lean)
    )
    bias_result = await db.execute(bias_query)
    bias_dist = {row[0].name: row[1] for row in bias_result.all()}
    
    # Default values to satisfy empty data visualizations
    if not media_split:
        media_split = {"BBC News": 0, "CNN": 0, "Al Jazeera": 0}
        
    for topic in ["POLITICS", "ECONOMY", "TECHNOLOGY", "SPORTS", "HEALTH", "WORLD"]:
        if topic not in topic_dist:
            topic_dist[topic] = 0
            
    for bias in ["LEFT", "CENTER_LEFT", "CENTER", "CENTER_RIGHT", "RIGHT"]:
        if bias not in bias_dist:
            bias_dist[bias] = 0

    return {
        "funnel": {
            "scraped": scraped_count,
            "vectorized": vectorized_count,
            "clustered": clustered_count,
            "enriched": enriched_count
        },
        "media_split": media_split,
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
        
    from app.services.llm_service import LLMService
    llm_service = LLMService()
    background_tasks.add_task(llm_service.process_pending_events)
    return {"status": "llm_processing_initiated"}


@router.post("/illustrate/process", status_code=status.HTTP_202_ACCEPTED, summary="Trigger batch illustration generation (ADMIN)")
async def process_illustrations(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    limit: int | None = None
):
    """
    Generate artwork for events that do not have it yet.

    Only users with the ADMIN role can execute this action. Runs in the
    background because one image takes seconds and a batch takes minutes; the
    service also enforces its own batch limit, so a generous ?limit cannot turn
    one call into an unbounded bill.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can trigger illustration generation"
        )

    from app.services.illustration_service import IllustrationService

    service = IllustrationService()
    if not settings.ILLUSTRATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Illustration generation is disabled (ILLUSTRATION_ENABLED is off)"
        )
    if not service.configured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No API key configured for illustration provider "
                f"'{service.provider}'"
            )
        )

    background_tasks.add_task(service.process_batch, limit)
    return {"status": "illustration_processing_initiated", "provider": service.provider}


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
        
    from app.services.search_service import SearchService
    search_service = SearchService()
    background_tasks.add_task(search_service.sync_all_processed_events)
    return {"status": "search_sync_initiated"}

