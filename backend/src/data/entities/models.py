"""GlobeLens AI â€” Domain Entities (SQLAlchemy ORM Models)"""
import enum
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, func, text
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# â”€â”€ Base â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class Base(DeclarativeBase):
    pass


# â”€â”€ Enumerations (matching blueprint spec) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class ProcessingStatus(str, enum.Enum):
    SCRAPED   = "SCRAPED"
    EMBEDDED  = "EMBEDDED"
    CLUSTERED = "CLUSTERED"
    PROCESSED = "PROCESSED"


class UserRole(str, enum.Enum):
    GUEST      = "GUEST"
    AUTH_USER  = "AUTH_USER"
    JOURNALIST = "JOURNALIST"
    ADMIN      = "ADMIN"


class PlanTier(str, enum.Enum):
    """
    Account plan, distinct from role. Role decides *what an account may do in
    the product* (admin/journalist verbs); plan decides *how much* (usage
    quotas). Every account registers as FREE, and a plan can only be changed by
    an administrator: no payment provider is wired, so there is no self-serve
    purchase path. Assigning PRO or ENTERPRISE is an operational decision, and
    every such move is written to the plan_changes audit log.
    """
    FREE       = "FREE"
    PRO        = "PRO"
    ENTERPRISE = "ENTERPRISE"


class FactCheckResult(str, enum.Enum):
    TRUE      = "TRUE"
    FALSE     = "FALSE"
    UNCERTAIN = "UNCERTAIN"


class ClaimStatus(str, enum.Enum):
    """
    Closed set of claim verdicts, stored as a native enum so a value outside this
    set is rejected by the database rather than only by the API layer. These
    strings are rendered as review badges, so an unexpected value would be shown
    to readers as if it were a real verdict.

    PARTIALLY_VERIFIED exists so a claim backed by some but not all sources has
    an honest state. Without it, such a claim had to be recorded as either
    Corroborated or Unverified, overstating the evidence either way.
    """
    CORROBORATED        = "Corroborated"
    PARTIALLY_VERIFIED  = "Partially verified"
    DISPUTED            = "Disputed"
    UNVERIFIED          = "Unverified"


class BiasLean(str, enum.Enum):
    LEFT         = "LEFT"
    CENTER_LEFT  = "CENTER_LEFT"
    CENTER       = "CENTER"
    CENTER_RIGHT = "CENTER_RIGHT"
    RIGHT        = "RIGHT"


# â”€â”€ Source â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class Source(Base):
    """Models media publishers (BBC, CNN, Al Jazeera, etc.)."""
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    name: Mapped[str]               = mapped_column(String(255), nullable=False, unique=True)
    url: Mapped[str]                = mapped_column(String(500), nullable=False)
    country: Mapped[Optional[str]]  = mapped_column(String(100))
    # None means "not assessed". A 0.5 default rendered as "50% trust", which is
    # a rating nobody made; the event page shows "Not rated" for null instead.
    credibility_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    bias_lean: Mapped[Optional[BiasLean]] = mapped_column(Enum(BiasLean), nullable=True)
    # Enterprise custom feeds: a source registered by an organization (not part
    # of the built-in catalogue) carries the owning organization id and the
    # is_custom flag so the catalogue view can keep them separate.
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True,
    )
    is_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    created_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())

    articles: Mapped[List["Article"]] = relationship("Article", back_populates="source")


