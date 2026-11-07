"""
GlobeLens AI — Application Configuration
Reads all settings from environment variables (or .env file via pydantic-settings).
"""
from functools import lru_cache
from typing import Any, List, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder secrets that must never reach a deployed environment. Kept as
# module constants so the guard and the defaults cannot drift apart.
INSECURE_DEVELOPMENT_SECRET = "CHANGE_ME_IN_PRODUCTION"
INSECURE_DOCKER_SECRET = "super_secret_dev_key_change_in_production"


class Settings(BaseSettings):
    """Central configuration store for all environment-driven settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    SECRET_KEY: str = INSECURE_DEVELOPMENT_SECRET
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # Refresh tokens are long-lived but must never be usable as a Bearer access
    # token. The `typ` claim is what separates the two; see AuthService.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # Set to true to accept the development placeholder secret outside
    # development. It exists only so a staging box can be booted deliberately;
    # production always refuses, with no override.
    ALLOW_INSECURE_DEV_SECRET: bool = False

    # ── Entitlements ─────────────────────────────────────────────────────────
    # When false (default), usage is *metered* but quotas are never enforced,
    # so existing endpoints keep their current limits. When true, an account
    # that has spent its plan's monthly allowance on a metered action is
    # refused with 429. No payment provider is wired yet, so every account is
    # on the FREE plan regardless of this flag.
    ENFORCE_PLAN_QUOTAS: bool = False

    # ── Payments / Stripe ────────────────────────────────────────────────────
    # Billing is a real channel and also defaults OFF: with no STRIPE_SECRET_KEY
    # the PaymentProvider factory answers Noop and the self-serve upgrade path
    # refuses with an honest message. Set STRIPE_SECRET_KEY to start creating
    # checkout sessions, and STRIPE_WEBHOOK_SECRET to accept signature-verified
    # subscription events. Price IDs map to the plan ladder: FREE has no price.
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_ENTERPRISE: str = ""

    # ── API rate limiting ────────────────────────────────────────────────────
    # Redis sliding-window counters over the whole /api/v1 surface, bucketed
    # by principal (JWT user, hashed API key) or client IP for anonymous calls.
    # Like plan quotas, this defaults OFF so existing behavior is untouched;
    # when enabled, a bucket that exceeds its daily allowance is refused with
    # 429 + Retry-After plus X-RateLimit-* headers. FREE (the default plan and
    # anonymous callers) gets API_DAILY_LIMIT, PRO gets API_DAILY_LIMIT_PRO,
    # ENTERPRISE has no daily cap.
    ENFORCE_RATE_LIMITS: bool = False
    API_DAILY_LIMIT: int = 100
    API_DAILY_LIMIT_PRO: int = 10_000

    # ── Scheduled digest / comprehensive briefs ────────────────────────────────
    # Like the quotas above this defaults OFF: the engine only starts its cron
    # loop when enabled, so an operator opts in before any periodic work runs.
    DIGEST_ENABLED: bool = False
    # 5-field cron: minute hour day-of-month month day-of-week. Default is a
    # daily 06:00 UTC comprehensive brief.
    DIGEST_CRON: str = "0 6 * * *"
    # Cap on events ranked (by importance score) into a single brief. Defaults
    # to the daily "top 5" the roadmap specifies.
    DIGEST_MAX_EVENTS: int = 5
    # When no earlier digest exists, how far back the first run may look.
    DIGEST_LOOKBACK_HOURS: int = 24

    # ── Weekly deep-dive digest ────────────────────────────────────────────────
    # A separate scheduled variant of the comprehensive brief: a wider window
    # and a larger cap, delivered on this cron (default Sunday 07:00 UTC).
    DIGEST_WEEKLY_ENABLED: bool = False
    DIGEST_WEEKLY_CRON: str = "0 7 * * 0"
    # Cap for the weekly deep-dive. Keeping it above the daily cap is what makes
    # the weekly run a "deep dive" rather than a re-send of Monday's brief.
    DIGEST_WEEKLY_MAX_EVENTS: int = 12
    # Weekly window spans this many days when no earlier digest exists.
    DIGEST_WEEKLY_LOOKBACK_DAYS: int = 7

    # ── Re-engagement ──────────────────────────────────────────────────────────
    # A subscriber whose last successful send was more than this many days ago
    # gets a win-back line in the next email. Needs no open-tracking: it is a
    # pure function of our own delivery history.
    REENGAGEMENT_DAYS: int = 30
    DIGEST_REENGAGEMENT_ENABLED: bool = False

    # ── Email / Newsletter ─────────────────────────────────────────────────────
    # Email is a real delivery channel, so it also defaults OFF: with
    # EMAIL_ENABLED false the delivery queue records each send as 'logged'
    # (captured to the log, never claimed as emailed). It only relays through
    # SMTP once an operator provides a relay and flips the flag.
    EMAIL_ENABLED: bool = False
    # Background drain cadence for pending deliveries.
    EMAIL_POLL_SECONDS: int = 60
    EMAIL_SMTP_HOST: str = ""
    EMAIL_SMTP_PORT: int = 587
    EMAIL_SMTP_USERNAME: str = ""
    EMAIL_SMTP_PASSWORD: str = ""
    EMAIL_FROM_EMAIL: str = "newsletter@globelens.local"
    EMAIL_SMTP_STARTTLS: bool = True
    # Public origin used to build unsubscribe links in the email body, e.g.
    # "https://app.example.com". Empty disables the link (the body then tells
    # the reader to sign in and toggle the subscription instead).
    APP_BASE_URL: str = ""

    # ── Web Push ───────────────────────────────────────────────────────────────
    # Push, like email, is a real delivery channel and defaults OFF: with
    # PUSH_ENABLED false the delivery queue records each send as 'logged'
    # (captured to the log, never claimed as pushed). It only relays through a
    # push service once an operator supplies a VAPID keypair and flips the flag.
    PUSH_ENABLED: bool = False
    # Background drain cadence for pending pushes.
    PUSH_POLL_SECONDS: int = 60
    # VAPID identity for the push service. LEFT_BLANK: the sender refuses to
    # start with PUSH_ENABLED true but no keys — it fails loudly instead of
    # guessing. Set all three to relay real notifications.
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_CONTACT_EMAIL: str = ""
    # Display name in the notification payload ("subject").
    PUSH_SUBJECT: str = "GlobeLens AI"
    # Follower-event alerting: when an event a user follows flips to PROCESSED,
    # queue a push for every device that user registered. Honors PUSH_ENABLED
    # (off → the log-mode sender captures the would-be payload). Gated so an
    # operator who wants only digest pushes can cut the follower channel.
    PUSH_EVENT_FOLLOW_ENABLED: bool = True

    # ── External fact-check adapters ─────────────────────────────────────────
    # Third-party fact-checker feed that supplies the genuine "reviewed by"
    # attribution on analysed claims. This is a real external lookup, so it
    # defaults OFF: an empty FACTCHECK_PROVIDER makes no network call and every
    # claim keeps honest nulls. Set it to "google" and provide the key to enable
    # lookups. Following the provider rule used across the app, selecting a
    # provider without its key is refused loudly at call time, never skipped
    # silently or guessed from another provider's credentials.
    FACTCHECK_PROVIDER: str = ""  # "" | "google" | "politifact" | "factcheck.org"
    GOOGLE_FACTCHECK_API_KEY: str = ""
    # PolitiFact's public rulings feed (no API key required); only used when
    # FACTCHECK_PROVIDER="politifact". BASE defaults to the site origin that
    # hosts /factchecks/atom.xml and the JSON statements endpoint.
    POLITIFACT_BASE_URL: str = "https://www.politifact.com"
    # FactCheck.org's public article feed (no API key required); only used when
    # FACTCHECK_PROVIDER="factcheck.org". /feed/ is the origin's RSS 2.0 feed.
    FACTCHECKORG_BASE_URL: str = "https://www.factcheck.org"
    # Per-request cap on lookups (one claim = one API call) to bound latency and
    # billable quota; claims beyond the cap simply stay un-reviewed.
    FACTCHECK_MAX_LOOKUPS_PER_REQUEST: int = 5
    FACTCHECK_TIMEOUT_SECONDS: float = 10

    # ── Real-time feeds ───────────────────────────────────────────────────────
    # SSE "live" feed that announces events the moment a batch run flips them to
    # PROCESSED. Unlike the digest/push/newsletter workers (which default off
    # because they perform periodic send work), the realtime broker is idle
    # unless a client is actually connected and Redis is already a hard
    # dependency, so this defaults ON. Set to false to refuse any /stream
    # connection and skip the Redis subscription.
    REALTIME_ENABLED: bool = True
    # Redis pub/sub channel the broker relays to connected SSE clients.
    REALTIME_EVENT_CHANNEL: str = "globelens:events:new"
    # Bounded Redis list of the most recent PROCESSED-event announcements,
    # used to answer /events/latest?since=... so a client reconnecting to the
    # SSE stream can catch up on announcements that happened while offline.
    REALTIME_LATEST_KEY: str = "globelens:events:latest"
    REALTIME_LATEST_CAP: int = 200

    # ── Scheduled ingestion ────────────────────────────────────────────────────
    # Background scraper that runs the RSS-discovery + Playwright hydration
    # pipeline on a cron schedule, like the digest engine. Defaults OFF: the
    # loop only starts when an operator opts in. `"0 */4 * * *"` every four
    # hours matches the default frequency the roadmap expects, and manual
    # extraction stays available through POST /articles/scrape regardless.
    INGEST_ENABLED: bool = False
    INGEST_CRON: str = "0 */4 * * *"

    # ── PostgreSQL / pgvector ─────────────────────────────────────────────────
    # Async URL (asyncpg driver) — used by FastAPI runtime and SQLAlchemy engine
    DATABASE_URL: str = (
        "postgresql+asyncpg://globelens:globelens_secret@db:5432/globelens_db"
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://:redis_secret@cache:6379/0"

    # ── Elasticsearch ─────────────────────────────────────────────────────────
    ELASTICSEARCH_URL: str = "http://search:9200"
    ELASTICSEARCH_INDEX_EVENTS: str = "globelens_events"

    # ── LLM Providers ─────────────────────────────────────────────────────────
    # Chat adapter selected purely from this value; a provider can be wired by
    # setting this plus its credentials, with no code change. Unknown values are
    # refused at call time (never silently routed to another provider).
    LLM_PROVIDER: str = "anthropic"  # anthropic | openai | grok | gemini | nvidia | bedrock | ollama | cohere
    # Embedding adapter. Same rule: choosing one here plus its credentials is
    # enough to wire it, and an unknown value is refused, not mapped elsewhere.
    EMBEDDING_PROVIDER: str = "openai"  # openai | gemini | grok | azure | bedrock | ollama | cohere
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5"   # aliases resolve to a current model; override per deployment
    OPENAI_API_KEY: str = ""
    GROK_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    AZURE_EMBEDDING_API_KEY: Optional[str] = None
    AZURE_EMBEDDING_ENDPOINT: Optional[str] = None
    NVIDIA_API_KEY: Optional[str] = None
    NVIDIA_API_URL: str = "https://integrate.api.nvidia.com/v1"
    # Ollama is a local OpenAI-compatible endpoint (/v1 on the given origin).
    # API key is optional; Ollama ignores it but the OpenAI client requires the
    # field, so the factory sends a fixed wire value.
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_CHAT_MODEL: str = "llama3.1"
    OLLAMA_EMBEDDING_MODEL: str = "nomic-embed-text"
    COHERE_API_KEY: Optional[str] = None
    COHERE_CHAT_MODEL: str = "command-r-plus-08-2024"
    COHERE_EMBEDDING_MODEL: str = "embed-english-v3.0"
    # AWS Bedrock Runtime. boto3 is imported lazily, so the app boots without
    # it; choosing a Bedrock provider without the SDK installed fails at call
    # time with a clear message instead.
    AWS_REGION: Optional[str] = None
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_SESSION_TOKEN: Optional[str] = None
    BEDROCK_CHAT_MODEL: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    BEDROCK_EMBEDDING_MODEL: str = "amazon.titan-embed-text-v2:0"

    # ── Media AI (vision / audio) ─────────────────────────────────────────────
    # Separately-gated multimodal adapters. Like the chat/embedding providers,
    # these default to NOTHING: with VISION_PROVIDER/AUDIO_PROVIDER empty the
    # media controller refuses honestly instead of faking a caption or
    # transcription. The value names the provider to reuse its credentials
    # (openai → OPENAI_API_KEY, gemini → GEMINI_API_KEY).
    VISION_PROVIDER: str = ""  # "" | openai | gemini
    VISION_MODEL: str = "gpt-4o-mini"
    AUDIO_PROVIDER: str = ""  # "" | openai (whisper)
    AUDIO_MODEL: str = "whisper-1"

    # ── Single sign-on (OIDC) ────────────────────────────────────────────────
    # SSO is fully opt-in and defaults OFF: until OIDC_ENABLED is true the
    # /auth/oidc/* routes refuse honestly, and the login page does not show an
    # SSO button. When enabled, OIDC_ISSUER must be the issuer's discovery
    # document origin (e.g. https://accounts.google.com); the well-known config,
    # JWKS, tokens and userinfo are all resolved from it at call time.
    OIDC_ENABLED: bool = False
    OIDC_ISSUER: str = ""
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    OIDC_SCOPES: str = "openid email profile"
    OIDC_NAME: str = "Google"
    TAVILY_API_KEY: Optional[str] = None

    # ── Real-time source adapters (beyond RSS) ───────────────────────────────
    # The ingest pipeline fans out over registered IngestFeed rows. Beyond RSS,
    # adapters exist for Reddit (public JSON, or OAuth creds when provided),
    # SEC EDGAR (public full-text search; SEC asks senders to identify
    # themselves, hence EDGAR_USER_AGENT_EMAIL), and the Twitter/X API v2
    # search (needs X_BEARER_TOKEN). Each adapter refuses loudly when its
    # required credential is missing rather than silently degrading.
    X_BEARER_TOKEN: str = ""
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""
    REDDIT_USER_AGENT: str = ""
    EDGAR_USER_AGENT_EMAIL: str = ""
    # Cap on feeds a single scheduled run may poll and on raw items each feed
    # returns, bounding the network + processing window of each pipeline run.
    INGEST_MAX_FEEDS_PER_RUN: int = 20
    INGEST_MAX_ITEMS_PER_FEED: int = 25

    # ── Outbound webhooks (API monetization / real-time updates) ─────────────
    # Real delivery channel, so it also defaults OFF like email/push: with
    # WEBHOOK_ENABLED false the dispatcher records each delivery as 'logged'
    # instead of POSTing anywhere. When enabled, queued deliveries are drained
    # on a background worker with bounded retries (exponential backoff).
    WEBHOOK_ENABLED: bool = False
    WEBHOOK_POLL_SECONDS: int = 30
    # Per-user cap on registered endpoints, to bound blast radius of any single
    # account and the queue size that must be drained.
    WEBHOOK_MAX_ENDPOINTS: int = 5
    WEBHOOK_DELIVERY_MAX_ATTEMPTS: int = 5
    WEBHOOK_RETRY_BASE_SECONDS: int = 60

    # ── Enterprise dashboard ──────────────────────────────────────────────────
    # Cap of seats (members) per organization; B2B tenants get this many
    # invites before needing the operator to raise it.
    ORG_MAX_SEATS: int = 25

    # ── Per-user analytics ────────────────────────────────────────────────────
    # View-tracking is always on for authenticated users (single row per
    # (user, event) per window); these bound how far the insights endpoint may
    # read back and how many days "this week" spans.
    ANALYTICS_LOOKBACK_DAYS: int = 30
    ANALYTICS_WEEK_DAYS: int = 7


    # ── Embedding ─────────────────────────────────────────────────────────────
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    # ── CORS ──────────────────────────────────────────────────────────────
    # Accepts a comma-separated string OR a JSON array string from the env:
    #   CORS_ORIGINS=http://localhost:3000          (bare string)
    #   CORS_ORIGINS=http://a:3000,http://b:3001   (comma-separated)
    #   CORS_ORIGINS=["http://localhost:3000"]      (JSON array)
    CORS_ORIGINS: Any = ["http://localhost:3000"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> List[str]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):          # JSON array
                import json
                return json.loads(v)
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def IS_PRODUCTION(self) -> bool:
        return self.APP_ENV.strip().lower() in {"production", "prod"}

    @model_validator(mode="after")
    def _reject_insecure_secret(self) -> "Settings":
        """
        Refuse to start a production process on a known placeholder secret.

        JWT signing is symmetric, so a known SECRET_KEY lets an attacker mint
        their own valid access tokens for any user, including an admin role. That
        is total authentication bypass, and it is the kind of failure that
        stays silent: the app boots, logs look healthy, and every request is
        authenticated against a key that is published in the repository.

        Failing at startup is the only point where this is cheap to fix. The
        alternative -- logging a warning -- was what allowed the placeholder to
        survive in the first place, and it would survive a deploy the same way.

        Development and test are exempt so contributors are not forced to
        generate a key before running anything. A non-production environment
        can opt in explicitly via ALLOW_INSECURE_DEV_SECRET.
        """
        if self.SECRET_KEY != INSECURE_DEVELOPMENT_SECRET:
            # Still reject the docker-compose placeholder, which is a *real*
            # default rather than a "please change me" marker, and which is
            # easy to leave in place while thinking the config was set.
            if self.SECRET_KEY == INSECURE_DOCKER_SECRET:
                raise ValueError(
                    "SECRET_KEY is set to the docker-compose placeholder. "
                    "This value is published in the repository, so anyone can "
                    "forge valid JWTs. Generate a unique value, e.g. "
                    "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
                )
            return self

        if not self.IS_PRODUCTION:
            if self.ALLOW_INSECURE_DEV_SECRET:
                return self
            # Development/test keep working; this is informational, not fatal.
            import warnings
            warnings.warn(
                f"SECRET_KEY is the development placeholder while APP_ENV="
                f"{self.APP_ENV!r}. Fine locally, never acceptable in "
                f"production.",
                UserWarning,
                stacklevel=2,
            )
            return self

        raise ValueError(
            "SECRET_KEY is the development placeholder but APP_ENV is "
            f"{self.APP_ENV!r}. Refusing to start: a known signing key lets "
            "anyone mint valid access tokens for any user. Set SECRET_KEY to a "
            "unique random value, e.g. "
            "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
        )

    @property
    def SYNC_DATABASE_URL(self) -> str:
        """
        Synchronous database URL for Alembic migrations.

        Alembic's migration runner is synchronous (no event loop), so it
        cannot use asyncpg. We derive the sync URL from DATABASE_URL by
        replacing the driver segment, keeping credentials and host identical.

        postgresql+asyncpg://... → postgresql+psycopg2://...
        """
        return self.DATABASE_URL.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — instantiated once per process."""
    return Settings()


# Module-level convenience alias
settings = get_settings()
