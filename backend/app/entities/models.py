"""GlobeLens AI — Domain Entities (SQLAlchemy ORM Models)"""
import enum
import uuid
from datetime import datetime
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint, func
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import settings


# ── Base ──────────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Enumerations (matching blueprint spec) ────────────────────────────────────
class ProcessingStatus(str, enum.Enum):
    SCRAPED   = "SCRAPED"
    EMBEDDED  = "EMBEDDED"
    CLUSTERED = "CLUSTERED"
    PROCESSED = "PROCESSED"


class ArticleOrigin(str, enum.Enum):
    """Where an article came from.

    PIPELINE rows are collected by the scraper and carry a source + event.
    AUTHORED rows are written in the newsroom by a journalist: they have an
    ``author_id`` and no publisher source.
    """
    PIPELINE = "PIPELINE"
    AUTHORED = "AUTHORED"


class PublicationStatus(str, enum.Enum):
    """Editorial state of an authored article.

    Pipeline articles are published on arrival; authored ones start as DRAFT
    and only become visible to readers once a journalist publishes them.
    """
    DRAFT     = "DRAFT"
    PUBLISHED = "PUBLISHED"


class UserRole(str, enum.Enum):
    GUEST      = "GUEST"
    AUTH_USER  = "AUTH_USER"
    JOURNALIST = "JOURNALIST"
    ADMIN      = "ADMIN"


class FactCheckResult(str, enum.Enum):
    TRUE      = "TRUE"
    FALSE     = "FALSE"
    UNCERTAIN = "UNCERTAIN"


class BiasLean(str, enum.Enum):
    LEFT         = "LEFT"
    CENTER_LEFT  = "CENTER_LEFT"
    CENTER       = "CENTER"
    CENTER_RIGHT = "CENTER_RIGHT"
    RIGHT        = "RIGHT"


class IllustrationStatus(str, enum.Enum):
    """Where a generated illustration has got to.

    PENDING is written the moment an event is queued, so a row exists to retry
    from and the frontend can tell "not generated yet" apart from "no
    illustration was ever asked for". FAILED keeps the reason: image providers
    fail for credit, quota and content-policy reasons, and an operator needs to
    see which without digging through worker logs.
    """
    PENDING    = "PENDING"
    GENERATING = "GENERATING"
    READY      = "READY"
    FAILED     = "FAILED"
    SKIPPED    = "SKIPPED"


class BlockSource(str, enum.Enum):
    """Who decided an IP is blocked.

    THREAT_LENS is the anomaly scorer (the S5 innovation). MANUAL is the
    operator escape hatch, filled by hand so an admin can block an IP the model
    never flagged.
    """
    THREAT_LENS = "THREAT_LENS"
    MANUAL      = "MANUAL"


# ── BlockedIp ─────────────────────────────────────────────────────────────────
class BlockedIp(Base):
    """A network identity blocked by the Threat Lens worker (or an operator).

    The Redis block marker is what the middleware actually consults on every
    request (lookup-free and TTL'd); this table is the durable record of a
    block so it survives a Redis flush, shows up in ops queries, and can be
    listed/revoked from the admin panel. One row per IP, upserted when a block
    is re-issued.

    ``source`` is a String to mirror how Illustration.status is stored — the
    table is created by hand-written SQL (VARCHAR), and an Enum column here
    would make every `alembic check` report drift.
    """
    __tablename__ = "blocked_ips"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ip: Mapped[str] = mapped_column(String(45), unique=True, index=True, nullable=False)
    source: Mapped[str] = mapped_column(
        String(20),
        default=BlockSource.THREAT_LENS.value,
        server_default=BlockSource.THREAT_LENS.value,
        nullable=False,
    )
    reason: Mapped[Optional[str]] = mapped_column(Text)
    risk_score: Mapped[Optional[float]] = mapped_column(Float)
    blocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ── Source ────────────────────────────────────────────────────────────────────
class Source(Base):
    """Models media publishers (BBC, CNN, Al Jazeera, etc.)."""
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str]               = mapped_column(String(255), nullable=False, unique=True)
    url: Mapped[str]                = mapped_column(String(500), nullable=False)
    country: Mapped[Optional[str]]  = mapped_column(String(100))
    credibility_score: Mapped[float] = mapped_column(Float, default=0.5)
    bias_lean: Mapped[BiasLean]     = mapped_column(Enum(BiasLean), default=BiasLean.CENTER)
    created_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())

    articles: Mapped[List["Article"]] = relationship("Article", back_populates="source")