# â”€â”€ User â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class User(Base):
    """Authentication identity and user preferences."""
    __tablename__ = "users"
    # The unique constraint keeps its database-given name. Declaring it here
    # (rather than unique=True+index=True on the column, which would mint a
    # *unique index* named ix_users_email) is what matches the applied schema:
    # the DB has a UNIQUE constraint here, not a unique index. The redundant
    # non-unique index Alembic once refused to see is dropped by migration.
    __table_args__ = (
        UniqueConstraint("email", name="users_email_key"),
    )

    id: Mapped[uuid.UUID]             = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    name: Mapped[str]                 = mapped_column(String(255), nullable=False)
    email: Mapped[str]                = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str]        = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole]            = mapped_column(
        Enum(UserRole), default=UserRole.AUTH_USER, server_default="AUTH_USER"
    )
    plan: Mapped[PlanTier]            = mapped_column(
        Enum(PlanTier), default=PlanTier.FREE, server_default="FREE"
    )
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    is_blocked: Mapped[bool]          = mapped_column(Boolean, default=False, server_default=text("false"))
    preferred_topics: Mapped[Optional[List[str]]]    = mapped_column(ARRAY(String), default=list)
    preferred_countries: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), default=list)
    created_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now())

    comments: Mapped[List["Comment"]]           = relationship("Comment", back_populates="user", cascade="all, delete-orphan")
    comment_votes: Mapped[List["CommentVote"]]   = relationship("CommentVote", back_populates="user", cascade="all, delete-orphan")
    fact_check_requests: Mapped[List["FactCheckRequest"]] = relationship("FactCheckRequest", back_populates="user", cascade="all, delete-orphan")
    bookmarks: Mapped[List["Bookmark"]]          = relationship("Bookmark", back_populates="user", cascade="all, delete-orphan")
    reading_lists: Mapped[List["ReadingList"]]   = relationship("ReadingList", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens: Mapped[List["RefreshToken"]] = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    api_keys: Mapped[List["ApiKey"]]             = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    push_subscriptions: Mapped[List["PushSubscription"]] = relationship("PushSubscription", back_populates="user", cascade="all, delete-orphan")
    event_follows: Mapped[List["EventFollow"]]   = relationship("EventFollow", back_populates="user", cascade="all, delete-orphan")
    expert_verifications: Mapped[List["ExpertVerification"]] = relationship(
        "ExpertVerification", back_populates="user",
        foreign_keys="ExpertVerification.user_id", cascade="all, delete-orphan",
    )


# â”€â”€ RefreshToken â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class RefreshToken(Base):
    """
    Server-side record of an issued refresh token, so rotation can actually
    revoke something.

    Previously refresh tokens were pure JWTs with no server-side state, which
    made "rotation" cosmetic: the previous token stayed valid for its full
    seven days, and logout could not end a session. Storing a row per token
    turns both into real operations.

    Security model:
      * Only the SHA-256 of the token is stored. A database leak therefore does
        not hand an attacker usable session tokens.
      * SHA-256 (not bcrypt/argon2) is the correct primitive here, unlike for
        passwords. The input is a 384-bit server-generated random string, so
        there is no small search space for a slow KDF to defend against, and a
        slow hash would add latency to the hottest auth path.
      * `family_id` groups one login's rotation chain. Presenting an
        already-rotated token means either a replay or a stolen token, and both
        are handled by revoking the whole family: a thief holding a descendant
        token cannot keep the session alive, and the legitimate user's next
        refresh fails loudly instead of silently losing the session.
    """
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_family_id", "family_id"),
        # Serves the "revoke everything still live in this family" sweep.
        Index("ix_refresh_tokens_user_active", "user_id", "revoked_at"),
        # Kept in sync with the migration that introduced the table so pg_dump
        # and pg_description match the model (and autogenerate sees no diff).
        {"comment": "Server-side record of issued refresh tokens. Stores only "
         "a SHA-256 digest, never the token. family_id groups one login "
         "rotation chain; revoked_at non-null means spent or revoked, and "
         "replaying such a token revokes the whole family."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    # Hex SHA-256 of the raw token. Unique, and the lookup path for every
    # refresh, so it must be indexed; the unique constraint provides that.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # Stable across a rotation chain. All tokens descended from one login share
    # it, which is what makes reuse detection able to revoke the whole chain.
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # The JWT's own `jti`. Kept so an incident can be traced from a leaked token
    # back to this row without re-hashing.
    jti: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    issued_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Non-null once the token is spent or revoked. A revoked token that is
    # presented again is treated as reuse, not as an unknown token.
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set on the row that this one replaced, forming the rotation chain.
    replaced_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )

    # Recorded so a user can see where a session came from, and so an unusual
    # location on refresh is detectable. Nullable: API clients send neither.
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.now(timezone.utc)


# â”€â”€ ApiKey â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class ApiKey(Base):
    """
    A long-lived bearer credential for machine/scripted use, stored hashed.

    Same threat model as refresh tokens: only the SHA-256 hex is ever kept, and
    SHA-256 (not a slow KDF) is correct because the raw key is a high-entropy
    server-generated secret with no small search space. The raw key is shown
    exactly once, at creation; ``key_prefix`` (the ``glb_`` prefix plus eight
    hex characters) lets an owner recognise a key in lists without revealing
    it.

    ``scopes`` bounds what the key may do. Scope gates only apply to api-key
    principals: an access-token session (a signed JWT for the user) always has
    the account's full rights, so existing endpoints are unaffected.
    """
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_user_id", "user_id"),
        Index("ix_api_keys_created_at", "created_at"),
        {"comment": "Long-lived bearer API keys. Only SHA-256 hex is stored; "
         "key_prefix is the display prefix, scopes bound the credential, and "
         "revoked_at non-null disables the key."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Hex SHA-256 of the raw key. Unique, and the lookup path for every
    # authenticated request, so the unique constraint provides the index.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[List[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="api_keys")


# â”€â”€ Event â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class Event(Base):
    """High-level news event aggregating multiple articles."""
    __tablename__ = "events"

    id: Mapped[uuid.UUID]            = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    title: Mapped[str]               = mapped_column(String(500), nullable=False)
    summary: Mapped[Optional[str]]   = mapped_column(Text)
    topic: Mapped[Optional[str]]     = mapped_column(String(255), index=True)
    country: Mapped[Optional[str]]   = mapped_column(String(100), index=True)
    latitude: Mapped[Optional[float]]  = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    importance_score: Mapped[float]  = mapped_column(Float, default=0.0, server_default="0")
    is_promoted: Mapped[bool]        = mapped_column(Boolean, default=False, server_default=text("false"))
    bias_lean: Mapped[Optional[BiasLean]] = mapped_column(Enum(BiasLean), nullable=True)
    status: Mapped[str]              = mapped_column(String(50), default="DRAFT", server_default="DRAFT", index=True)
    citation_sources: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True,
        comment="Ordered list of article UUIDs exactly as presented to the LLM at synthesis time. Index N-1 is the target of the summary's [N] inline citation.",
    )
    contradictions: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True,
        comment="Structured A-vs-B contradiction pairs detected across the cluster's sources.",
    )
    created_at: Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    articles: Mapped[List["Article"]] = relationship(
        "Article",
        back_populates="event",
        order_by="(Article.created_at, Article.id)",
    )
    comments: Mapped[List["Comment"]] = relationship("Comment", back_populates="event", cascade="all, delete-orphan")
    bookmarks: Mapped[List["Bookmark"]] = relationship("Bookmark", back_populates="event", cascade="all, delete-orphan")
    reading_list_items: Mapped[List["ReadingListItem"]] = relationship(
        "ReadingListItem", back_populates="event", cascade="all, delete-orphan"
    )
    # Per-claim rows backing the "which sources contributed to this claim" view.
    # ordered_list makes the ordinal ordering explicit rather than relying on
    # unspecified row order.
    #
    # selectin loading is required, not an optimisation: this relationship is
    # read while building the async event-detail response. A lazy load there
    # would emit IO from a sync attribute access and raise MissingGreenlet
    # instead of returning the claims.
    claims: Mapped[List["EventClaim"]] = relationship(
        "EventClaim",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventClaim.ordinal",
        lazy="selectin",
    )


