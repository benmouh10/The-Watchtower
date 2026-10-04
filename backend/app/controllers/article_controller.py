"""
GlobeLens AI — ArticleController
GET  /articles/{id} | /articles/{id}/audio | /articles/mine | /articles/authored
POST /articles | /articles/{id}/publish | /articles/{id}/unpublish | /articles/sync
PUT  /articles/{id} | DELETE /articles/{id}

Two kinds of rows share this table:

* PIPELINE — collected by the scraper, published on arrival, editable by any
  journalist or admin.
* AUTHORED — written in the newsroom by a journalist. They start as DRAFT and
  are only publicly readable once published; only their author (or an admin)
  may edit, publish or delete them.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Path, Query, status, Depends, BackgroundTasks, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.controllers.auth_controller import (
    bearer,
    get_current_user,
    get_current_user_optional,
    resolve_listener,
)
from app.core.config import settings
from app.core.database import get_db
from app.entities.models import (
    Article,
    ArticleOrigin,
    ProcessingStatus,
    PublicationStatus,
    User,
    UserRole,
)
from app.services.scraper_service import ScraperService
from app.services import audio_signing
from app.services.tts_service import (
    TTSUnavailable,
    article_narration_text,
    audio_extension,
    tts_service,
)

router = APIRouter()


class CreateArticleRequest(BaseModel):
    title: str
    content: str
    url: Optional[str] = None
    source_id: Optional[str] = None


class UpdateArticleRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    url: Optional[str] = None


def _parse_uuid(raw: str, label: str = "article") -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {label} UUID")


def _role_of(user: User) -> str:
    return user.role.value if hasattr(user.role, "value") else user.role


def _require_editor(current_user: User) -> None:
    """Content edits are open to journalists and admins."""
    if _role_of(current_user) not in (UserRole.ADMIN.value, UserRole.JOURNALIST.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Journalist or admin role required to modify articles",
        )


def _require_owner_or_admin(article: Article, current_user: User) -> None:
    """Authored articles belong to their writer; nobody else may change them.

    Pipeline rows are shared wire copy, so the editor check already done by the
    caller is sufficient for them.
    """
    if article.origin != ArticleOrigin.AUTHORED:
        return
    if _role_of(current_user) == UserRole.ADMIN.value:
        return
    if article.author_id == current_user.id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only the author or an admin can modify this article",
    )


def _serialize(article: Article) -> dict:
    """Shape one article row for the API, including source and author."""
    source = getattr(article, "source", None)
    author = getattr(article, "author", None)
    origin = article.origin.value if hasattr(article.origin, "value") else article.origin
    publication_status = (
        article.publication_status.value
        if hasattr(article.publication_status, "value")
        else article.publication_status
    )
    return {
        "id": str(article.id),
        "title": article.title,
        "content": article.content,
        "url": article.url,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "created_at": article.created_at.isoformat() if article.created_at else None,
        "event_id": str(article.event_id) if article.event_id else None,
        "is_hidden": article.is_hidden,
        "processing_status": (
            article.processing_status.value
            if hasattr(article.processing_status, "value")
            else article.processing_status
        ),
        "origin": origin,
        "publication_status": publication_status,
        "author": {"id": str(author.id), "name": author.name} if author else None,
        "source": (
            {
                "id": str(source.id),
                "name": source.name,
                "url": source.url,
                "country": source.country,
                "credibility_score": source.credibility_score,
            }
            if source
            else None
        ),
    }


def _validate_url(url: Optional[str]) -> Optional[str]:
    if url is None:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    if not cleaned.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must be http(s)")
    return cleaned


async def _load_article(db: AsyncSession, article_uuid: uuid.UUID) -> Optional[Article]:
    """Load one article with the relationships _serialize touches.

    A bare db.get() leaves source/author as lazy loads, which raise
    MissingGreenlet under async SQLAlchemy the moment _serialize reads them.
    """
    result = await db.execute(
        select(Article)
        .where(Article.id == article_uuid)
        .options(selectinload(Article.source), selectinload(Article.author))
    )
    return result.scalar_one_or_none()


# ── Static paths first: /mine and /authored must not be captured by /{id} ──────
@router.get("/mine", summary="List the articles authored by the caller")
async def list_my_articles(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Drafts and published pieces written by the signed-in journalist/admin."""
    _require_editor(current_user)
    result = await db.execute(
        select(Article)
        .where(Article.author_id == current_user.id, Article.origin == ArticleOrigin.AUTHORED)
        .options(selectinload(Article.author))
        .order_by(Article.created_at.desc())
    )
    return {"articles": [_serialize(a) for a in result.scalars().all()]}