# ── User ──────────────────────────────────────────────────────────────────────
class User(Base):
    """Authentication identity and user preferences."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID]             = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str]                 = mapped_column(String(255), nullable=False)
    email: Mapped[str]                = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str]        = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole]            = mapped_column(Enum(UserRole), default=UserRole.AUTH_USER)
    is_blocked: Mapped[bool]          = mapped_column(Boolean, default=False)
    preferred_topics: Mapped[Optional[List[str]]]    = mapped_column(ARRAY(String), default=list)
    preferred_countries: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), default=list)
    created_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now())

    # passive_deletes lets PostgreSQL's ON DELETE CASCADE remove the child
    # rows. Without it SQLAlchemy loads each child and assigns NULL to the
    # foreign key on delete, which trips the NOT NULL constraint and turns
    # account deletion into a 500 whenever the user has ever bookmarked.
    comments: Mapped[List["Comment"]]           = relationship("Comment", back_populates="user", passive_deletes=True)
    fact_check_requests: Mapped[List["FactCheckRequest"]] = relationship("FactCheckRequest", back_populates="user", passive_deletes=True)
    bookmarks: Mapped[List["Bookmark"]] = relationship("Bookmark", back_populates="user", passive_deletes=True)
    # Deleting an author keeps their published articles; the FK is ON DELETE
    # SET NULL, so passive_deletes lets PostgreSQL detach them.
    authored_articles: Mapped[List["Article"]] = relationship("Article", back_populates="author", passive_deletes=True)
    reading_lists: Mapped[List["ReadingList"]] = relationship("ReadingList", back_populates="user", passive_deletes=True)


# ── Event ─────────────────────────────────────────────────────────────────────
class Event(Base):
    """High-level news event aggregating multiple articles."""
    __tablename__ = "events"

    id: Mapped[uuid.UUID]            = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str]               = mapped_column(String(500), nullable=False)
    summary: Mapped[Optional[str]]   = mapped_column(Text)
    topic: Mapped[Optional[str]]     = mapped_column(String(255), index=True)
    country: Mapped[Optional[str]]   = mapped_column(String(100), index=True)
    latitude: Mapped[Optional[float]]  = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    importance_score: Mapped[float]  = mapped_column(Float, default=0.0)
    is_promoted: Mapped[bool]        = mapped_column(Boolean, default=False)
    bias_lean: Mapped[Optional[BiasLean]] = mapped_column(Enum(BiasLean), nullable=True)
    status: Mapped[str]              = mapped_column(String(50), default="DRAFT", index=True)
    created_at: Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Ordered to match ArticleRepository.find_by_event: summaries cite sources
    # positionally as [1], [2]..., and the frontend resolves those against this
    # list. An unordered relationship makes citation N resolve to an arbitrary
    # article, so the two orderings have to be defined in the same place.
    articles: Mapped[List["Article"]] = relationship(
        "Article",
        back_populates="event",
        order_by="(Article.published_at.asc().nulls_last(), Article.id.asc())",
        passive_deletes=True,
    )
    comments: Mapped[List["Comment"]] = relationship("Comment", back_populates="event", passive_deletes=True)
    contradictions: Mapped[List["EventContradiction"]] = relationship(
        "EventContradiction",
        back_populates="event",
        cascade="all, delete-orphan",
    )
    illustration: Mapped[Optional["Illustration"]] = relationship(
        "Illustration",
        back_populates="event",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Illustration(Base):
    """A generated illustration for one event.

    One row per event, holding both the artefact and the record of how it was
    made: the prompt, the provider and model that ran, and the attribution line
    shown to readers. The prompt and attribution are kept because a generated
    image is only trustworthy as newsroom art if an editor can see afterwards
    what it was asked to depict and which model produced it.

    ``storage_key`` is a relative path under the media volume, never an absolute
    one, so the same row survives the directory being mounted somewhere else in
    a deployment.
    """
    __tablename__ = "illustrations"

    id: Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        # unique=True already creates the lookup index this column needs; adding
        # index=True as well would only build a second, redundant one.
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[IllustrationStatus] = mapped_column(
        # String, not Enum: the table is created by hand-written SQL in the
        # migration as VARCHAR(20), so an Enum column here would make every
        # `alembic check` report drift. IllustrationStatus stays the vocabulary.
        String(20), default=IllustrationStatus.PENDING.value, nullable=False, index=True
    )
    prompt: Mapped[Optional[str]] = mapped_column(Text)
    negative_prompt: Mapped[Optional[str]] = mapped_column(Text)
    provider: Mapped[Optional[str]]   = mapped_column(String(50))
    model: Mapped[Optional[str]]      = mapped_column(String(120))
    attribution: Mapped[Optional[str]] = mapped_column(String(255))
    storage_key: Mapped[Optional[str]] = mapped_column(String(500))
    content_type: Mapped[Optional[str]] = mapped_column(String(100))
    byte_size: Mapped[Optional[int]]    = mapped_column(Integer)
    width: Mapped[Optional[int]]        = mapped_column(Integer)
    height: Mapped[Optional[int]]       = mapped_column(Integer)
    seed: Mapped[Optional[int]]         = mapped_column(BigInteger)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    event: Mapped["Event"] = relationship("Event", back_populates="illustration")


# ── Article ───────────────────────────────────────────────────────────────────
class Article(Base):
    """Content unit — either scraped from a publisher or authored in the newsroom."""
    __tablename__ = "articles"

    id: Mapped[uuid.UUID]             = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str]                = mapped_column(String(500), nullable=False)
    content: Mapped[Optional[str]]    = mapped_column(Text)
    url: Mapped[Optional[str]]        = mapped_column(String(2000), unique=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now())
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus), default=ProcessingStatus.SCRAPED, index=True
    )
    is_hidden: Mapped[bool]           = mapped_column(Boolean, default=False)
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), index=True)
    event_id: Mapped[Optional[uuid.UUID]]  = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), index=True)
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    origin: Mapped[ArticleOrigin] = mapped_column(
        Enum(ArticleOrigin), default=ArticleOrigin.PIPELINE, nullable=False, index=True
    )
    publication_status: Mapped[PublicationStatus] = mapped_column(
        Enum(PublicationStatus), default=PublicationStatus.PUBLISHED, nullable=False, index=True
    )

    source: Mapped[Optional["Source"]]  = relationship("Source", back_populates="articles")
    event: Mapped[Optional["Event"]]    = relationship("Event", back_populates="articles")
    author: Mapped[Optional["User"]]    = relationship("User", back_populates="authored_articles")
    embedding: Mapped[Optional["Embedding"]] = relationship("Embedding", back_populates="article", uselist=False)
    comments: Mapped[List["Comment"]] = relationship("Comment", back_populates="article", passive_deletes=True)


# ── Embedding ─────────────────────────────────────────────────────────────────
class Embedding(Base):
    """Vector embedding storage — decoupled from Article for perf."""
    __tablename__ = "embeddings"

    id: Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 1536 dimensions for text-embedding-3-small
    # Width tracks settings.EMBEDDING_DIMENSIONS so the ORM stays aligned with the
    # pgvector column after a provider switch (1536 OpenAI, 1024 bge-m3, 768 nomic).
    vector: Mapped[list]       = mapped_column(Vector(settings.EMBEDDING_DIMENSIONS))
    model: Mapped[str]         = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    article_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("articles.id"), unique=True, index=True)

    article: Mapped["Article"] = relationship("Article", back_populates="embedding")


# ── Comment ───────────────────────────────────────────────────────────────────
class Comment(Base):
    """User community comments on an Event or a newsroom-authored Article."""
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint(
            "(event_id IS NOT NULL) <> (article_id IS NOT NULL)",
            name="ck_comment_exactly_one_target",
        ),
    )

    id: Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content: Mapped[str]       = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    # A comment targets either an aggregated Event or a newsroom-authored
    # Article, never both; the check constraint enforces exactly one.
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), index=True, nullable=True
    )
    article_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), index=True, nullable=True
    )

    user:  Mapped["User"]  = relationship("User",  back_populates="comments")
    event: Mapped[Optional["Event"]] = relationship("Event", back_populates="comments")
    article: Mapped[Optional["Article"]] = relationship("Article", back_populates="comments")


# ── FactCheckRequest ──────────────────────────────────────────────────────────
class FactCheckRequest(Base):
    """User-submitted external verification requests."""
    __tablename__ = "fact_check_requests"

    id: Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    input_text_url: Mapped[str]    = mapped_column(Text, nullable=False)
    result: Mapped[Optional[FactCheckResult]] = mapped_column(Enum(FactCheckResult))
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)

    user: Mapped["User"] = relationship("User", back_populates="fact_check_requests")


# ── Bookmark ──────────────────────────────────────────────────────────────────
class Bookmark(Base):
    """User saved events (Reading List)."""
    __tablename__ = "bookmarks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="bookmarks")
    event: Mapped["Event"] = relationship("Event")


# ── Reading List ──────────────────────────────────────────────────────────────
class ReadingList(Base):
    """A user-curated, named collection of events and newsroom articles.

    Bookmarks answer "did I save this?" with a single flat set. A reading list
    is the editorial layer on top: named collections ("Iran watch", "Climate")
    that can be reordered and exported. Names are unique per user so the picker
    never offers two indistinguishable lists.
    """
    __tablename__ = "reading_lists"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_reading_list_user_name"),
    )

    id: Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str]              = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_public: Mapped[bool]        = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", back_populates="reading_lists")
    items: Mapped[List["ReadingListItem"]] = relationship(
        "ReadingListItem",
        back_populates="reading_list",
        cascade="all, delete-orphan",
        order_by="ReadingListItem.position.asc()",
    )


class ReadingListItem(Base):
    """One saved unit inside a reading list — an Event or an authored Article.

    Mirrors Comment: exactly one of event_id / article_id is set. The FK cascade
    means deleting the underlying event/article removes the stale list entry
    rather than leaving a row that points at nothing.
    """
    __tablename__ = "reading_list_items"
    __table_args__ = (
        CheckConstraint(
            "(event_id IS NOT NULL) <> (article_id IS NOT NULL)",
            name="ck_reading_list_item_exactly_one_target",
        ),
        UniqueConstraint("list_id", "event_id", name="uq_reading_list_item_event"),
        UniqueConstraint("list_id", "article_id", name="uq_reading_list_item_article"),
    )

    id: Mapped[uuid.UUID]            = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    list_id: Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("reading_lists.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True, nullable=True
    )
    article_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), index=True, nullable=True
    )
    position: Mapped[int]            = mapped_column(Integer, default=0)
    note: Mapped[Optional[str]]      = mapped_column(Text)
    created_at: Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=func.now())

    reading_list: Mapped["ReadingList"] = relationship("ReadingList", back_populates="items")
    event: Mapped[Optional["Event"]]    = relationship("Event")
    article: Mapped[Optional["Article"]] = relationship("Article")


# ── NewsletterSubscriber ───────────────────────────────────────────────────────
class NewsletterSubscriber(Base):
    """An email recipient for the daily / weekly digest.

    Deliberately separate from User: most subscribers are anonymous readers who
    only supplied an email, and linking to an account is optional (user_id is
    SET NULL if the account is deleted). Delivery requires a confirmed address
    (double opt-in), so is_confirmed gates sending alongside is_active, which
    the unsubscribe link flips.
    """
    __tablename__ = "newsletter_subscribers"

    id: Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str]             = mapped_column(String(320), nullable=False, unique=True, index=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    interests: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), default=list)
    # "daily" or "weekly"; stored as text so a new cadence needs no enum migration.
    frequency: Mapped[str]         = mapped_column(String(20), default="daily", index=True)
    is_active: Mapped[bool]        = mapped_column(Boolean, default=True, index=True)
    is_confirmed: Mapped[bool]     = mapped_column(Boolean, default=False, index=True)
    confirm_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True, nullable=True)
    unsubscribe_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ── PushSubscription ───────────────────────────────────────────────────────────
class PushSubscription(Base):
    """A browser Web Push subscription belonging to a user.

    One user may have many (one per browser/device). `endpoint` is the unique
    address issued by the browser's push service; `p256dh` and `auth` are the
    client keys used to encrypt each payload. ON DELETE CASCADE removes rows with
    the account so we never retain push addresses for deleted users.
    """
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]   = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str]        = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str]          = mapped_column(Text, nullable=False)
    auth: Mapped[str]            = mapped_column(Text, nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ── PushPreference ─────────────────────────────────────────────────────────────
class PushPreference(Base):
    """Per-user opt-ins controlling which notification categories are delivered."""
    __tablename__ = "push_preferences"

    id: Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]   = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    breaking_news: Mapped[bool]  = mapped_column(Boolean, default=True)
    followed_events: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_briefing: Mapped[bool] = mapped_column(Boolean, default=False)
    briefing_hour_utc: Mapped[int] = mapped_column(Integer, default=8)
    timezone: Mapped[str]        = mapped_column(String(64), default="UTC")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── EventContradiction ────────────────────────────────────────────────────────
class EventContradiction(Base):
    """
    A recorded disagreement between two outlets covering the same event.

    Persisted rather than recomputed per request: detection reads article
    bodies and compares every claim pair, which is far too expensive to run
    inside a GET. Rewritten only when an event's articles change.

    This stores a *candidate* conflict for human review, not a verified
    falsehood. ``nature`` records why the two claims looked incompatible, and
    nothing here asserts which side is correct.
    """
    __tablename__ = "event_contradictions"

    id: Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    nature: Mapped[str]        = mapped_column(String(50), nullable=False)
    detail: Mapped[str]        = mapped_column(Text, nullable=False)
    claim_a_text: Mapped[str]  = mapped_column(Text, nullable=False)
    claim_a_source: Mapped[str] = mapped_column(String(255), nullable=False)
    claim_b_text: Mapped[str]  = mapped_column(Text, nullable=False)
    claim_b_source: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship("Event", back_populates="contradictions")

    # One identical disagreement is stored once; re-scanning an unchanged event
    # must not accumulate duplicates.
    __table_args__ = (
        UniqueConstraint(
            "event_id", "nature", "claim_a_text", "claim_b_text", name="uq_event_contradiction_pair"
        ),
    )
