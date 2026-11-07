"""
GlobeLens AI — MediaController
================================
Config-gated image captioning (vision) and audio transcription adapters.

Mounted at /api/v1/media:
    POST /api/v1/media/analyze    — multipart image → AI caption (VISION_PROVIDER)
    POST /api/v1/media/transcribe — multipart audio → transcript (AUDIO_PROVIDER)

Every endpoint meters METER_MEDIA and enforces the plan quota ahead of the
provider call, then records usage only on success. Honest refusal rules:

  * No provider is configured (the default) → 503 explaining exactly what env
    to set. No fake captions, no "transcription unavailable" simulation.
  * A provider is selected but its credential is missing → 503 with that fact.
  * Over quota → 429 before any provider call (nothing is spent).
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import get_current_user
from src.core.database import get_db
from src.data.entities.models import User
from src.services.entitlement_service import (
    METER_MEDIA,
    UsageService,
    require_action_quota,
)
from src.services.media_providers import MediaProviderUnavailable

router = APIRouter()

_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB


def _provider_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, MediaProviderUnavailable):
        detail = str(exc)
    else:
        detail = f"Media provider call failed: {exc}"
    return HTTPException(status_code=503, detail=detail)


@router.post(
    "/analyze",
    summary="Generate an AI caption for an uploaded image (VISION_PROVIDER)",
)
async def analyze_image(
    image: Optional[UploadFile] = File(
        None, description="Image file (JPEG/PNG/WebP); either this or image_url"
    ),
    image_url: Optional[str] = Form(
        None, max_length=2000,
        description="Public http(s) URL of the image; either this or the file field",
    ),
    hint: Optional[str] = Form(
        None, max_length=500,
        description="Optional article context shown to the model (reference only)",
    ),
    current_user: User = Depends(get_current_user),
    _quota_guard: None = Depends(require_action_quota(METER_MEDIA)),
    db: AsyncSession = Depends(get_db),
):
    from src.services.media_providers import build_vision_provider

    if (image is None) == (image_url is None):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of 'image' (file) or 'image_url'.",
        )

    if image_url:
        if not image_url.lower().startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail="image_url must be a public http(s) address.",
            )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                media_resp = await client.get(image_url)  # noqa: ASYNC100
                media_resp.raise_for_status()
                bytes_ = media_resp.content
                media_type = media_resp.headers.get("content-type", "image/jpeg")
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not fetch image_url: {exc}",
            )
        if not bytes_ or len(bytes_) > _MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=400,
                detail="Image must be non-empty and smaller than 10 MB.",
            )
    else:
        assert image is not None
        bytes_ = await image.read()
        if not bytes_ or len(bytes_) > _MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=400,
                detail="Image must be non-empty and smaller than 10 MB.",
            )
        media_type = image.content_type or "image/jpeg"

    provider = build_vision_provider()
    try:
        result = await provider.caption_image(
            bytes_, media_type, hint=hint or ""
        )
    except Exception as exc:
        raise _provider_failure(exc)

    await UsageService(db).record(
        current_user.id,
        METER_MEDIA,
        meta={
            "kind": "vision",
            "provider": result.provider,
            "model": result.model,
            "by_url": bool(image_url),
        },
    )
    await db.commit()
    return {
        "caption": result.caption,
        "model": result.model,
        "provider": result.provider,
        "ai_generated": True,
    }


@router.post(
    "/transcribe",
    summary="Transcribe an uploaded audio file (AUDIO_PROVIDER)",
)
async def transcribe_audio(
    audio: UploadFile = File(..., description="Audio file (mp3/wav/ogg)"),
    language: Optional[str] = Form(
        None, min_length=2, max_length=6,
        description="Optional ISO-639-1 language hint (e.g. 'fr')",
    ),
    current_user: User = Depends(get_current_user),
    _quota_guard: None = Depends(require_action_quota(METER_MEDIA)),
    db: AsyncSession = Depends(get_db),
):
    from src.services.media_providers import build_audio_provider

    bytes_ = await audio.read()
    if not bytes_ or len(bytes_) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=400,
            detail="Audio must be non-empty and smaller than 25 MB.",
        )
    media_type = audio.content_type or "audio/mpeg"

    provider = build_audio_provider()
    try:
        result = await provider.transcribe(
            bytes_, media_type, language=language
        )
    except Exception as exc:
        raise _provider_failure(exc)

    await UsageService(db).record(
        current_user.id,
        METER_MEDIA,
        meta={
            "kind": "audio",
            "provider": result.provider,
            "model": result.model,
        },
    )
    await db.commit()
    return {
        "text": result.text,
        "model": result.model,
        "provider": result.provider,
        "ai_generated": True,
    }


@router.post(
    "/analyze-video",
    summary="Fetch and summarize a publisher video transcript (video path)",
)
async def analyze_video(
    video_url: str = Form(
        ...,
        min_length=16,
        max_length=2000,
        description="YouTube watch/shorts/embed URL declared as the story's video",
    ),
    article_id: Optional[str] = Form(
        None,
        description="Optional article UUID whose video_* columns should be persisted",
    ),
    current_user: User = Depends(get_current_user),
    _quota_guard: None = Depends(require_action_quota(METER_MEDIA)),
    db: AsyncSession = Depends(get_db),
):
    from src.services.media_service import MediaService

    if not video_url.lower().startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="video_url must be a public http(s) address.",
        )

    service = MediaService()
    try:
        transcript, source = await service.fetch_transcript(video_url)
        summary, model = await service.summarize_transcript(transcript)
    except Exception as exc:
        raise _provider_failure(exc)

    persisted = False
    if article_id:
        import uuid

        from sqlalchemy import select

        from src.data.entities.models import Article

        try:
            parsed_article = uuid.UUID(str(article_id))
        except ValueError:
            parsed_article = None
        if parsed_article is not None:
            article = (
                await db.execute(select(Article).where(Article.id == parsed_article))
            ).scalars().first()
            if article is not None:
                from datetime import datetime, timezone

                article.video_url = video_url
                article.video_transcript = transcript
                article.video_summary = summary
                article.video_summary_model = model
                article.video_processed_at = datetime.now(timezone.utc)
                persisted = True

    await UsageService(db).record(
        current_user.id,
        METER_MEDIA,
        meta={
            "kind": "video",
            "source": source,
            "model": model,
            "persisted_to_article": persisted,
        },
    )
    await db.commit()
    return {
        "video_url": video_url,
        "transcript": transcript,
        "summary": summary,
        "model": model,
        "persisted_to_article": persisted,
    }


@router.post(
    "/summarize",
    summary="Summarize a transcript of audio/video text (LLM)",
)
async def summarize_text(
    text: str = Form(
        ..., min_length=20, max_length=40000,
        description="Transcript text (from /transcribe or a captions fetch) to summarize",
    ),
    current_user: User = Depends(get_current_user),
    _quota_guard: None = Depends(require_action_quota(METER_MEDIA)),
    db: AsyncSession = Depends(get_db),
):
    from src.services.media_service import MediaService

    service = MediaService()
    try:
        summary, model = await service.summarize_transcript(text)
    except Exception as exc:
        raise _provider_failure(exc)

    await UsageService(db).record(
        current_user.id,
        METER_MEDIA,
        meta={"kind": "text_summary", "model": model},
    )
    await db.commit()
    return {
        "summary": summary,
        "model": model,
        "ai_generated": True,
    }