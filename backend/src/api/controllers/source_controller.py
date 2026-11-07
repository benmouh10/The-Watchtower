"""
GlobeLens AI — SourceController
GET /sources                     list publishers with bias + credibility ratings
GET /sources/bias-spectrum       aggregate distribution across the catalogue

Why this exists: per-source bias was exposed only as a field on individual
events. There was no way to ask "what is our stance on these publishers?", which
is the question the Bias Detection Visualization feature is actually about.

Integrity rules honoured here:
  * bias_lean / credibility_score are reported as null when never assessed.
    A null is rendered as "not rated", never as centre or 0.5.
  * bias_score is DERIVED from the stored enum, not invented. It is null unless
    a real classification exists.
  * Counts are of distinct articles actually in the database.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.api.controllers.auth_controller import AuthIdentity
from src.api.controllers.public_read_guard import require_public_read
from src.services.api_key_service import KEY_SCOPE_READ_SOURCES
from src.data.entities.models import Source

router = APIRouter()

# Maps the stored ordinal enum onto a -1..+1 scale for plotting. This is a
# rendering aid derived from an existing real rating; it is not a new judgement.
_BIAS_SCALE = {
    "LEFT": -1.0,
    "CENTER_LEFT": -0.5,
    "CENTER": 0.0,
    "CENTER_RIGHT": 0.5,
    "RIGHT": 1.0,
}


def _serialize_source(src: Source, article_count: int = 0) -> Dict[str, Any]:
    lean = src.bias_lean.name if src.bias_lean else None
    return {
        "id": str(src.id),
        "name": src.name,
        "url": src.url,
        "country": src.country,
        "bias_lean": lean,
        # Derived from bias_lean; None when the outlet has never been classified.
        "bias_score": _BIAS_SCALE.get(lean) if lean else None,
        "is_classified": lean is not None,
        "credibility_score": src.credibility_score,
        "is_rated": src.credibility_score is not None,
        "article_count": article_count,
    }


@router.get("", summary="List all tracked publishers with their ratings")
async def list_sources(
    db: AsyncSession = Depends(get_db),
    classified_only: bool = Query(
        False, description="Return only outlets that have a real bias classification"
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_SOURCES)
    ),
):
    """
    Every publisher in the catalogue.

    `classified_only=True` is a genuine filter, not a presentation trick: it
    selects rows where bias_lean is actually set, so a client can build a
    spectrum without having to filter nulls itself.
    """
    from src.data.entities.models import Article

    count_q = (
        select(func.count(Article.id))
        .where(Article.source_id == Source.id)
        .correlate(Source)
        .scalar_subquery()
    )

    query = select(Source, count_q.label("article_count"))
    if classified_only:
        query = query.where(Source.bias_lean.isnot(None))
    query = query.order_by(Source.name).offset(offset).limit(limit)

    rows = (await db.execute(query)).all()

    return {
        "sources": [_serialize_source(s, n or 0) for s, n in rows],
        "count": len(rows),
        "offset": offset,
        "limit": limit,
    }


@router.get("/bias-spectrum", summary="Aggregate bias distribution across all publishers")
async def bias_spectrum(
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_SOURCES)
    ),
):
    """
    Distribution of publishers across the five-point lean scale, plus how many
    remain unclassified.

    Every one of the five buckets is always present in the response, including
    empty ones, so a chart renders an honest gap instead of silently dropping a
    column. `unclassified` is reported separately rather than folded into CENTER,
    which is the specific mistake this project has been removing.
    """
    from src.data.entities.models import Article

    count_q = (
        select(func.count(Article.id))
        .where(Article.source_id == Source.id)
        .correlate(Source)
        .scalar_subquery()
    )
    rows = (
        await db.execute(select(Source.bias_lean, Source.credibility_score, count_q.label("n")))
    ).all()

    buckets = {key: {"publishers": 0, "articles": 0} for key in _BIAS_SCALE}
    unclassified_publishers = 0
    unclassified_articles = 0
    unrated_credibility = 0

    for lean, credibility, n in rows:
        articles = n or 0
        if credibility is None:
            unrated_credibility += 1
        if lean is None:
            unclassified_publishers += 1
            unclassified_articles += articles
            continue
        bucket = buckets[lean.name]
        bucket["publishers"] += 1
        bucket["articles"] += articles

    classified_values = [
        _BIAS_SCALE[lean.name]
        for lean, _, _ in rows
        if lean is not None
    ]
    mean_bias = (
        round(sum(classified_values) / len(classified_values), 3)
        if classified_values
        else None
    )

    return {
        "buckets": [
            {"bias_lean": key, "bias_score": _BIAS_SCALE[key], **buckets[key]}
            for key in _BIAS_SCALE
        ],
        "unclassified": {
            "publishers": unclassified_publishers,
            "articles": unclassified_articles,
        },
        "unrated_credibility": unrated_credibility,
        "total_publishers": len(rows),
        "classified_publishers": len(classified_values),
        "mean_bias": mean_bias,
        "note": (
            "mean_bias is computed only over outlets with a real classification. "
            "Unclassified outlets are excluded rather than counted as centre."
        ),
    }