# â”€â”€ Article â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class Article(Base):
    """Foundational content unit â€” scraped from media publishers."""
    __tablename__ = "articles"

    id: Mapped[uuid.UUID]             = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    title: Mapped[str]                = mapped_column(String(500), nullable=False)
    content: Mapped[Optional[str]]    = mapped_column(Text)
    url: Mapped[str]                  = mapped_column(String(2000), nullable=False, unique=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Multi-modal media attached at ingest time. image_url is the address the
    # source actually published for this story (og:image from the hydrated page,
    # or the feed's own thumbnail), resolved honestly: a data: URI, a relative
    # reference or a placeholder is never stored as an image.
    image_url: Mapped[Optional[str]]      = mapped_column(
        String(2000),
        comment=("Image URL the source published for this story (og:image from "
                 "the hydrated page, or the feed thumbnail). Null when the "
                 "article was ingested without one."),
    )
    # Alt/caption text the source attached to that image (og:image:alt, feed
    # caption) â€” never a description we invented.
    image_caption: Mapped[Optional[str]]  = mapped_column(
        Text,
        comment=("Alt/caption text the source attached to that image "
                 "(og:image:alt). Null when the source gave none."),
    )
    # What kind of media was stored: "image" when image_url is present, null
    # when the article was ingested without one.
    media_type: Mapped[Optional[str]]     = mapped_column(
        String(20),
        comment=('Kind of media stored: "image" when image_url is present, '
                 'null when none was captured.'),
    )
    # AI-generated caption written by the vision adapter (config-gated), kept
    # separate from the source's own alt text above so a machine description is
    # never mistaken for what the publisher wrote. model records which vision
    # model produced it; _at is when it was written.
    image_ai_caption: Mapped[Optional[str]] = mapped_column(
        Text,
        comment=("AI caption generated by the config-gated vision adapter. "
                 "Null until an operator enables VISION_PROVIDER and the "
                 "caption runs."),
    )
    image_ai_caption_model: Mapped[Optional[str]] = mapped_column(
        String(200),
        comment=("Name of the vision model that produced image_ai_caption."),
    )
    image_ai_caption_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        comment=("Timestamp the AI caption was written."),
    )
    # Video path (roadmap #10): video_url is the og:video/twitter:player address
    # the publisher declared for this story (null when the page is text-only).
    # video_transcript and video_summary are the produced artifacts — the
    # transcript comes from the video provider, the summary from the LLM.
    video_url: Mapped[Optional[str]] = mapped_column(
        String(2000),
        comment=("Video URL the source published for this story (og:video or "
                 "twitter:player). Null when the article is text-only."),
    )
    video_transcript: Mapped[Optional[str]] = mapped_column(
        Text,
        comment=("Transcript of the article's video, produced by the config-"
                 "gated video provider. Null until enrichment runs."),
    )
    video_summary: Mapped[Optional[str]] = mapped_column(
        Text,
        comment=("LLM summary of the video transcript. Null until enrichment "
                 "runs."),
    )
    video_summary_model: Mapped[Optional[str]] = mapped_column(
        String(200),
        comment=("Name of the LLM that produced video_summary."),
    )
    video_processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        comment=("Timestamp the video transcript/summary was written."),
    )
    created_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now())
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus), default=ProcessingStatus.SCRAPED,
        server_default="SCRAPED", index=True,
    )
    is_hidden: Mapped[bool]           = mapped_column(Boolean, default=False, server_default=text("false"))
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    event_id: Mapped[Optional[uuid.UUID]]  = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), index=True
    )

    source: Mapped[Optional["Source"]]  = relationship("Source", back_populates="articles")
    event: Mapped[Optional["Event"]]    = relationship("Event", back_populates="articles")
    embedding: Mapped[Optional["Embedding"]] = relationship("Embedding", back_populates="article", uselist=False)