@router.get("/authored", summary="List published newsroom articles (public)")
async def list_authored_articles(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Public feed of published, non-hidden pieces written in the newsroom."""
    offset = (page - 1) * limit
    base = select(Article).where(
        Article.origin == ArticleOrigin.AUTHORED,
        Article.publication_status == PublicationStatus.PUBLISHED,
        Article.is_hidden.is_(False),
    )
    result = await db.execute(
        base.options(selectinload(Article.author))
        .order_by(Article.published_at.desc().nullslast(), Article.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    total = await db.scalar(
        select(func.count())
        .select_from(Article)
        .where(
            Article.origin == ArticleOrigin.AUTHORED,
            Article.publication_status == PublicationStatus.PUBLISHED,
            Article.is_hidden.is_(False),
        )
    ) or 0
    return {
        "articles": [_serialize(a) for a in result.scalars().all()],
        "page": page,
        "limit": limit,
        "total": total,
    }


@router.get("/{article_id}", summary="Get article by ID")
async def get_article(
    article_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Return the stored article.

    Authored drafts are only visible to their author or an admin; everyone else
    gets a 404 so an unpublished piece cannot be probed for existence.
    """
    article_uuid = _parse_uuid(article_id)
    article = await _load_article(db, article_uuid)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    if (
        article.origin == ArticleOrigin.AUTHORED
        and article.publication_status == PublicationStatus.DRAFT
    ):
        is_owner = current_user is not None and article.author_id == current_user.id
        is_admin = current_user is not None and _role_of(current_user) == UserRole.ADMIN.value
        if not (is_owner or is_admin):
            raise HTTPException(status_code=404, detail="Article not found")

    return _serialize(article)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Write a new article (Journalist)")
async def create_article(
    payload: CreateArticleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create an authored draft. Only journalists and admins may write."""
    _require_editor(current_user)

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    article = Article(
        title=title,
        content=content,
        url=_validate_url(payload.url),
        origin=ArticleOrigin.AUTHORED,
        publication_status=PublicationStatus.DRAFT,
        # Authored pieces never enter the scraping/embedding pipeline, so they
        # are considered fully processed and carry no event until linked.
        processing_status=ProcessingStatus.PROCESSED,
        author_id=current_user.id,
    )
    db.add(article)
    await db.commit()
    return _serialize(await _load_article(db, article.id))


@router.put("/{article_id}", summary="Update article content (Journalist/Admin)")
async def update_article(
    payload: UpdateArticleRequest,
    article_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persist the edited fields, respecting authored-article ownership."""
    _require_editor(current_user)

    article_uuid = _parse_uuid(article_id)
    article = await _load_article(db, article_uuid)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    _require_owner_or_admin(article, current_user)

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        article.title = title
    if payload.content is not None:
        article.content = payload.content
    if payload.url is not None:
        article.url = _validate_url(payload.url)

    await db.commit()
    return {"message": "Article updated", **_serialize(await _load_article(db, article_uuid))}


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete article")
async def delete_article(
    article_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete, restricted to editors (and, for authored rows, the author)."""
    _require_editor(current_user)

    article_uuid = _parse_uuid(article_id)
    article = await db.get(Article, article_uuid)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    _require_owner_or_admin(article, current_user)

    await db.delete(article)
    await db.commit()
    return None


@router.post("/{article_id}/publish", summary="Publish an authored article")
async def publish_article(
    article_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move an authored draft into the public feed."""
    return await _set_publication_status(article_id, True, db, current_user)


@router.post("/{article_id}/unpublish", summary="Return an authored article to draft")
async def unpublish_article(
    article_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hide an authored article again without deleting it."""
    return await _set_publication_status(article_id, False, db, current_user)


async def _set_publication_status(
    article_id: str, publish: bool, db: AsyncSession, current_user: User
) -> dict:
    _require_editor(current_user)

    article_uuid = _parse_uuid(article_id)
    article = await _load_article(db, article_uuid)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.origin != ArticleOrigin.AUTHORED:
        raise HTTPException(
            status_code=400,
            detail="Only newsroom-authored articles can be published",
        )
    _require_owner_or_admin(article, current_user)

    if publish:
        article.publication_status = PublicationStatus.PUBLISHED
        if article.published_at is None:
            article.published_at = datetime.now(timezone.utc)
    else:
        article.publication_status = PublicationStatus.DRAFT

    await db.commit()

    if publish:
        # Warm the narration in the background: nobody should wait on speech
        # synthesis for a piece that was just published.
        from app.services.narration_warmer import narration_warmer
        narration_warmer.enqueue_article(article_uuid)

    return {
        "message": "Article published" if publish else "Article unpublished",
        **_serialize(await _load_article(db, article_uuid)),
    }


@router.get("/{article_id}/audio/url", summary="Mint a signed playback URL for an article")
async def get_article_audio_url(
    article_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a short-lived token that lets a media element stream the narration.

    <audio src> cannot carry an Authorization header, so the player needs a link it
    can load directly. The token is scoped to this article and this listener, which
    is what lets the audio route keep enforcing draft visibility.
    """
    article_uuid = _parse_uuid(article_id)
    article = await db.get(Article, article_uuid)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _require_audio_access(article, current_user)

    minted = audio_signing.issue(
        f"article:{article_uuid}", current_user.id, _role_of(current_user),
        settings.AUDIO_URL_TTL_SECONDS,
    )
    return {"token": minted["token"], "expires_at": minted["expires_at"]}


@router.get("/{article_id}/audio", summary="Get audio narration for an article")
async def get_article_audio(
    article_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    sig: Optional[str] = Query(
        None, description="Signed playback token; alternative to the Authorization header"
    ),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
):
    """Stream an audio narration of the article body.

    Requires an account, presented either as a bearer token or as the signed token
    from /audio/url. Each result is cached by content hash, meaning an unchanged
    article is narrated once per TTL rather than on every play.

    MP3 is sent as a stream so playback starts on the first chunk instead of after
    the whole recording. Fails with 501 while no speech provider is configured, and
    502 when the provider is configured but cannot deliver audio; the first chunk is
    synthesised before the response starts so a provider failure is still reported
    as a status code rather than a truncated file.
    """
    article_uuid = _parse_uuid(article_id)
    article = await db.get(Article, article_uuid)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    listener = await resolve_listener(f"article:{article_uuid}", sig, credentials, db)
    _require_audio_access(article, listener)

    if not tts_service.configured:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Text-to-speech is not configured: set TTS_PROVIDER and its API key.",
        )

    return await _stream_narration(
        tts_service, article_narration_text(article), article.title or "article"
    )


def _require_audio_access(article, listener) -> None:
    """Same visibility rule as GET /articles/{id}, for either caller shape.

    An unpublished draft stays readable only by its author and admins; anyone else
    gets a 404 rather than a hint that the draft exists.
    """
    if (
        article.origin == ArticleOrigin.AUTHORED
        and article.publication_status == PublicationStatus.DRAFT
    ):
        is_owner = article.author_id == listener.id
        is_admin = _role_of(listener) == UserRole.ADMIN.value
        if not (is_owner or is_admin):
            raise HTTPException(status_code=404, detail="Article not found")


async def _stream_narration(service, text: str, title: str):
    """Answer with a progressively streamed recording, or a buffered one.

    Streaming is what removes the wait: the first chunk is synthesised before the
    response headers go out, so a provider failure still surfaces as 502, and the
    rest arrives while the clip is already playing.
    """
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()[:60]
    headers = {
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": f'inline; filename="{stem or "narration"}.mp3"',
    }

    if not service.streams_progressively:
        try:
            audio, media_type = await service.narrate(text)
        except TTSUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        headers["Content-Disposition"] = (
            f'inline; filename="{stem or "narration"}{audio_extension(media_type)}"'
        )
        return Response(content=audio, media_type=media_type, headers=headers)

    stream = service.iter_audio(text)
    try:
        first_chunk = await anext(stream)
    except TTSUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except StopAsyncIteration:
        # Headers are not out yet, so this can still be reported honestly.
        await stream.aclose()
        raise HTTPException(status_code=502, detail="Speech synthesis produced no audio")

    async def body():
        yield first_chunk
        async for part in stream:
            yield part

    return StreamingResponse(body(), media_type="audio/mpeg", headers=headers)


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED, summary="Trigger RSS & Playwright scraping sync pipeline (Admin)")
async def sync_articles(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Trigger the asynchronous ingestion pipeline. Admin only.

    Ingestion is an infrastructure action, not an editorial one: journalists
    write articles, they do not control what the platform collects.
    """
    if _role_of(current_user) != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can sync articles",
        )

    scraper_service = ScraperService()
    background_tasks.add_task(scraper_service.run_pipeline)
    return {"status": "sync_initiated"}
