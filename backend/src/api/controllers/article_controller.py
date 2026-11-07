"""
GlobeLens AI — ArticleController
GET  /articles/{id} | /events/{id}/articles | /articles/{id}/audio
POST /articles | PUT /articles/{id} | DELETE /articles/{id}
POST /articles/sync
"""
import uuid
from typing import Optional, List
from fastapi import APIRouter, Path, Body, status, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel

from src.api.controllers.auth_controller import get_current_user
from src.data.entities.models import User, UserRole
from src.services.scraper_service import ScraperService

router = APIRouter()


class CreateArticleRequest(BaseModel):
    title: str
    content: str
    url: str
    source_id: Optional[str] = None


@router.get("/{article_id}", summary="Get article by ID")
async def get_article(article_id: str = Path(...)):
    """
    Not implemented. This previously returned a hardcoded
    {"title": "Placeholder Article"}, i.e. a fabricated headline. Real article
    content is served as part of GET /api/v1/events/{event_id}.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Standalone article lookup is not implemented. Use /api/v1/events/{event_id}.",
    )


@router.post("", status_code=status.HTTP_501_NOT_IMPLEMENTED, summary="Submit a new article (Journalist)")
async def create_article(
    payload: CreateArticleRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Not implemented. This previously returned 201 "Article submitted" without
    authentication and without storing anything. Articles are only created by
    the ingestion pipeline (POST /articles/sync).
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Direct article submission is not implemented; nothing was stored.",
    )


@router.put("/{article_id}", summary="Update article content (Journalist/Admin)")
async def update_article(
    article_id: str = Path(...),
    payload: CreateArticleRequest = Body(...),
    current_user: User = Depends(get_current_user),
):
    """
    Not implemented. Previously returned "Article updated" while writing
    nothing. Article text is treated as immutable source evidence, so editing
    is deliberately not offered.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Article editing is not implemented; article text is immutable evidence.",
    )


@router.delete("/{article_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED, summary="Delete article")
async def delete_article(
    article_id: str = Path(...),
    current_user: User = Depends(get_current_user),
):
    """
    Not implemented. Previously a public 204 that deleted nothing. Use the admin
    hide endpoint to take an article out of public view without destroying it.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Article deletion is not implemented. Use PUT /api/v1/admin/articles/{id}/hide instead.",
    )


@router.get("/{article_id}/audio", summary="Get TTS audio stream for article")
async def get_article_audio(article_id: str = Path(...)):
    """
    Not implemented. Previously returned a placeholder object instead of audio.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Text-to-speech is not implemented.",
    )


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED, summary="Trigger RSS & Playwright scraping sync pipeline (Admin/Journalist)")
async def sync_articles(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """
    Trigger the asynchronous data ingestion pipeline.
    Authorized for Admin and Journalist roles only.
    """
    if current_user.role not in (UserRole.ADMIN, UserRole.JOURNALIST):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only journalists and administrators can sync articles"
        )
        
    scraper_service = ScraperService()
    background_tasks.add_task(scraper_service.run_pipeline)
    return {"status": "sync_initiated"}