# â”€â”€ Embedding â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class Embedding(Base):
    """Vector embedding storage â€” decoupled from Article for perf."""
    __tablename__ = "embeddings"
    __table_args__ = (
        # Declared with its database name. unique=True,index=True on the column
        # would mint a *unique index* named ix_embeddings_article_id; the schema
        # actually has a UNIQUE constraint here, and autogenerate used that
        # mismatch to keep proposing a drop of the vector search index too.
        UniqueConstraint("article_id", name="embeddings_article_id_key"),
        # The pgvector ANN index. If this disappears from the model, autogenerate
        # feels free to DROP ix_embeddings_vector_ivfflat, silently turning
        # nearest-neighbour search into a full scan. It is declared here on
        # purpose, and with the same options the database was created with.
        Index(
            "ix_embeddings_vector_ivfflat",
            "vector",
            postgresql_using="ivfflat",
            postgresql_ops={"vector": "vector_cosine_ops"},
            postgresql_with={"lists": "100"},
        ),
    )

    id: Mapped[uuid.UUID]      = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    # 1536 dimensions for text-embedding-3-small
    vector: Mapped[list]       = mapped_column(Vector(1536))
    model: Mapped[str]         = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE")
    )

    article: Mapped["Article"] = relationship("Article", back_populates="embedding")


# â”€â”€ Comment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class Comment(Base):
    """User community comments on Events. Supports one level of threading via parent_id."""
    __tablename__ = "comments"

    id: Mapped[uuid.UUID]      = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    content: Mapped[str]       = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now())
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comments.id", ondelete="CASCADE"), index=True
    )
    is_hidden: Mapped[bool]    = mapped_column(Boolean, default=False, server_default=text("false"))

    user:  Mapped["User"]  = relationship("User",  back_populates="comments")
    event: Mapped["Event"] = relationship("Event", back_populates="comments")
    parent: Mapped[Optional["Comment"]] = relationship("Comment", remote_side=[id], back_populates="replies")
    replies: Mapped[List["Comment"]] = relationship("Comment", back_populates="parent")
    votes: Mapped[List["CommentVote"]] = relationship(
        "CommentVote", back_populates="comment", cascade="all, delete-orphan"
    )


# â”€â”€ CommentVote â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class CommentVote(Base):
    """
    A single user's vote on a single comment.

    The unique constraint on (comment_id, user_id) is what prevents vote
    inflation â€” a user cannot upvote the same comment twice, and switching
    direction updates the existing row rather than adding another.
    """
    __tablename__ = "comment_votes"
    __table_args__ = (
        UniqueConstraint("comment_id", "user_id", name="uq_comment_vote_user"),
        CheckConstraint("value IN (-1, 1)", name="ck_comment_vote_value"),
    )

    id: Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    value: Mapped[int]         = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comments.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)

    comment: Mapped["Comment"] = relationship("Comment", back_populates="votes")
    user: Mapped["User"] = relationship("User", back_populates="comment_votes")


# â”€â”€ Bookmark â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class Bookmark(Base):
    """
    A user saving an event to their own dossier for later reading.

    The unique constraint on (user_id, event_id) makes saving idempotent: a
    repeated save updates nothing instead of creating a duplicate row, so the
    toggle endpoint can be retried safely without inflating the list.
    """
    __tablename__ = "bookmarks"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_bookmark_user_event"),
    )

    id: Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), index=True)

    user: Mapped["User"]  = relationship("User", back_populates="bookmarks")
    event: Mapped["Event"] = relationship("Event", back_populates="bookmarks")


# â”€â”€ ReadingList â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class ReadingList(Base):
    """
    A named, user-owned collection of events ("reading list").

    A user may not own two lists with the same title, and lists are private to
    their owner. Membership is ordered: each item carries a per-list position,
    so the order the reader arranges is the order the UI renders (and the order
    a print/PDF export follows).
    """
    __tablename__ = "reading_lists"
    __table_args__ = (
        UniqueConstraint("user_id", "title", name="uq_reading_list_user_title"),
        {"comment": "Named reading lists owned by a user. user_id+title is "
         "unique so a user cannot accumulate two identically-labelled lists."},
    )

    id: Mapped[uuid.UUID]      = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    title: Mapped[str]         = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_private: Mapped[bool]   = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )

    user: Mapped["User"] = relationship("User", back_populates="reading_lists")
    items: Mapped[List["ReadingListItem"]] = relationship(
        "ReadingListItem", back_populates="reading_list",
        cascade="all, delete-orphan", order_by="ReadingListItem.position",
    )


