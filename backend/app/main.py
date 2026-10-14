# =============================================================================
# GlobeLens AI — FastAPI Application Entry Point
# =============================================================================
from contextlib import asynccontextmanager

import asyncio

from fastapi import FastAPI, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import engine, get_db
from app.core.logging import configure_logging
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.selective_gzip import SelectiveGZipMiddleware
from app.middleware.threat_lens import ThreatLensMiddleware

# ── Controller (Router) imports ───────────────────────────────────────────────
from app.controllers.auth_controller import router as auth_router
from app.controllers.user_controller import router as user_router
from app.controllers.event_controller import router as event_router
from app.controllers.article_controller import router as article_router
from app.controllers.search_controller import router as search_router
from app.controllers.fact_check_controller import router as fact_check_router
from app.controllers.comment_controller import router as comment_router
from app.controllers.reading_list_controller import router as reading_list_router
from app.controllers.admin_controller import router as admin_router
from app.controllers.chatbot_controller import router as chatbot_router
from app.controllers.newsletter_controller import router as newsletter_router
from app.controllers.push_controller import router as push_router
from app.controllers.insights_controller import router as insights_router

from pathlib import Path

import structlog
from fastapi.staticfiles import StaticFiles

logger = structlog.get_logger()



# ─────────────────────────────────────────────────────────────────────────────
# Application Lifespan — startup / shutdown hooks
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifecycle events.
    - Startup : configure logging, warm the DB connection pool, init Elasticsearch and create index mappings.
    - Shutdown: dispose connection pools gracefully.
    """
    configure_logging()
    print("🚀 GlobeLens AI backend starting up...")
    
    # Warm-up: establish the first connection to verify DB reachability
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    print("✅ Database connection pool initialized.")
    
    # Initialize Elasticsearch connection with retry logic
    from app.core.elasticsearch import init_elasticsearch
    await init_elasticsearch()
    
    # Initialize Elasticsearch index mappings
    try:
        from app.services.search_service import SearchService
        search_service = SearchService()
        await search_service.create_events_index()
    except Exception as exc:
        print(f"❌ Failed to create Elasticsearch index: {exc}")
        
    # Fill the narration cache in the background so the first press of a play
    # button reads Redis instead of waiting on local speech synthesis.
    from app.services.narration_warmer import narration_warmer
    narration_warmer.start()

    # Pull the speech model into memory now rather than inside somebody's first
    # request: reading the weights costs several seconds, and the server can serve
    # traffic while it happens.
    from app.services.tts_service import tts_service
    asyncio.create_task(tts_service.preload_local())

    yield
    
    await narration_warmer.stop()
    
    # Dispose releases all pooled connections back to PostgreSQL
    await engine.dispose()
    
    # Close global Elasticsearch client
    try:
        from app.core.elasticsearch import es_client
        await es_client.close()
        print("✅ Elasticsearch connection closed.")
    except Exception as exc:
        print(f"❌ Error closing Elasticsearch connection: {exc}")
        
    print("🛑 GlobeLens AI backend shutting down.")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Application Instance
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="GlobeLens AI API",
    description=(
        "Advanced news intelligence platform API. "
        "Processes, clusters, and synthesizes global news using AI."
    ),
    version="2.0.0",
    docs_url="/docs" if settings.APP_ENV != "production" else None,
    redoc_url="/redoc" if settings.APP_ENV != "production" else None,
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────
# Order matters: added last is outermost. Rate limiting is added first so CORS
# wraps it and 429 responses still carry CORS headers for browser clients.
app.add_middleware(RateLimitMiddleware)
# Threat Lens sits outside the rate limiter (a blocked identity is declined
# before it can spend rate-limit budget) but inside CORS so its 403s still
# carry CORS headers for browser clients. Inert while THREAT_LENS_ENABLED=false.
app.add_middleware(ThreatLensMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Selective rather than blanket gzip: narration is a live MP3 stream, and a gzip
# compressor holds its first bytes back until the buffer fills, which would put the
# wait straight back that streaming removed. See middleware/selective_gzip.py.
app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000)
# Added last so it is outermost: every response, including 429s and CORS
# preflight replies, carries the hardening headers.
app.add_middleware(SecurityHeadersMiddleware)

# ── API Routers (v1) ──────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"

app.include_router(auth_router,       prefix=f"{API_PREFIX}/auth",        tags=["Authentication"])
app.include_router(user_router,       prefix=f"{API_PREFIX}/users",       tags=["Users"])
app.include_router(event_router,      prefix=f"{API_PREFIX}/events",      tags=["Events"])
app.include_router(article_router,    prefix=f"{API_PREFIX}/articles",    tags=["Articles"])
app.include_router(search_router,     prefix=f"{API_PREFIX}/search",      tags=["Search"])
app.include_router(fact_check_router, prefix=f"{API_PREFIX}/fact-check",  tags=["Fact-Check"])
app.include_router(comment_router,    prefix=API_PREFIX,                   tags=["Comments"])
app.include_router(reading_list_router, prefix=f"{API_PREFIX}/users",      tags=["Reading Lists"])
app.include_router(admin_router,      prefix=f"{API_PREFIX}/admin",       tags=["Admin"])
app.include_router(chatbot_router,    prefix=f"{API_PREFIX}/chatbot",     tags=["Chatbot"])
app.include_router(newsletter_router, prefix=f"{API_PREFIX}/newsletter",  tags=["Newsletter"])
app.include_router(push_router,       prefix=f"{API_PREFIX}/push",         tags=["Push Notifications"])
app.include_router(insights_router,   prefix=f"{API_PREFIX}/insights",     tags=["Insights"])

# ── Generated media ──────────────────────────────────────────────────────────
# Illustrations are immutable files written by the worker into a mounted volume,
# so they are served as static assets rather than through a controller: a public
# file that never changes does not need a database read per request.
#
# Mounted after the API routers on purpose — a catch-all static mount placed
# earlier would shadow /api/v1 and quietly break the whole API.
_illustrations_root = Path(settings.ILLUSTRATION_STORAGE_DIR)
try:
    _illustrations_root.mkdir(parents=True, exist_ok=True)
except OSError as _err:  # a read-only or missing volume must not stop the API
    logger.warn("Illustration storage unavailable", path=str(_illustrations_root), error=str(_err))
else:
    app.mount(
        settings.ILLUSTRATION_URL_PREFIX,
        StaticFiles(directory=str(_illustrations_root)),
        name="illustrations",
    )
    logger.info(
        "Serving generated illustrations",
        prefix=settings.ILLUSTRATION_URL_PREFIX,
        directory=str(_illustrations_root),
    )



# ── Health Check Endpoint ─────────────────────────────────────────────────────
@app.get("/health", tags=["Health"], summary="Service health probe")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Docker and load-balancer health probe.
    Performs a live SELECT 1 against PostgreSQL, pings Redis and Elasticsearch.
    """
    # 1. Test PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"

    # 2. Test Redis Cache
    try:
        from app.services.cache_service import cache_service
        redis_client = await cache_service._get_client()
        await redis_client.ping()
        redis_status = "ok"
    except Exception as exc:
        redis_status = f"error: {exc}"

    # 3. Test Elasticsearch
    try:
        from app.repositories.search_repository import SearchRepository
        search_repo = SearchRepository()
        ping_ok = await search_repo._client.ping()
        elasticsearch_status = "ok" if ping_ok else "degraded"
        await search_repo.close()
    except Exception as exc:
        elasticsearch_status = f"error: {exc}"

    overall_healthy = (
        db_status == "ok" and redis_status == "ok" and elasticsearch_status == "ok"
    )

    return JSONResponse(
        content={
            "status": "healthy" if overall_healthy else "degraded",
            "service": "GlobeLens AI Backend",
            "version": "2.0.0",
            "environment": settings.APP_ENV,
            "database": db_status,
            "redis": redis_status,
            "elasticsearch": elasticsearch_status,
        }
    )


@app.get("/", tags=["Root"])
async def root():
    return {"message": "Welcome to GlobeLens AI API. Visit /docs for Swagger UI."}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Browsers probe /favicon.ico on every origin, including the API's Swagger
    # UI. Answer 204 (no content) instead of 404 so the console and access logs
    # stay clean; the real icon is served by the frontend.
    return Response(status_code=204)
