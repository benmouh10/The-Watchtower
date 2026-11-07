"""
GlobeLens AI — FactCheckController
===================================
POST /fact-check | GET /fact-check/{id} | GET /fact-check/users/{id}
"""
import json
import uuid
from fastapi import APIRouter, Path, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.core.database import get_db
from src.api.controllers.auth_controller import get_current_user, require_scopes
from src.data.entities.models import User, FactCheckRequest as FactCheckModel, FactCheckResult
from src.services.api_key_service import KEY_SCOPE_FACT_CHECK
from src.services.entitlement_service import (
    METER_FACT_CHECK,
    UsageService,
    require_action_quota,
)

router = APIRouter()

# A scraped page that yields less than this is treated as no usable content.
# The previous threshold of 30 characters was low enough that a navigation menu
# or cookie banner alone would pass, and the URL string would then be scored.
MIN_SCRAPED_CHARS = 200


class FactCheckRequest(BaseModel):
    input_text_url: str


@router.post("", status_code=status.HTTP_201_CREATED, summary="Submit text/URL for AI fact-checking")
async def submit_fact_check(
    payload: FactCheckRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_scopes(KEY_SCOPE_FACT_CHECK)),
    _quota_guard: None = Depends(require_action_quota(METER_FACT_CHECK)),
):
    from src.services.scraper_service import ScraperService
    from src.services.llm_service import LLMService

    input_text = payload.input_text_url.strip()
    if not input_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either text to analyse or a URL to fetch",
        )

    is_url = input_text.startswith("http://") or input_text.startswith("https://")
    scraped_content = ""

    # 1. If it's a URL, extract its main body text
    if is_url:
        try:
            scraper = ScraperService()
            scraped_content = await scraper.extract_full_content(input_text)
        except Exception as scrape_err:
            # A URL we could not read gives the model nothing to assess.
            # Scoring the URL string itself would produce a confident
            # credibility score out of nothing, which is worse than an error,
            # so this is reported rather than papered over.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Could not retrieve the content at that URL, so there is "
                    "nothing to fact-check. Paste the article text directly, "
                    "or try a different link."
                ),
            ) from scrape_err

        if len((scraped_content or "").strip()) < MIN_SCRAPED_CHARS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "That page returned too little text to analyse "
                    f"({len((scraped_content or '').strip())} characters, "
                    f"{MIN_SCRAPED_CHARS} required). It may be paywalled, "
                    "JavaScript-rendered, or not an article. Paste the text "
                    "directly instead."
                ),
            )

    # 2. Call LLM to evaluate claim credibility
    text_to_analyze = scraped_content if is_url else input_text

    llm_service = LLMService()
    try:
        analysis = await llm_service.analyze_claim_credibility(text_to_analyze)
    except Exception as llm_err:
        # Surfaced as 503 rather than an unhandled 500 so the client can tell
        # "our model is down" apart from "your input was rejected".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The verification model is temporarily unavailable. No result was recorded.",
        ) from llm_err
    
    # 3. Map credibility score to standard status enum
    score = analysis.credibility_score
    if score >= 70:
        result_enum = FactCheckResult.TRUE
    elif score <= 40:
        result_enum = FactCheckResult.FALSE
    else:
        result_enum = FactCheckResult.UNCERTAIN

    # Third-party fact-check attribution comes exclusively from a configured
    # external feed (Google Fact Check Tools). Disabled -> honest nulls;
    # configured but failing -> nulls plus status "degraded". See the adapter's
    # docstring for the refusal rules; a fabricated reviewer is impossible here.
    from src.services.factcheck_adapter import enrich_claims_with_reviews
    enrichment = await enrich_claims_with_reviews(analysis.claims)

    explanation_dict = {
        "credibility_score": score,
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
        "summary": analysis.summary,
        # How the assessed text was obtained, so a reader can tell whether the
        # model saw the full article or only the snippet the user pasted.
        "input_kind": "scraped_url" if is_url else "pasted_text",
        "analysed_characters": len(text_to_analyze.strip()),
    }

    # 4. Save in database
    new_request = FactCheckModel(
        input_text_url=input_text,
        result=result_enum,
        explanation=json.dumps(explanation_dict),
        user_id=current_user.id
    )
    db.add(new_request)
    await UsageService(db).record(current_user.id, METER_FACT_CHECK)
    await db.commit()
    await db.refresh(new_request)
    
    return {
        "id": str(new_request.id),
        "status": "completed",
        "input": input_text,
        "result": result_enum.value,
        "explanation": explanation_dict,
        "created_at": new_request.created_at.isoformat() if new_request.created_at else None
    }


@router.get("/{request_id}", summary="Get fact-check result by ID")
async def get_fact_check(
    request_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        req_uuid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid request UUID format")

    query = select(FactCheckModel).where(FactCheckModel.id == req_uuid)
    res = await db.execute(query)
    req = res.scalars().first()
    
    if not req:
        raise HTTPException(status_code=404, detail="Fact check request not found")
        
    # Safeguard user separation (unless admin)
    if current_user.role != "ADMIN" and current_user.id != req.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
        
    explanation_data = {}
    if req.explanation:
        try:
            explanation_data = json.loads(req.explanation)
        except Exception:
            explanation_data = {"raw": req.explanation}
            
    return {
        "id": str(req.id),
        "input": req.input_text_url,
        "result": req.result.value if req.result else None,
        "explanation": explanation_data,
        "created_at": req.created_at.isoformat() if req.created_at else None
    }


@router.get("/users/{user_id}", summary="Get all fact-check requests by user")
async def get_user_fact_checks(
    user_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        target_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user UUID format")

    # Safeguard user separation (unless admin)
    if current_user.role != "ADMIN" and current_user.id != target_uuid:
        raise HTTPException(status_code=403, detail="Access denied")
        
    query = (
        select(FactCheckModel)
        .where(FactCheckModel.user_id == target_uuid)
        .order_by(FactCheckModel.created_at.desc())
    )
    res = await db.execute(query)
    requests = res.scalars().all()
    
    response_list = []
    for req in requests:
        explanation_data = {}
        if req.explanation:
            try:
                explanation_data = json.loads(req.explanation)
            except Exception:
                explanation_data = {"raw": req.explanation}
                
        response_list.append({
            "id": str(req.id),
            "input": req.input_text_url,
            "result": req.result.value if req.result else None,
            "explanation": explanation_data,
            "created_at": req.created_at.isoformat() if req.created_at else None
        })
        
    return {"user_id": user_id, "requests": response_list}