# â”€â”€ ReadingListItem â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class ReadingListItem(Base):
    """
    One event inside one reading list, with its user-arranged position.

    Position is 0-based and unique within a list (a 1-based ordinal would make
    "element 0" impossible and confuse reordering code). The position unique
    constraint doubles as the anti-duplicate guard for the intended order; the
    (list_id, event_id) constraint separately guarantees the same event cannot
    appear twice no matter what position dance a client attempts.
    """
    __tablename__ = "reading_list_items"
    __table_args__ = (
        UniqueConstraint("list_id", "position", name="uq_reading_list_item_position"),
        UniqueConstraint("list_id", "event_id", name="uq_reading_list_item_event"),
        CheckConstraint("position >= 0", name="ck_reading_list_item_position"),
        {"comment": "An event inside a reading list at a user-arranged 0-based "
         "position. list_id+position unique, and list_id+event_id unique, so "
         "ordering is stable and a duplicate add is impossible."},
    )

    id: Mapped[uuid.UUID]      = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    position: Mapped[int]      = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reading_lists.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )

    reading_list: Mapped["ReadingList"] = relationship("ReadingList", back_populates="items")
    event: Mapped["Event"] = relationship("Event", back_populates="reading_list_items")


# â”€â”€ UsageEvent â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class UsageEvent(Base):
    """
    One metered action by one account (a fact-check, a chatbot message, an
    export). An immutable append-only log: the row records what happened; quota
    and billing decisions are derived from it at read time, never written back.

    `user_id` is nullable and set NULL (not deleted) when an account is removed,
    so historical spend survives account deletion without being attributable.
    `quantity` exists so one action can record N units (e.g. a doctor-level
    spend report counting documents) while most rows stay at 1.
    """
    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_user_id", "user_id"),
        Index("ix_usage_events_created_at", "created_at"),
        {"comment": "Append-only log of metered per-account actions. user_id is "
         "NULL after account deletion so history is kept anonymized; quota "
         "decisions are derived from this log at read time."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    meter: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    meta: Mapped[Optional[dict]] = mapped_column(JSONB)


# â”€â”€ PlanChange â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class PlanChange(Base):
    """
    One audited plan assignment for one account.

    This table exists so a move to PRO or ENTERPRISE is a real, recorded
    operation rather than a silent column edit. It is append-only, mirroring
    usage_events: `from_plan` is NULL for the opening row written at
    registration (the account was created onto `to_plan`), and every later row
    records the previous tier, the target tier, and the admin who made the
    change. `changed_by` is NULL for that initial registration and for any
    future self-serve path.

    Both `user_id` and `changed_by` are SET NULL (not cascaded) on account
    deletion, so the log keeps a truthful, anonymized history after cleanup.
    """
    __tablename__ = "plan_changes"
    __table_args__ = (
        Index("ix_plan_changes_user_id", "user_id"),
        Index("ix_plan_changes_changed_by", "changed_by"),
        Index("ix_plan_changes_created_at", "created_at"),
        {"comment": "Append-only log of plan assignments. from_plan NULL means "
         "the account was created onto to_plan; later rows record the previous "
         "tier, the target tier, and the admin who made the change. user_id and "
         "changed_by are NULL after account deletion, so history survives "
         "anonymized."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_plan_changes_user_id_users"),
    )
    changed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_plan_changes_changed_by_users"),
    )
    from_plan: Mapped[Optional[PlanTier]] = mapped_column(Enum(PlanTier), nullable=True)
    to_plan: Mapped[PlanTier] = mapped_column(Enum(PlanTier), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# â”€â”€ FactCheckRequest â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class FactCheckRequest(Base):
    """User-submitted external verification requests."""
    __tablename__ = "fact_check_requests"

    id: Mapped[uuid.UUID]          = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    input_text_url: Mapped[str]    = mapped_column(Text, nullable=False)
    result: Mapped[Optional[FactCheckResult]] = mapped_column(Enum(FactCheckResult))
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID]     = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    user: Mapped["User"] = relationship("User", back_populates="fact_check_requests")


# â”€â”€ EventClaim â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class EventClaim(Base):
    """
    One extracted claim from an event's synthesis, with the sources that back it.

    This is what makes "show which sources contributed to each claim" answerable
    per claim. Previously citations existed only as [N] markers inside the
    summary text, which attributed sources to the document as a whole and could
    not say which outlet supported which sentence.

    citation_indices holds 1-based positions into the owning event's ordered
    `citation_sources` list, matching the [N] markers in the summary, so a claim
    and the inline markers always resolve against the same ordering.

    Every judgement column is nullable on purpose:
      * confidence None  = not scored (distinct from a score of 0)
      * reviewed_by None = no third-party fact-checker feed is connected, so no
        "reviewed by" badge may be rendered
    """
    __tablename__ = "event_claims"
    # Indexes are declared here (not via index=True on the column) so the model
    # matches the migration 1:1. Both a name-collision and a divergence between
    # the model's metadata and the applied schema would otherwise surface later
    # as an autogen-diff surprise. event_id backs the per-event load; the
    # compound (event_id, ordinal) index serves the ordered read.
    __table_args__ = (
        Index("ix_event_claims_event_id", "event_id"),
        Index("ix_event_claims_event_ordinal", "event_id", "ordinal"),
        # The database carries the same explanation; keeping it in the model is
        # what stops autogenerate from proposing a comment drop on every run.
        {"comment": "Per-claim citations and confidence. Rows are produced by "
         "claim extraction only; NULL status/confidence mean not adjudicated/"
         "not scored."},
    )

    id: Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"))

    text: Mapped[str]           = mapped_column(Text, nullable=False)
    # Ordinal position of this claim within its event, so the UI can present
    # claims in the order the synthesis makes them.
    ordinal: Mapped[int]        = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # values_callable is required, not cosmetic.
    #
    # By default SQLAlchemy stores an Enum by its member NAME ("CORROBORATED"),
    # but the migration created the database enum from the member VALUES
    # ("Corroborated", with a space). Without this, every write fails with
    # "invalid input value for enum claimstatus". It also pins the API wire
    # format to the human-readable label.
    status: Mapped[Optional[ClaimStatus]] = mapped_column(
        Enum(ClaimStatus, values_callable=lambda enum_cls: [m.value for m in enum_cls]),
        nullable=True,
    )
    # 0..1. None means the model did not score this claim.
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # 1-based indices into the event's citation_sources ordering.
    citation_indices: Mapped[List[int]]  = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    # Only ever set from a real external review. Never invented.
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    review_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    event: Mapped["Event"] = relationship("Event", back_populates="claims")


# â”€â”€ Digest â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class Digest(Base):
    """
    A completed scheduled comprehensive brief ("digest") run.

    The scheduled engine ranks PROCESSED events inside a window by importance,
    renders a single self-contained HTML document, and persists it here exactly
    once. `event_ids` records coverage facts (what was in the brief, and in what
    window) so readers can audit what a digest did and did not include without
    trusting a re-render. Delivery (email/push) is deliberately absent: those
    are separate roadmap items that consume this artifact.
    """
    __tablename__ = "digests"
    __table_args__ = (
        Index("ix_digests_created_at", "created_at"),
        {"comment": "Scheduled comprehensive briefs. Each row is one completed "
         "run: the persisted self-contained HTML plus the exact events and time "
         "window it covered, for auditability."},
    )

    id: Mapped[uuid.UUID]       = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    title: Mapped[str]          = mapped_column(String(200), nullable=False)
    range_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[Optional[datetime]]   = mapped_column(DateTime(timezone=True))
    event_ids: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True,
        comment="UUIDs of the events covered by this brief, in rendered order.",
    )
    event_count: Mapped[int]    = mapped_column(Integer, nullable=False, default=0, server_default="0")
    content_html: Mapped[str]   = mapped_column(Text, nullable=False)
    # daily (the scheduled comprehensive brief) or weekly (the Sunday deep-dive).
    # Delivery targeting keys off this so weekly-only readers are not emailed a
    # daily brief, and push/email queues agree on what they are announcing.
    frequency: Mapped[str]      = mapped_column(
        String(16), nullable=False, default="daily", server_default="daily",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# â”€â”€ NewsletterSubscription â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class NewsletterSubscription(Base):
    """
    One active (or explicitly ended) digest subscription per user.

    A user has at most one row (unique user_id). unsubscribed_at is NULL while
    subscribed; unsubscribing sets it rather than deleting the row so the audit
    trail can show a subscription existed, and resubscribing simply clears the
    timestamp instead of minting a second row.
    """
    __tablename__ = "newsletter_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_newsletter_subscriptions_user_id"),
        # Queries filter subscribers by digest_frequency when assembling a
        # daily/weekly batch, so the scheduler's index is declared here to
        # match the migration that created it.
        Index("ix_newsletter_subscriptions_frequency", "digest_frequency"),
        {"comment": "Digest email subscriptions. One row per user; "
         "unsubscribed_at non-null marks an ended subscription; the row is "
         "kept for audit and the timestamp is cleared on a resubscribe."},
    )

    id: Mapped[uuid.UUID]      = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    # daily | weekly | off. "daily" gets the daily brief; "weekly" gets only the
    # Sunday deep-dive; "off" opts out of email while keeping preferences.
    digest_frequency: Mapped[str] = mapped_column(
        String(16), nullable=False, default="daily", server_default="daily",
    )
    # Newsletter-specific topic interests. When set, the daily/weekly brief is
    # only delivered if it covers at least one of these topics (mirrors the
    # user-level preference but scoped to the newsletter itself).
    topics: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), default=list)
    # Local-offset hour (0-23) the reader wants their brief; stored in UTC in
    # the deliveries. enqueue_digest stamps scheduled_for to the next
    # occurrence of this hour so the email arrives when the reader asked.
    preferred_digest_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, default=6, server_default="6",
    )
    # Stamped on every successful (sent/logged) delivery so the re-engagement
    # logic can tell how long a subscriber has been quiet.
    last_email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship("User")


