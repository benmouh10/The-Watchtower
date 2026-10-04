"""
GlobeLens AI — Application Configuration
Reads all settings from environment variables (or .env file via pydantic-settings).
"""
from functools import lru_cache
from typing import Any, List, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.redaction import redact_mapping

# SMTP hosts that are development test sinks, never real delivery. Used to
# refuse a production boot that would silently swallow outbound mail.
_SMTP_SINK_HOSTS = {"", "mailpit", "localhost", "127.0.0.1", "::1"}

# APP_ENV values that mean "this is a real deployment".
_PRODUCTION_ENVS = {"production", "prod"}


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
    SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ── Rate limiting (Redis-backed; see app/middleware/rate_limit.py) ────────
    # Specs use the form "<count>/<period>" with period second|minute|hour|day.
    # AUTH guards the credential endpoints (brute-force surface); DEFAULT covers
    # every other route. TRUST_FORWARDED must stay False unless the app sits
    # behind a proxy that sets X-Forwarded-For, otherwise the header is spoofable.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: str = "120/minute"
    RATE_LIMIT_AUTH: str = "10/minute"
    # Anonymous visitors (no bearer token) are the cheapest to abuse, so they
    # get a tighter budget than authenticated accounts.
    RATE_LIMIT_GUEST: str = "30/minute"
    RATE_LIMIT_TRUST_FORWARDED: bool = False

    # ── Security response headers (see app/middleware/security_headers.py) ────
    # HSTS is emitted automatically only when APP_ENV=production.
    SECURITY_HEADERS_ENABLED: bool = True

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
    LLM_PROVIDER: str = "anthropic"          # "anthropic" | "openai" | "grok" | "gemini" | "nvidia" | "ollama"
    EMBEDDING_PROVIDER: str = "openai"       # "openai" | "gemini" | "grok" | "azure" | "ollama"
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    GROK_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    AZURE_EMBEDDING_API_KEY: Optional[str] = None
    AZURE_EMBEDDING_ENDPOINT: Optional[str] = None
    NVIDIA_API_KEY: Optional[str] = None
    NVIDIA_API_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_LLM_MODEL: str = "nvidia/nemotron-3-super-120b-a12b"
    TAVILY_API_KEY: Optional[str] = None

    # ── Ollama (local inference, OpenAI-compatible API) ────────────────────────
    # Inside Docker the host is reachable via host.docker.internal, not localhost.
    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434/v1"
    OLLAMA_LLM_MODEL: str = "qwen2.5-coder:14b"
    OLLAMA_EMBEDDING_MODEL: str = "bge-m3"

    # How much of an article body is fed to the embedding model. bge-m3 is a
    # document encoder: the lead carries the who/what/where, while the tail is
    # often boilerplate, related-links and newsletter furniture. Embedding the
    # whole body made syndicated roundups ("Latest news bulletin") collide with
    # each other on template structure rather than subject matter.
    EMBEDDING_MAX_CONTENT_CHARS: int = 1500


    # ── Embedding ─────────────────────────────────────────────────────────────
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    # Must match the pgvector column width in the embeddings table.
    # 1536 = text-embedding-3-small, 1024 = bge-m3, 768 = nomic-embed-text.
    EMBEDDING_DIMENSIONS: int = 1536

    # ── Text-to-Speech (article narration) ─────────────────────────────────────
    # Feeds GET /articles/{id}/audio and GET /events/{id}/audio.
    #
    # "local" is the default and costs nothing: Kokoro-82M runs on CPU inside this
    # image, so narration works with no API key and no per-minute billing. The
    # hosted providers stay available as upgrades: "openai" and "grok" speak
    # through an OpenAI-compatible /audio/speech endpoint, "gemini" uses the
    # Generative Language TTS endpoint. An unsupported or credential-less provider
    # makes the route answer 501 rather than failing opaquely.
    TTS_PROVIDER: str = "local"                # "local" | "openai" | "grok" | "gemini"
    TTS_MODEL: str = "gpt-4o-mini-tts"
    TTS_VOICE: str = "alloy"                   # OpenAI voice; Gemini prebuilt names also accepted
    TTS_SPEED: float = 1.0
    # Local engine files, baked into the image by the Dockerfile and overridable so
    # the fp16/int8 Kokoro builds can be dropped in without a rebuild.
    TTS_LOCAL_MODEL_PATH: str = "/app/models/tts/kokoro-v1.0.onnx"
    TTS_LOCAL_VOICES_PATH: str = "/app/models/tts/voices-v1.0.bin"
    TTS_LOCAL_VOICE: str = "af_heart"          # any Kokoro voice id, e.g. am_michael
    # LAME quality for the locally encoded MP3: 2 = best/slowest, 7 = near-best.
    # lameenc picks the bitrate from this (128-160 kbps at Kokoro's 24 kHz mono,
    # which is transparent for speech), so there is no bitrate knob to set.
    TTS_LOCAL_MP3_QUALITY: int = 7
    # Kokoro's raw output sits around -22 dBFS RMS, which reads as quiet on phone
    # and laptop speakers. Audio is scaled toward the target RMS and then capped at
    # the peak ceiling so normalisation can never introduce clipping.
    TTS_LOCAL_TARGET_RMS_DBFS: float = -20.0
    TTS_LOCAL_PEAK_DBFS: float = -1.0
    # Local synthesis runs at roughly half real time on CPU, so a cold article would
    # make the listener wait as long as the clip is long. The warmer walks recent
    # articles and events in the background and fills the Redis cache ahead of the
    # first press of Listen, one item at a time so it never starves live requests.
    TTS_PREWARM_ENABLED: bool = True
    TTS_PREWARM_STARTUP_DELAY_SECONDS: float = 60.0   # let boot and health checks settle
    TTS_PREWARM_INTERVAL_SECONDS: float = 900.0       # sweep cadence for missed items
    TTS_PREWARM_BATCH: int = 3                        # articles + events per sweep
    TTS_PREWARM_ITEM_DELAY_SECONDS: float = 2.0       # pause between items
    # A 20,000-character article takes over two minutes of CPU to narrate, which on a
    # small host would starve live requests. Anything longer than this is left cold:
    # the play button still synthesises it on demand, it just is not warmed ahead.
    TTS_PREWARM_MAX_CHARS: int = 6000
    # A single OpenAI speech request accepts at most 4,000 characters, so longer
    # articles are split on paragraph/sentence boundaries and the audio chunks are
    # concatenated. TTS_MAX_CHARS bounds how long one play button can run.
    TTS_MAX_CHARS: int = 20000
    TTS_CHUNK_CHARS: int = 3500
    # ONNX Runtime threads for local speech. kokoro_onnx opens its session with no
    # options, which would otherwise give it one intra-op thread per logical CPU and
    # let a single sentence of narration saturate the whole machine. The graph is
    # memory bound, so a small pool is nearly as fast and leaves the database, search
    # and web tiers on the same host responsive. 0 would leave the default in place.
    TTS_ONNX_THREADS: int = 4
    # How many clips may be synthesised at the same time. onnxruntime hands every
    # concurrent run its own threads, so this multiplies the ceiling above: 1 keeps
    # the service on TTS_ONNX_THREADS cores no matter how many listeners arrive.
    TTS_MAX_CONCURRENT_SYNTHESIS: int = 1
    # Chunk sizes used when narration is streamed to the player, far below
    # TTS_CHUNK_CHARS on purpose. Playback drains the buffer at 1x while synthesis
    # refills it at a fairly fixed 32 characters per second, so the buffer sawtooths:
    # each chunk's audio has to cover the wall time of the next chunk. A large chunk
    # therefore risks a long silence, and measured on this model 800 characters took
    # ~22s to build against ~25s of audio, which stalls repeatedly. Smaller chunks
    # cost a little extra per-call overhead and bound the worst gap to a few seconds;
    # once the buffer is ahead it only grows.
    # The first chunk also sets the wait before any sound, so it stays small; it is
    # deliberately a little larger than nothing so the opening covers the first refill.
    TTS_STREAM_FIRST_CHUNK_CHARS: int = 150
    TTS_STREAM_CHUNK_CHARS: int = 300
    # Lifetime of a signed playback URL (see services/audio_signing).
    AUDIO_URL_TTL_SECONDS: int = 600
    # Narration is cached in Redis under a hash of provider + model + voice +
    # text, so an article is synthesised once per TTL instead of on every play.
    TTS_CACHE_TTL_SECONDS: int = 604800        # 7 days

    # ── CORS ──────────────────────────────────────────────────────────────
    # Accepts a comma-separated string OR a JSON array string from the env:
    #   CORS_ORIGINS=http://localhost:3000          (bare string)
    #   CORS_ORIGINS=http://a:3000,http://b:3001   (comma-separated)
    #   CORS_ORIGINS=["http://localhost:3000"]      (JSON array)
    CORS_ORIGINS: Any = ["http://localhost:3000"]

    # ── Email / Newsletter (SMTP) ─────────────────────────────────────────────
    # The development default targets the Mailpit catcher service defined in
    # docker-compose (SMTP on 1025, no auth, no TLS), so a fresh checkout can
    # send and inspect real mail without external credentials. Production
    # overrides these via SMTP_* environment variables.
    SMTP_HOST: str = "mailpit"
    SMTP_PORT: int = 1025
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "newsletter@globelens.ai"
    SMTP_FROM_NAME: str = "GlobeLens AI"
    SMTP_STARTTLS: bool = False
    SMTP_SSL: bool = False
    SMTP_TIMEOUT_SECONDS: int = 15

    # Public base URLs embedded in outbound email links (confirm / unsubscribe
    # / open an event). Kept separate because the API and the web client are
    # distinct origins.
    FRONTEND_URL: str = "http://localhost:3000"
    API_PUBLIC_URL: str = "http://localhost:8000"

    # ── Newsletter scheduling ─────────────────────────────────────────────────
    NEWSLETTER_DAILY_TOP_N: int = 5
    NEWSLETTER_WEEKLY_TOP_N: int = 8
    NEWSLETTER_DAILY_HOUR_UTC: int = 7
    NEWSLETTER_WEEKLY_DAY: str = "monday"
    NEWSLETTER_WEEKLY_HOUR_UTC: int = 8

    # ── Celery (background jobs) ──────────────────────────────────────────────
    # Falls back to REDIS_URL when unset, so enabling the worker needs no extra
    # configuration in development.
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    # ── Web Push (VAPID) ──────────────────────────────────────────────────────
    # Generate a keypair with the cryptography package (P-256), url-safe base64
    # without padding. The private key signs every request; the public key is
    # handed to the browser. /push/vapid-public-key reports `enabled: false`
    # until both are set. PUSH_DAILY_BRIEFING_HOUR_UTC drives the beat task.
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_SUBJECT: str = "mailto:ops@globelens.ai"
    PUSH_DAILY_BRIEFING_HOUR_UTC: int = 8
    # Seconds the push service should retain an undelivered message. Must be
    # non-zero: Windows Push Notification Service answers ttl=0 with 400.
    PUSH_TTL_SECONDS: int = 3600

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

    @model_validator(mode="after")
    def _enforce_production_secrets(self) -> "Settings":
        """Refuse to start in production with a weak or default SECRET_KEY."""
        if self.APP_ENV.strip().lower() in _PRODUCTION_ENVS:
            key = (self.SECRET_KEY or "").strip()
            if key in {"", "CHANGE_ME_IN_PRODUCTION"} or len(key) < 32:
                raise ValueError(
                    "SECRET_KEY must be a random value of at least 32 characters "
                    "when APP_ENV=production. Generate one with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(48))"'
                )
        return self

    @model_validator(mode="after")
    def _enforce_production_smtp(self) -> "Settings":
        """Refuse to start in production while email still points at a test sink.

        Without this, a production deploy that forgot the SMTP_* variables looks
        healthy but every newsletter confirmation and digest is silently dropped
        into nowhere (or, worse, a same-host Mailpit that no one reads).
        """
        if self.APP_ENV.strip().lower() in _PRODUCTION_ENVS:
            host = (self.SMTP_HOST or "").strip().lower()
            if host in _SMTP_SINK_HOSTS:
                raise ValueError(
                    "SMTP_HOST still points at the development Mailpit sink "
                    f"({self.SMTP_HOST!r}) but APP_ENV=production requires real "
                    "email delivery. For Gmail set SMTP_HOST=smtp.gmail.com, "
                    "SMTP_PORT=587, SMTP_STARTTLS=true, SMTP_USERNAME=<address>, "
                    "SMTP_PASSWORD=<App Password>, SMTP_FROM_EMAIL=<address>."
                )
        return self

    def redacted(self) -> dict:
        """Settings as a mapping with secrets and URL credentials masked."""
        return redact_mapping(self.model_dump())

    def __repr__(self) -> str:
        return f"Settings({self.redacted()!r})"

    __str__ = __repr__

    @property
    def celery_broker(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def celery_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL

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
