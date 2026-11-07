"""
GlobeLens AI — EventController
GET  /events | /events/{id} | /events/trending | /events/latest
GET  /events/country/{country} | /events/topic/{topic} | /events/map
POST /events/{id}/follow
"""
from fastapi import APIRouter, Path, Query, Depends, HTTPException, status
import uuid as _uuid
import re
import structlog
from typing import Any, Dict, List, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.database import get_db
from src.api.controllers.auth_controller import AuthIdentity, get_current_user, get_current_user_optional
from src.api.controllers.public_read_guard import require_public_read
from src.services.api_key_service import KEY_SCOPE_READ_EVENTS
from src.data.entities.models import Article, Event, EventFollow, User

router = APIRouter()
logger = structlog.get_logger()


def _parse_event_uuid(event_id: str) -> _uuid.UUID:
    try:
        return _uuid.UUID(str(event_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid event id")


async def _require_event(db: AsyncSession, event_id: str) -> _uuid.UUID:
    """Validate the path id and confirm the event exists; 400 / 404 otherwise."""
    parsed = _parse_event_uuid(event_id)
    evt = (
        await db.execute(select(Event.id).where(Event.id == parsed))
    ).first()
    if evt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return parsed


def _serialize_event_summary(evt: Event) -> Dict[str, Any]:
    """
    Compact event shape shared by list, map, trending, latest, country and topic.

    `source_count` counts DISTINCT outlets, not articles. Two articles from the
    same publisher are not two sources, and counting them inflated the number on
    the list and map endpoints.
    """
    outlet_ids = {a.source_id for a in (evt.articles or []) if a.source_id is not None}
    contradiction = _contradiction_signal(evt.contradictions)
    return {
        "id": str(evt.id),
        "title": evt.title,
        "summary": evt.summary or "",
        "topic": evt.topic or "WORLD",
        "country": evt.country or "Unknown",
        "latitude": evt.latitude,
        "longitude": evt.longitude,
        "importance_score": evt.importance_score,
        "source_count": len(outlet_ids) or len(evt.articles or []),
        # None means "not assessed", not center. Clients must not render an
        # unclassified event as neutral; see is_classified below.
        "bias_lean": evt.bias_lean.name if evt.bias_lean else None,
        "is_classified": evt.bias_lean is not None,
        # Cross-source conflict signal derived from stored contradiction rows at
        # serialisation time (no separate column). level uses the strongest
        # scored pair; "reported" marks unscored legacy rows.
        "contradiction_count": contradiction["count"],
        "contradiction_max_confidence": contradiction["max_confidence"],
        "contradiction_level": contradiction["level"],
        "status": evt.status,
        "created_at": evt.created_at.isoformat() if evt.created_at else None,
        "updated_at": evt.updated_at.isoformat() if evt.updated_at else None,
    }


_BIAS_SCALE = {
    "LEFT": -1.0,
    "CENTER_LEFT": -0.5,
    "CENTER": 0.0,
    "CENTER_RIGHT": 0.5,
    "RIGHT": 1.0,
}

# Grading ladder for the strongest scored contradiction in an event. The web
# detector's formula floors near 0.35 (negation) / 0.49 (numeric), so 0.3/0.5/0.75
# split the reachable range into meaningful bands rather than a uniform 0..1.
_CONTRADICTION_LEVELS = (
    ("strong", 0.75),
    ("moderate", 0.5),
    ("weak", 0.3),
)


def _contradiction_signal(raw: Optional[List[Any]]) -> Dict[str, Any]:
    """
    Harden stored contradiction rows and derive an event-level signal.

    Rows written before the web ContradictionService existed (the offline
    pipeline emitted pairs with no confidence key) are kept intact with
    confidence None rather than synthesised, so a client can render "not scored"
    honestly. A confidence outside 0..1 is invalid data and is also surfaced as
    None — clamping it would assert a score the pipeline never gave.

    Returns:
        {
          "pairs": normalized list (each pair carries a confidence float|None),
          "count": number of usable pairs (non-dict rows are ignored),
          "max_confidence": strongest scored pair, or None,
          "level": "strong"|"moderate"|"weak"|"minimal"|"reported"|"none",
        }

    "reported" means pairs exist but none carried a score; it is distinct from
    "none" (no conflict detected) and from "weak" (scored, borderline).
    """
    cleaned: List[Dict[str, Any]] = []
    max_confidence: Optional[float] = None

    for item in raw or []:
        if not isinstance(item, dict):
            continue
        conf = item.get("confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            confidence = None
        else:
            confidence = float(conf)
            if not (0.0 <= confidence <= 1.0):
                confidence = None
        cleaned.append({**item, "confidence": confidence})
        if confidence is not None:
            max_confidence = confidence if max_confidence is None else max(max_confidence, confidence)

    if not cleaned:
        level = "none"
    elif max_confidence is None:
        level = "reported"
    else:
        level = "weak"
        for name, threshold in _CONTRADICTION_LEVELS:
            if max_confidence >= threshold:
                level = name
                break

    return {
        "pairs": cleaned,
        "count": len(cleaned),
        "max_confidence": round(max_confidence, 2) if max_confidence is not None else None,
        "level": level,
    }


def _build_transparency(
    articles: List[Dict[str, Any]],
    contradictions: Optional[List[Dict[str, Any]]],
    citation_sources: Optional[List[str]],
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Derive the Article Transparency Panel from real data only.

    editor_confidence_score is a transparent formula, not a stored opinion:
        100 * (0.45*source_credibility + 0.25*source_diversity + 0.30*agreement)
    where agreement is reduced by detected contradictions. Every term is derived
    from stored data so a reader can see why the number is what it is.
    """
    visible = [a for a in articles if not a.get("is_hidden")]
    outlet_names = {a["source"]["name"] for a in visible if a.get("source")}

    # Only sources that actually carry a credibility rating contribute to the
    # credibility term. Treating an unrated source as 0.0 would drag the score
    # down as though we had assessed it and found it worthless; treating it as
    # 0.5 would assert a neutral rating. Neither is true, so it is excluded and
    # the count is reported.
    credibility_values = [
        float(a["source"]["credibility_score"])
        for a in visible
        if a.get("source") and a["source"].get("credibility_score") is not None
    ]
    unrated_source_count = sum(
        1
        for a in visible
        if a.get("source") and a["source"].get("credibility_score") is None
    )
    source_credibility = (
        sum(credibility_values) / len(credibility_values) if credibility_values else 0.0
    )

    source_diversity = min(1.0, (len(outlet_names) - 1) / 3.0) if outlet_names else 0.0

    detected = contradictions or []
    penalty = sum(float(c.get("confidence") or 0.0) for c in detected)

    # Outlet agreement is only meaningful across two or more independent
    # sources. With a single source (or none) there is nothing to agree or
    # disagree with, so the term is dropped rather than defaulting to a
    # perfect 1.0 — otherwise an empty dossier would score 30/100 purely
    # from agreement it never had.
    agreement_applicable = len(outlet_names) >= 2
    agreement = (1.0 / (1.0 + penalty)) if agreement_applicable else 0.0

    confidence = round(
        100.0 * (0.45 * source_credibility + 0.25 * source_diversity + 0.30 * agreement),
        1,
    )

    # Bias consensus averages only outlets with a real classification.
    bias_values = [
        _BIAS_SCALE[a["source"]["bias_lean"]]
        for a in visible
        if a.get("source") and a["source"].get("bias_lean") in _BIAS_SCALE
    ]
    unclassified_count = sum(
        1
        for a in visible
        if a.get("source") and a["source"].get("bias_lean") not in _BIAS_SCALE
    )
    consensus_bias = round(sum(bias_values) / len(bias_values), 2) if bias_values else None

    # Citation provenance as a ratio, not a boolean: how many of the summary's
    # inline [N] markers can actually be resolved to the ordered source list
    # read during synthesis. A marker past the end of that list is an orphan.
    citation_markers = len(re.findall(r"\[\d+\]", summary or ""))
    citation_sources_count = len(citation_sources or [])
    unresolved_markers = max(0, citation_markers - citation_sources_count)
    if citation_sources:
        provenance = (
            "verified"
            if unresolved_markers == 0 and citation_markers > 0
            else "partial"
        )
    else:
        provenance = "unavailable"

    return {
        # Every article on record for the event, including ones condensed out of
        # the visible set, so the headline number can never be gamed by hiding
        # duplicates or low-quality wires.
        "article_count": len(articles),
        # Distinct source documents actually used in the synthesis.
        "source_count": len(visible),
        "outlets": sorted(outlet_names),
        "mean_source_credibility": round(source_credibility, 3),
        "credibility_rated_sources": len(credibility_values),
        "unrated_sources": unrated_source_count,
        "contradictions_detected": len(detected),
        "editor_confidence_score": confidence,
        "confidence_factors": {
            "source_credibility": round(source_credibility, 3),
            "source_diversity": round(source_diversity, 3),
            "agreement": round(agreement, 3),
        },
        "agreement_applicable": agreement_applicable,
        "bias_consensus": consensus_bias,
        "unclassified_sources": unclassified_count,
        "citation_provenance": provenance,
        "citation_markers": citation_markers,
        "citation_sources_count": citation_sources_count,
        "citation_unresolved_markers": unresolved_markers,
    }


@router.get("", summary="List all events (paginated)")
async def list_events(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    from sqlalchemy.orm import selectinload
    
    offset = (page - 1) * limit
    
    # Total count of PROCESSED events
    total_query = select(func.count(Event.id)).where(Event.status == "PROCESSED")
    total_count = await db.scalar(total_query) or 0
    
    # Paginated PROCESSED events. created_at goes to server precision, so the
    # id is appended as a tiebreak: with only one sort key a live insert shifts
    # every subsequent page by one (unstable offset pagination over DESC).
    events_query = (
        select(Event)
        .where(Event.status == "PROCESSED")
        .options(selectinload(Event.articles))
        .order_by(Event.created_at.desc(), Event.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(events_query)
    events = result.scalars().all()
    
    event_list = [_serialize_event_summary(evt) for evt in events]

    return {
        "events": event_list,
        "page": page,
        "limit": limit,
        "total": total_count
    }


@router.get("/trending", summary="Get trending events by importance score")
async def get_trending(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    """
    Events ordered by importance_score, newest first as the tiebreak.

    Previously returned `200 {"events": []}` with a TODO, so a client could not
    tell "nothing is trending" apart from "this was never built".

    Caveat worth stating plainly: importance_score is a single LLM judgement
    assigned once at synthesis time, not a velocity signal. Nothing here counts
    how fast coverage is growing, so this is "highest rated", not "trending now".
    """
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Event)
        .where(Event.status == "PROCESSED")
        .options(selectinload(Event.articles))
        .order_by(Event.importance_score.desc(), Event.created_at.desc(), Event.id.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return {"events": [_serialize_event_summary(e) for e in events], "limit": limit}


@router.get("/latest", summary="Get latest events ordered by creation date")
async def get_latest(
    limit: int = Query(20, ge=1, le=100),
    since: Optional[str] = Query(
        None,
        description=(
            "ISO 8601 timestamp; when given, only events created after it are "
            "returned (with recently-announced events overlaid from the realtime "
            "cache, so a reconnecting SSE client can catch up)."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    """Most recently created PROCESSED events. Previously a silent empty list."""
    from datetime import datetime, timezone

    from sqlalchemy.orm import selectinload

    since_dt: Optional[datetime] = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid 'since' timestamp; use ISO 8601.",
            )

    statement = select(Event).where(Event.status == "PROCESSED")
    if since_dt is not None:
        statement = statement.where(Event.created_at > since_dt)
    result = await db.execute(
        statement
        .options(selectinload(Event.articles))
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(limit)
    )
    events = list(result.scalars().all())

    cache_source: Optional[str] = None
    if since_dt is not None:
        # Overlay announcements recorded while the client was offline. The
        # bounded cache is newest-first and may include events the DB query
        # above also returned; the cache copy wins so reconnects are idempotent.
        try:
            from src.services.realtime_service import recent_announcements

            recent = await recent_announcements()
            older_than_since = [
                a
                for a in recent
                if a.get("created_at") and a.get("created_at") > since_dt.isoformat()
            ]
            if older_than_since:
                cache_source = "redis"
                cached_ids = [a["id"] for a in older_than_since]
                cached = await db.execute(
                    select(Event)
                    .where(
                        Event.id.in_(cached_ids),
                        Event.status == "PROCESSED",
                    )
                    .options(selectinload(Event.articles))
                )
                by_id = {str(e.id): e for e in cached.scalars().all()}
                existing = {str(e.id) for e in events}
                for a in older_than_since:
                    evt = by_id.get(a["id"])
                    if evt is None or str(evt.id) in existing:
                        continue
                    events.append(evt)
                    existing.add(str(evt.id))
        except Exception as err:
            # Never fail a list endpoint because the reminder cache hiccuped.
            logger.warning("Announcement cache miss for /events/latest", error=str(err))

    payload = {"events": [_serialize_event_summary(e) for e in events], "limit": limit}
    if since_dt is not None:
        payload["since"] = since_dt.isoformat()
        payload["cache"] = cache_source
    return payload


@router.get("/map", summary="Get events with geolocation data for map view")
async def get_map_events(
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    """Returns lat/lon + metadata for the map interface."""
    from sqlalchemy.orm import selectinload
    
    result = await db.execute(
        select(Event)
        .where(
            Event.status == "PROCESSED",
            Event.latitude.isnot(None),
            Event.longitude.isnot(None)
        )
        .options(selectinload(Event.articles))
    )
    events = result.scalars().all()
    
    event_list = [_serialize_event_summary(evt) for evt in events]

    return {"events": event_list}


@router.get("/country/{country}", summary="Filter events by country")
async def get_events_by_country(
    country: str = Path(..., description="Country name"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    """
    PROCESSED events whose stored country matches, case-insensitively.

    Previously returned `200 {"events": []}` with a TODO.
    """
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Event)
        .where(
            Event.status == "PROCESSED",
            func.lower(Event.country) == country.strip().lower(),
        )
        .options(selectinload(Event.articles))
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return {
        "country": country,
        "events": [_serialize_event_summary(e) for e in events],
        "count": len(events),
    }


@router.get("/topic/{topic}", summary="Filter events by topic")
async def get_events_by_topic(
    topic: str = Path(...),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    """
    PROCESSED events for a topic, case-insensitively.

    Previously returned `200 {"events": []}` with a TODO, so the topic filter on
    the home page silently showed nothing. An unmatched topic is a real empty
    result, and `count` distinguishes that from a failure.
    """
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Event)
        .where(
            Event.status == "PROCESSED",
            func.lower(func.coalesce(Event.topic, "")) == topic.strip().lower(),
        )
        .options(selectinload(Event.articles))
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return {
        "topic": topic,
        "events": [_serialize_event_summary(e) for e in events],
        "count": len(events),
    }


@router.get("/{event_id}", summary="Get a single event by ID")
async def get_event(
    event_id: str = Path(..., description="Event UUID"),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    from sqlalchemy.orm import selectinload
    from fastapi import HTTPException
    import uuid
    
    try:
        event_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event UUID format")
        
    query = (
        select(Event)
        .where(Event.id == event_uuid)
        .options(selectinload(Event.articles).selectinload(Article.source))
    )
    result = await db.execute(query)
    evt = result.scalars().first()
    
    if not evt:
        raise HTTPException(status_code=404, detail="Event not found")
        
    articles_list = []
    for art in evt.articles:
        articles_list.append({
            "id": str(art.id),
            "title": art.title,
            "content": art.content or "",
            "url": art.url,
            "published_at": art.published_at.isoformat() if art.published_at else None,
            # Media the source actually published for the story, captured at
            # ingest time. image_url null means the article has none.
            "image_url": art.image_url,
            "image_caption": art.image_caption,
            "media_type": art.media_type,
            # AI caption written by the (config-gated) vision adapter, kept
            # distinct from the source's alt text. Null until VISION_PROVIDER
            # is enabled and the caption has run.
            "image_ai_caption": art.image_ai_caption,
            "image_ai_caption_model": art.image_ai_caption_model,
            "is_hidden": art.is_hidden,
            "source": {
                "name": art.source.name if art.source else "Unknown",
                "country": art.source.country if art.source else None,
                # Null rather than a 0.5/0.0 stand-in: an outlet we have no
                # rating for is not a 50%-credible outlet. The UI renders these
                # as unclassified instead of inventing a neutral reading.
                "credibility_score": art.source.credibility_score if art.source else None,
                # Likewise "CENTER" would assert a political leaning we have no
                # evidence for, and on a bias spectrum it is indistinguishable
                # from a genuinely centred outlet.
                "bias_lean": art.source.bias_lean.name
                if art.source and art.source.bias_lean
                else None,
                "is_classified": bool(art.source and art.source.bias_lean),
            }
        })

    contradiction_signal = _contradiction_signal(evt.contradictions)
    contradictions = contradiction_signal["pairs"]
    citation_sources = evt.citation_sources or None
    transparency = _build_transparency(
        articles_list, contradictions, citation_sources, evt.summary
    )

    # Per-claim attribution. citation_indices are 1-based positions into the
    # event's ordered citation_sources, which is the same ordering that resolves
    # the inline [N] markers in the summary, so a claim and a marker always
    # point at the same article.
    #
    # citation_sources is the authoritative order and MUST be used here.
    # evt.articles is ordered by (created_at, id), and created_at is a
    # transaction-scoped now(), so every article in a cluster shares one
    # timestamp and the tie is broken by a random UUID. Indexing off that would
    # attribute claims to articles in an order unrelated to the one the model
    # actually saw -- a silent misattribution on the exact surface meant to make
    # attribution trustworthy. The frontend's [N] resolution already keys off
    # citation_sources for the same reason.
    #
    # The claim is serialised together with the article metadata behind each
    # index, so the client can render "reported by X, Y" without having to join
    # two arrays positionally. An index with no matching article is reported in
    # `unresolved_indices` rather than silently dropped, because a dangling
    # attribution is a bug worth surfacing, not hiding.
    by_index: Dict[int, Dict[str, Any]] = {}
    visible_articles = [a for a in (evt.articles or []) if not a.is_hidden]
    by_id = {str(a.id): a for a in visible_articles}

    def _payload(index: int, art: Article) -> Dict[str, Any]:
        return {
            "index": index,
            "article_id": str(art.id),
            "title": art.title,
            "url": art.url,
            "published_at": art.published_at.isoformat() if art.published_at else None,
            "image_url": art.image_url,
            "image_caption": art.image_caption,
            "media_type": art.media_type,
            "source_name": art.source.name if art.source else None,
            "source_bias_lean": (
                art.source.bias_lean.name
                if art.source and art.source.bias_lean
                else None
            ),
        }

    if citation_sources:
        for position, article_id in enumerate(citation_sources, start=1):
            art = by_id.get(str(article_id))
            if art is not None:
                by_index[position] = _payload(position, art)
    else:
        # Legacy event synthesised before citation_sources existed. There is no
        # recorded order to honour, so the visible-article order is the only
        # thing available. Claim indices from such an event are unlikely to
        # exist at all, since both were introduced together.
        for position, art in enumerate(visible_articles, start=1):
            by_index[position] = _payload(position, art)

    claims_payload: List[Dict[str, Any]] = []
    for claim in (evt.claims or []):
        sources = []
        unresolved: List[int] = []
        for idx in (claim.citation_indices or []):
            resolved = by_index.get(int(idx))
            if resolved:
                sources.append(resolved)
            else:
                unresolved.append(int(idx))
        claims_payload.append({
            "id": str(claim.id),
            "ordinal": claim.ordinal,
            "text": claim.text,
            # None means the claim was extracted but never adjudicated, which is
            # different from an "Unverified" verdict and must render differently.
            "status": claim.status.value if claim.status else None,
            "is_adjudicated": claim.status is not None,
            # None means the model expressed no confidence. Not zero.
            "confidence": claim.confidence,
            "is_scored": claim.confidence is not None,
            "citation_indices": list(claim.citation_indices or []),
            "sources": sources,
            "unresolved_indices": unresolved,
            # Always null while no third-party fact-checker feed is connected, so
            # the UI cannot render a "reviewed by" badge.
            "reviewed_by": claim.reviewed_by,
            "review_url": claim.review_url,
        })

    return {
        "id": str(evt.id),
        "title": evt.title,
        "summary": evt.summary or "",
        "topic": evt.topic or "WORLD",
        "country": evt.country or "Unknown",
        "latitude": evt.latitude,
        "longitude": evt.longitude,
        "importance_score": evt.importance_score,
            "bias_lean": evt.bias_lean.name if evt.bias_lean else None,
            "is_classified": evt.bias_lean is not None,
            "status": evt.status,
        "created_at": evt.created_at.isoformat() if evt.created_at else None,
        "updated_at": evt.updated_at.isoformat() if evt.updated_at else None,
        "citation_sources": citation_sources,
        "claims": claims_payload,
        "claim_count": len(claims_payload),
        "contradiction_count": contradiction_signal["count"],
        "contradiction_max_confidence": contradiction_signal["max_confidence"],
        "contradiction_level": contradiction_signal["level"],
        "contradictions": contradictions,
        "transparency": transparency,
        "articles": articles_list
    }


@router.get(
    "/{event_uuid}/comparison",
    summary="Side-by-side outlet comparison for one event",
)
async def compare_sources(
    event_uuid: str = Path(...),
    db: AsyncSession = Depends(get_db),
    _read_guard: Optional[AuthIdentity] = Depends(
        require_public_read(KEY_SCOPE_READ_EVENTS)
    ),
):
    """
    How each publisher covering this event framed it, ordered along the bias axis.

    This is the data behind the "side-by-side comparison of the same event from
    different sources" requirement. It groups an event's articles by outlet and
    reports what each outlet actually published: its own bias rating (or null
    when unrated), credibility, and the claims it is the sole source for.

    Integrity notes:
      * An outlet with no bias classification appears in `unclassified_outlets`,
        not in the ordered spectrum, because it has no position on the axis.
      * `outlets` is only meaningful once the event has two or more independent
        publishers; `comparable` reports that, and for a single-source event the
        framing would be meaningless.
      * Headline and excerpt are the outlet's own words, never rewritten.
    """
    import uuid as _uuid

    try:
        event_id = _uuid.UUID(event_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event UUID format")

    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Event)
        .where(Event.id == event_id)
        .options(
            selectinload(Event.articles).selectinload(Article.source),
        )
    )
    evt = result.scalars().first()
    if evt is None:
        raise HTTPException(status_code=404, detail="Event not found")

    grouped: Dict[Any, List[Article]] = {}
    for art in evt.articles or []:
        if art.is_hidden:
            continue
        grouped.setdefault(art.source_id, []).append(art)

    outlets: List[Dict[str, Any]] = []
    unclassified: List[Dict[str, Any]] = []

    for source_id, arts in grouped.items():
        src = arts[0].source
        if src is None:
            continue
        lean = src.bias_lean.name if src.bias_lean else None
        entry = {
            "source_id": str(src.id),
            "source_name": src.name,
            "source_url": src.url,
            "country": src.country,
            "bias_lean": lean,
            "bias_score": _BIAS_SCALE.get(lean) if lean else None,
            "is_classified": lean is not None,
            "credibility_score": src.credibility_score,
            "article_count": len(arts),
            "headlines": [a.title for a in arts if a.title],
            "excerpts": [
                (a.content or "")[:280]
                for a in arts
                if a.content
            ],
            "article_ids": [str(a.id) for a in arts],
            "article_urls": [a.url for a in arts if a.url],
            "published_at": [
                a.published_at.isoformat() for a in arts if a.published_at
            ],
        }
        if lean is not None:
            outlets.append(entry)
        else:
            unclassified.append(entry)

    # Left-to-right along the axis, so the client can render the spectrum in
    # publication order rather than guessing a sort.
    outlets.sort(key=lambda e: (e["bias_score"], e["source_name"]))
    unclassified.sort(key=lambda e: e["source_name"])

    contradiction_signal = _contradiction_signal(evt.contradictions)

    return {
        "event_id": str(evt.id),
        "title": evt.title,
        # A side-by-side needs at least two independent publishers to compare.
        "comparable": len(outlets) >= 2,
        "outlet_count": len(grouped),
        "classified_outlet_count": len(outlets),
        "outlets": outlets,
        "unclassified_outlets": unclassified,
        "contradiction_count": contradiction_signal["count"],
        "contradiction_max_confidence": contradiction_signal["max_confidence"],
        "contradiction_level": contradiction_signal["level"],
        "contradictions": contradiction_signal["pairs"],
        "note": (
            "Outlets without a stored bias classification are returned separately "
            "because they have no position on the spectrum. They are not placed at "
            "centre."
        ),
    }

@router.get("/{event_uuid}/fact-check", summary="Run on-the-fly fact checking for an event's summary")
async def check_event_facts(
    event_uuid: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    # Fact-check verdicts are transparency data and are safe to render to any
    # visitor: the verdict is generated by the configured LLM provider, which
    # refuses loudly when unconfigured and never fabricates results. Anonymous
    # callers remain guarded by the request rate-limit middleware; signed-in
    # callers are additionally metered against their plan's fact-check quota.
    try:
        import uuid
        event_uuid_obj = uuid.UUID(event_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event UUID format")

    query = select(Event).where(Event.id == event_uuid_obj)
    result = await db.execute(query)
    evt = result.scalars().first()

    if not evt:
        raise HTTPException(status_code=404, detail="Event not found")

    if not evt.summary:
        raise HTTPException(status_code=400, detail="Event has no summary to fact-check")

    from src.services.llm_service import LLMService
    llm_service = LLMService()
    try:
        analysis = await llm_service.analyze_claim_credibility(evt.summary)
    except Exception as llm_err:
        # Matches the /fact-check contract: a provider failure is a 503 and is
        # never turned into a synthesised "credibility" result.
        raise HTTPException(
            status_code=503,
            detail="Claim analysis is temporarily unavailable; no result was produced."
        )

    # reviewed_by / review_url are only populated from a real, configured
    # external verification feed (the Google Fact Check Tools adapter). With no
    # provider configured the enrichment returns honest nulls and the status
    # reads "disabled"; a configured-but-failing provider reports "degraded"
    # and still yields nulls. The earlier version assigned reviewers
    # round-robin, which made the UI render "Reviewed by PolitiFact" on claims
    # no one had actually reviewed — that fabrication is structurally
    # impossible here because the only way in is a real API response.
    from src.services.factcheck_adapter import enrich_claims_with_reviews
    enrichment = await enrich_claims_with_reviews(analysis.claims)

    if current_user is not None:
        from src.services.entitlement_service import UsageService, METER_FACT_CHECK
        await UsageService(db).record(current_user.id, METER_FACT_CHECK)
        await db.commit()

    return {
        "credibility_score": analysis.credibility_score,
        "trust_risks": analysis.trust_risks,
        "independent_cross_references": analysis.independent_cross_references,
        "claims": enrichment["claims"],
        "external_verification": enrichment["status"],
        "historical_matches": [
            {
                "title": hm.title,
                "last_active": hm.last_active,
                "match_percentage": hm.match_percentage
            } for hm in analysis.historical_matches
        ],
        "summary": analysis.summary
    }



@router.get("/{event_id}/follow", summary="Whether the current user follows an event")
async def get_follow_status(
    event_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    evt_uuid = await _require_event(db, event_id)
    exists = (
        await db.execute(
            select(EventFollow.id).where(
                EventFollow.user_id == current_user.id,
                EventFollow.event_id == evt_uuid,
            )
        )
    ).first() is not None
    return {"event_id": str(evt_uuid), "following": exists}


@router.post("/{event_id}/follow", summary="Follow / subscribe to an event")
async def follow_event(
    event_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Opt in to follow a specific event (dossier).

    Idempotent: following an already-followed event returns changed=False and
    does not write a duplicate row (the unique constraint is the guard).
    """
    evt_uuid = await _require_event(db, event_id)
    existing = (
        await db.execute(
            select(EventFollow).where(
                EventFollow.user_id == current_user.id,
                EventFollow.event_id == evt_uuid,
            )
        )
    ).scalars().first()
    changed = existing is None
    if changed:
        db.add(EventFollow(user_id=current_user.id, event_id=evt_uuid))
        await db.commit()
    return {"event_id": str(evt_uuid), "following": True, "changed": changed}


@router.post("/{event_id}/view", summary="Record a view of an event (analytics)")
async def record_event_view(
    event_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    dwell_seconds: Optional[int] = Query(None, ge=0, description="Client-reported dwell time"),
):
    """Append one reading-habit row for this event by the current user.

    Fires from the event detail page on open (and optionally again on close
    with the client-measured dwell time). The analytics surface derives topic
    and bias-lean reading mix from these rows.
    """
    evt_uuid = await _require_event(db, event_id)
    from src.services.analytics_service import AnalyticsService

    await AnalyticsService(db).record_view(
        current_user.id, evt_uuid, dwell_seconds=dwell_seconds
    )
    return {"event_id": str(evt_uuid), "recorded": True}


@router.delete("/{event_id}/follow", summary="Unfollow an event")
async def unfollow_event(
    event_id: str = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    evt_uuid = await _require_event(db, event_id)
    result = await db.execute(
        EventFollow.__table__.delete().where(
            EventFollow.user_id == current_user.id,
            EventFollow.event_id == evt_uuid,
        )
    )
    changed = (result.rowcount or 0) > 0
    await db.commit()
    return {"event_id": str(evt_uuid), "following": False, "changed": changed}