# â”€â”€ NewsletterDelivery â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class NewsletterDelivery(Base):
    """
    One newsletter send attempt, or queued send, for one digest to one user.

    Unique on (digest_id, user_id), so a digest can never email a subscriber
    twice no matter how many times it is enqueued. delivery_status starts
    'pending', becomes 'sent' (real SMTP delivery), 'logged' (EMAIL_ENABLED is
    false and the message was captured to the log instead of emailed), or
    'failed' (an SMTP error; error_text keeps the reason for operators).
    """
    __tablename__ = "newsletter_deliveries"
    __table_args__ = (
        UniqueConstraint("digest_id", "user_id", name="uq_newsletter_delivery_digest_user"),
        Index("ix_newsletter_deliveries_status", "delivery_status"),
        Index("ix_newsletter_deliveries_scheduled_for", "scheduled_for"),
        {"comment": "Digest newsletter sends. Unique (digest_id, user_id) so a "
         "digest never emails a subscriber twice; status traces the send "
         "attempt (pending/sent/logged/failed) with error_text for failures. "
         "scheduled_for defers delivery to each subscriber's preferred hour."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    digest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("digests.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending",
    )
    error_text: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    digest: Mapped[Optional[Digest]] = relationship("Digest")
    user: Mapped[Optional[User]] = relationship("User")


# â”€â”€ PushSubscription â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class PushSubscription(Base):
    """
    One Web Push device subscription (a browser) belonging to one user.

    `endpoint` is the browser's push-service inbox URL and is unique â€” the same
    device cannot be registered twice. `p256dh`/`auth` are the encryption keys a
    push service returns with the subscription; they are stored only so the app
    can address that browser. Deleting a user cascades to their subscriptions.
    """
    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
        Index("ix_push_subscriptions_user_id", "user_id"),
        {"comment": "Web Push device subscriptions. One row per browser "
         "(unique endpoint); p256dh/auth are the push-service encryption keys "
         "the app uses to address that device."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[Optional[User]] = relationship("User", back_populates="push_subscriptions")


# â”€â”€ EventFollow â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class EventFollow(Base):
    """
    One user's opt-in to follow a specific event (a dossier).

    Following is how the notification/alert surface targets individual stories:
    the row exists only because the user asked for it, and it is cheap to read
    ("which users follow this event") without scanning preference arrays.
    Deletion of either side cascades the follow away.
    """
    __tablename__ = "event_follows"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_event_follows_user_event"),
        Index("ix_event_follows_user_id", "user_id"),
        Index("ix_event_follows_event_id", "event_id"),
        {"comment": "Opt-in follows of specific events by users. Unique "
         "(user_id, event_id) so a user follows a dossier at most once; "
         "cascades with a deleted user or event."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="event_follows")
    event: Mapped["Event"] = relationship("Event")


# â”€â”€ PushDelivery â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class PushDelivery(Base):
    """
    One queued or attempted digest push to one device subscription.

    Unique on (digest_id, subscription_id), mirroring NewsletterDelivery, so a
    digest never notifies the same browser twice. delivery_status traces the
    attempt: 'pending', 'sent' (real VAPID relay), 'logged' (PUSH_ENABLED off:
    captured to the log, never claimed as sent), or 'failed' (relay error;
    error_text keeps the reason).
    """
    __tablename__ = "push_deliveries"
    __table_args__ = (
        UniqueConstraint("digest_id", "subscription_id", name="uq_push_delivery_digest_sub"),
        # Follower-event pushes carry event_id (digest_id is NULL); the partial
        # unique index makes "one push per followed event per device" as stable
        # as the digest constraint above. A bare UNIQUE(event_id, subscription_id)
        # would treat digest rows' NULL event_id as distinct anyway, but the
        # WHERE keeps the index smaller and at-most-one guarantees crisp.
        Index(
            "uq_push_delivery_event_sub",
            "event_id",
            "subscription_id",
            unique=True,
            postgresql_where=text("event_id IS NOT NULL"),
        ),
        Index("ix_push_deliveries_status", "delivery_status"),
        {"comment": "Push sends — digest or followed-event. Digest rows unique "
         "(digest_id, subscription_id); event rows unique (event_id, "
         "subscription_id) via partial index; status traces the attempt "
         "(pending/sent/logged/failed) with error_text for failures."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    digest_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("digests.id", ondelete="CASCADE"), nullable=True,
    )
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=True,
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("push_subscriptions.id", ondelete="CASCADE"), nullable=False,
    )
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending",
    )
    error_text: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    digest: Mapped[Optional[Digest]] = relationship("Digest")
    event: Mapped[Optional[Event]] = relationship("Event")
    subscription: Mapped[Optional[PushSubscription]] = relationship("PushSubscription")


# â”€â”€ ExpertVerification â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class ExpertVerification(Base):
    """
    Admin-granted expert badge on a user account.

    The comment system derives its JOURNALIST/ADMIN badge from the role column,
    which only an administrator can set. This table adds a *verification*
    workflow on top: an administrator marks a user as a verified expert with a
    title (e.g. "Verified economist"), which the comment serializer renders as
    a distinct "ExpertÂ·" badge. A row is active until revoked.
    """
    __tablename__ = "expert_verifications"
    __table_args__ = (
        Index("ix_expert_verifications_user_id", "user_id"),
        {"comment": "Admin-granted expert verification badges shown on "
         "comments; one user at most one active row."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    verified_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    badge_title: Mapped[Optional[str]] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
    )
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped[Optional[User]] = relationship(
        "User", foreign_keys=[user_id], back_populates="expert_verifications",
    )


# â”€â”€ IngestFeed â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class IngestFeed(Base):
    """
    A monitored source of raw news items (RSS, Reddit, SEC EDGAR, Twitter/X).

    Real-time discovery needs more than the built-in RSS_feed_mapping: operators
    register the feeds they care about here, and the scheduled ingest pipeline
    fans out over all active rows. feed_type selects the adapter; url is
    provider-specific (an RSS URL, a subreddit, an EDGAR query, an X search).
    last_fetched_at/last_error give operators a per-feed health signal.
    """
    __tablename__ = "ingest_feeds"
    __table_args__ = (
        UniqueConstraint("feed_type", "url", name="uq_ingest_feeds_type_url"),
        Index("ix_ingest_feeds_active", "is_active"),
        {"comment": "Registered raw-news feeds the ingest pipeline polls. "
         "Unique (feed_type, url); health tracked via last_fetched_at and "
         "last_error."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    feed_type: Mapped[str] = mapped_column(String(24), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    topic_hint: Mapped[Optional[str]] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
    )
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# â”€â”€ OutboundWebhook â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class OutboundWebhook(Base):
    """
    A user-registered endpoint that receives POSTs when matching events are
    processed (API monetization / real-time updates).

    event_types narrows which announcements fire the hook ('event.created'
    etc.); an empty array means every processed event. The secret signs each
    payload with HMAC-SHA256 (X-GlobeLens-Signature) so receivers can verify
    authenticity. Deleting a user cascades their webhooks and delivery rows.
    """
    __tablename__ = "outbound_webhooks"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_outbound_webhooks_user_url"),
        Index("ix_outbound_webhooks_user_id", "user_id"),
        {"comment": "User-registered outbound webhook endpoints. Unique "
         "(user_id, url); HMAC-SHA256 signed delivery via X-GlobeLens-Signature."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    event_types: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), default=list)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[Optional[User]] = relationship("User")


# â”€â”€ WebhookDelivery â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class WebhookDelivery(Base):
    """
    One outbound webhook POST attempt for one event.

    status is 'pending' (queued), 'sent' (2xx received) or 'failed' (non-2xx
    or transport error). attempts + next_attempt_at drive the retry schedule;
    last_error keeps the reason for operators.
    """
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "webhook_id", "event_id", name="uq_webhook_deliveries_webhook_event"
        ),
        Index("ix_webhook_deliveries_status", "status"),
        Index("ix_webhook_deliveries_next_attempt_at", "next_attempt_at"),
        {"comment": "Outbound webhook POST attempts, one row per "
         "(webhook, event); status/attempts/next_attempt_at drive retries on "
         "the unique (webhook_id, event_id) constraint."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outbound_webhooks.id", ondelete="CASCADE"), nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending",
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    webhook: Mapped[Optional[OutboundWebhook]] = relationship("OutboundWebhook")


# â”€â”€ Organization / OrgMember / CompetitorTopic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class Organization(Base):
    """
    An enterprise tenant (B2B dashboard).

    Carries its plan tier and the owning admin. Members (OrgMember) share the
    tenant; CompetitorTopic rows power competitor monitoring; custom Sources
    (Source.organization_id) are the tenant's own feeds.
    """
    __tablename__ = "organizations"
    __table_args__ = (
        {"comment": "Enterprise tenants for the B2B dashboard; members share "
         "competitor topics and custom sources."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    plan: Mapped[str]                = mapped_column(
        String(16), default="ENTERPRISE", server_default="ENTERPRISE",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[List["OrgMember"]] = relationship(
        "OrgMember", back_populates="organization", cascade="all, delete-orphan",
    )


class OrgMember(Base):
    """One user's seat in an Organization. role: owner | admin | member."""
    __tablename__ = "org_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_members_org_user"),
        {"comment": "One seat per (organization, user). 'owner' created the "
         "tenant; 'member' was invited. Cascade-deletes with the org."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member", server_default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped[Optional[Organization]] = relationship("Organization", back_populates="members")
    user: Mapped[Optional[User]] = relationship("User")


class CompetitorTopic(Base):
    """
    A competitor-monitoring topic monitored by an enterprise tenant.

    keywords are the phrases to track; ingestion results for matching events
    are aggregated into the tenant's competitor dashboard.
    """
    __tablename__ = "competitor_topics"
    __table_args__ = (
        Index("ix_competitor_topics_org_id", "organization_id"),
        {"comment": "Competitor-monitoring keywords per enterprise tenant; "
         "match events are aggregated into the competitor dashboard."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
    )
    competitor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    keywords: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# â”€â”€ EventView (reading-habit tracking) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class EventView(Base):
    """
    One recorded view of an event by a user (per-user analytics).

    The analytics surface derives reading habits, coverage mix and bias
    personalization ("you read 40% left-leaning sources this week") from these
    rows. seen_at is the moment of the view; dwell_seconds is a best-effort
    client-reported duration.
    """
    __tablename__ = "event_views"
    __table_args__ = (
        Index("ix_event_views_user_id_seen_at", "user_id", "seen_at"),
        Index("ix_event_views_event_id", "event_id"),
        {"comment": "Per-user event views backing reading-habit and bias "
         "personalization analytics."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False,
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dwell_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    user: Mapped[Optional[User]] = relationship("User")
    event: Mapped[Optional[Event]] = relationship("Event")
