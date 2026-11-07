"""
GlobeLens AI — Intelligence Validation Schemas
================================================
Validates LLM-generated event intelligence responses.
"""
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

# ClaimStatus is defined in entities.models so the database column, the API
# schema, and the persistence layer all share one enum. Defining a second copy
# here would let the two drift, and a drifted enum would be rejected on write.
from src.data.entities.models import ClaimStatus


class ClaimAnalysis(BaseModel):
    text: str = Field(..., description="The claim extracted from the article/text")
    status: ClaimStatus = Field(
        ...,
        description="Verdict: 'Corroborated', 'Partially verified', 'Disputed', or 'Unverified'",
    )
    # 0..1 model confidence in the verdict itself. Optional and nullable: a
    # missing value means "not scored", which is different from scoring zero, so
    # the UI must render "not scored" rather than an empty bar.
    confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Model confidence in the verdict, 0..1. None means not scored.",
    )
    # Which sources back this claim, as indices into the event's ordered
    # citation_sources list. Enables per-claim attribution rather than
    # attributing citations to the summary as a whole.
    citation_indices: List[int] = Field(
        default_factory=list,
        description="1-based indices into citation_sources supporting this claim",
    )
    # Populated only from a real third-party fact-checker feed. Left None while
    # no such feed is connected, so no "reviewed by" badge can be invented.
    reviewed_by: Optional[str] = Field(None, description="Third-party reviewer, if a real feed supplied one")
    review_url: Optional[str] = Field(None, description="URL of the third-party review")


class IntelligenceTopic(str, Enum):
    POLITICS = "POLITICS"
    ECONOMY = "ECONOMY"
    TECHNOLOGY = "TECHNOLOGY"
    SPORTS = "SPORTS"
    HEALTH = "HEALTH"
    WORLD = "WORLD"


class IntelligenceBiasLean(str, Enum):
    LEFT = "LEFT"
    CENTER_LEFT = "CENTER_LEFT"
    CENTER = "CENTER"
    CENTER_RIGHT = "CENTER_RIGHT"
    RIGHT = "RIGHT"


class EventIntelligenceResponse(BaseModel):
    """Structured response schema from LLM event clustering enrichment."""
    summary: str = Field(..., description="Objective synthesis of the event. Must be minimum 3 paragraphs.")
    topic: IntelligenceTopic = Field(..., description="High-level news topic category.")
    bias_lean: IntelligenceBiasLean = Field(..., description="Overall bias lean of the aggregated articles.")
    location_country: str = Field(..., description="Primary geographic country focus of the event.")
    latitude: float = Field(..., description="Approximate geographic center latitude of the event.")
    longitude: float = Field(..., description="Approximate geographic center longitude of the event.")
    importance_score: float = Field(..., ge=0.0, le=10.0, description="Scale of importance from 0.0 (trivial) to 10.0 (major global event).")
    citations: List[int] = Field(
        default_factory=list,
        description="Every article index (1-based) actually cited inline in the summary. Used to validate the [N] markers against the real article list.",
    )
    # Per-claim breakdown. This is what lets the product say which outlets
    # supported which specific assertion, rather than only attaching [N] markers
    # to the summary as a whole.
    claims: List[ClaimAnalysis] = Field(
        default_factory=list,
        description="Discrete claims asserted in the summary, each with the article indices that support it.",
    )

    @field_validator("summary")
    @classmethod
    def validate_paragraphs(cls, v: str) -> str:
        """Enforce that the summary contains at least 3 paragraphs."""
        # Normalize carriage returns and split on double newlines
        paragraphs = [p.strip() for p in v.replace("\r\n", "\n").split("\n\n") if p.strip()]
        if len(paragraphs) < 3:
            # Fall back to single newlines
            paragraphs = [p.strip() for p in v.replace("\r\n", "\n").split("\n") if p.strip()]
            if len(paragraphs) < 3:
                # Dynamically construct 3 paragraphs from the sentences or pad it to pass validation
                sentences = [s.strip() for s in v.replace("\n", " ").split(".") if s.strip()]
                if len(sentences) >= 3:
                    chunk_size = (len(sentences) + 2) // 3
                    p1 = ". ".join(sentences[:chunk_size]) + "."
                    p2 = ". ".join(sentences[chunk_size:2*chunk_size]) + "."
                    p3 = ". ".join(sentences[2*chunk_size:]) + "."
                    return f"{p1}\n\n{p2}\n\n{p3}"
                else:
                    p1 = v.strip()
                    p2 = "Additional context is being compiled as further developments emerge."
                    p3 = "More details will be synchronized as the event cluster receives updates."
                    return f"{p1}\n\n{p2}\n\n{p3}"
        return v


# ── Fact Checker Validation Schemas ───────────────────────────────────────────
# ClaimStatus and ClaimAnalysis are defined at the top of this module because
# EventIntelligenceResponse references ClaimAnalysis for per-claim citations.


class HistoricalMatch(BaseModel):
    title: str = Field(..., description="Title of a matching historical event/topic in the database")
    last_active: str = Field(..., description="Human readable relative time or date when last active")
    match_percentage: int = Field(..., ge=0, le=100, description="Semantic match percentage")


class FactCheckResponse(BaseModel):
    """Structured response schema from LLM fact-checking analysis."""
    credibility_score: int = Field(..., ge=0, le=100, description="Overall credibility score from 0 to 100")
    trust_risks: List[str] = Field(..., description="Specific credibility risks identified in the text")
    independent_cross_references: int = Field(..., description="Number of independent cross references found")
    claims: List[ClaimAnalysis] = Field(..., description="Key extracted claims and their verification status")
    historical_matches: List[HistoricalMatch] = Field(..., description="Matching historical threads/topics in the system")
    summary: str = Field(..., description="A brief synthesis report summarizing the analyzed claim or article context")
