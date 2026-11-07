# =============================================================================
# GlobeLens AI — FastAPI Application Entry Point
# =============================================================================
from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import engine, get_db
from src.core.logging import configure_logging

# ── Controller (Router) imports ───────────────────────────────────────────────
from src.api.controllers.auth_controller import router as auth_router
from src.api.controllers.user_controller import router as user_router
from src.api.controllers.event_controller import router as event_router
from src.api.controllers.realtime_controller import router as realtime_router
from src.api.controllers.article_controller import router as article_router
from src.api.controllers.search_controller import router as search_router
from src.api.controllers.fact_check_controller import router as fact_check_router
from src.api.controllers.comment_controller import router as comment_router
from src.api.controllers.bookmark_controller import router as bookmark_router
from src.api.controllers.reading_list_controller import router as reading_list_router
from src.api.controllers.source_controller import router as source_router
from src.api.controllers.admin_controller import router as admin_router
from src.api.controllers.api_key_controller import router as api_key_router
from src.api.controllers.chatbot_controller import router as chatbot_router
from src.api.controllers.entitlement_controller import usage_router, billing_router
from src.api.controllers.digest_controller import router as digest_router
from src.api.controllers.newsletter_controller import router as newsletter_router
from src.api.controllers.push_controller import router as push_router
from src.api.controllers.plans_controller import router as plans_router
from src.api.controllers.media_controller import router as media_router
from src.api.controllers.webhook_controller import router as webhook_router
from src.api.controllers.ingest_controller import router as ingest_router
from src.api.controllers.analytics_controller import router as analytics_router
from src.api.controllers.org_controller import router as org_router



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
    from src.core.elasticsearch import init_elasticsearch
    await init_elasticsearch()
    
    # Initialize Elasticsearch index mappings
    try:
        from src.services.search_service import SearchService
        search_service = SearchService()
        await search_service.create_events_index()
    except Exception as exc:
        print(f"❌ Failed to create Elasticsearch index: {exc}")

    # Scheduled digest / comprehensive brief engine. Opt-in (DIGEST_ENABLED)
    # like the rate limiter and plan quotas, so a default process runs nothing
    # periodic. Cancelled on shutdown like Redis/ES resources.
    digest_task: asyncio.Task = None
    if settings.DIGEST_ENABLED:
        from src.services.digest_scheduler import run_digest_scheduler
        digest_task = asyncio.create_task(run_digest_scheduler())
        print("📅 Scheduled digest engine enabled.")

    # Newsletter delivery worker. Opt-in like the digest engine: it only drains
    # the queue when EMAIL_ENABLED gives it a relay (real SMTP) or a log
    # capture. With both DIGEST_ENABLED and EMAIL_ENABLED off there is never a
    # digest to send, so no periodic work runs by default.
    newsletter_task: asyncio.Task = None
    if settings.DIGEST_ENABLED or settings.EMAIL_ENABLED:
        from src.services.newsletter_service import run_newsletter_worker
        newsletter_task = asyncio.create_task(run_newsletter_worker())
        print("📧 Newsletter delivery worker enabled.")

    # Web Push delivery worker. Opt-in like email: it only drains the queue
    # when PUSH_ENABLED gives it a VAPID relay (or a log capture). With
    # DIGEST_ENABLED and PUSH_ENABLED both off there is no digest to notify on
    # and no relay, so nothing periodic runs by default.
    push_task: asyncio.Task = None
    if settings.DIGEST_ENABLED or settings.PUSH_ENABLED or settings.PUSH_EVENT_FOLLOW_ENABLED:
        from src.services.push_service import run_push_worker
        push_task = asyncio.create_task(run_push_worker())
        print("🔔 Web Push delivery worker enabled.")

    # Real-time feed broker. Watches the Redis event channel and relays new event
    # announcements to connected SSE clients (/events/stream). Unlike the
    # digest/push workers this does no periodic send work — it is idle unless a
    # client is connected — and Redis is already a hard dependency, so it
    # defaults ON (REALTIME_ENABLED). Cancelled with the other workers.
    realtime_task: asyncio.Task = None
    if settings.REALTIME_ENABLED:
        from src.services.realtime_service import run_realtime_broker
        realtime_task = asyncio.create_task(run_realtime_broker())
        print("📡 Real-time event broker enabled.")

    # Scheduled ingestion. Opt-in (INGEST_ENABLED) like the digest engine so a
    # default process runs nothing periodic that hits the network. Each fire
    # runs the RSS-discovery + Playwright pipeline; manual extraction via
    # POST /articles/scrape remains available regardless.
    ingest_task: asyncio.Task = None
    if settings.INGEST_ENABLED:
        from src.services.ingest_scheduler import run_ingest_scheduler
        ingest_task = asyncio.create_task(run_ingest_scheduler())
        print("🌐 Scheduled ingestion engine enabled.")

    # Outbound webhook dispatcher. Opt-in like email/push: when WEBHOOK_ENABLED
    # is off the delegator records deliveries as 'logged' (queue/channel always
    # exercised); when on, a worker drains the queue through real HTTP POSTs.
    webhook_task: asyncio.Task = None
    if settings.WEBHOOK_ENABLED:
        from src.services.webhook_service import run_webhook_worker
        webhook_task = asyncio.create_task(run_webhook_worker())
        print("🔗 Outbound webhook dispatcher enabled.")

    yield
    if webhook_task is not None:
        webhook_task.cancel()
        try:
            await webhook_task
        except asyncio.CancelledError:
            pass
    if realtime_task is not None:
        realtime_task.cancel()
        try:
            await realtime_task
        except asyncio.CancelledError:
            pass
    if ingest_task is not None:
        ingest_task.cancel()
        try:
            await ingest_task
        except asyncio.CancelledError:
            pass
    if push_task is not None:
        push_task.cancel()
        try:
            await push_task
        except asyncio.CancelledError:
            pass
    if newsletter_task is not None:
        newsletter_task.cancel()
        try:
            await newsletter_task
        except asyncio.CancelledError:
            pass
    if digest_task is not None:
        digest_task.cancel()
        try:
            await digest_task
        except asyncio.CancelledError:
            pass
    
    # Dispose releases all pooled connections back to PostgreSQL
    await engine.dispose()
    
    # Close global Elasticsearch client
    try:
        from src.core.elasticsearch import es_client
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Sliding-window daily API rate limiting. A no-op unless ENFORCE_RATE_LIMITS
# is true, so default behaviour is unchanged until an operator opts in.
from src.api.middleware.rate_limit_middleware import RateLimitMiddleware

app.add_middleware(RateLimitMiddleware)

# ── API Routers (v1) ──────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"

app.include_router(auth_router,       prefix=f"{API_PREFIX}/auth",        tags=["Authentication"])
app.include_router(user_router,       prefix=f"{API_PREFIX}/users",       tags=["Users"])
# Real-time stream first: a literal "stream" segment on the events router must
# be matched before any parameterised /events/{event_id} route (the UUID path
# converter would not match "stream" anyway, this just removes the ordering
# question entirely).
app.include_router(realtime_router,   prefix=f"{API_PREFIX}/events",      tags=["Events"])
app.include_router(event_router,      prefix=f"{API_PREFIX}/events",      tags=["Events"])
app.include_router(article_router,    prefix=f"{API_PREFIX}/articles",    tags=["Articles"])
app.include_router(search_router,     prefix=f"{API_PREFIX}/search",      tags=["Search"])
app.include_router(fact_check_router, prefix=f"{API_PREFIX}/fact-check",  tags=["Fact-Check"])
app.include_router(comment_router,    prefix=f"{API_PREFIX}/comments",    tags=["Comments"])
app.include_router(bookmark_router,   prefix=f"{API_PREFIX}/bookmarks",   tags=["Bookmarks"])
app.include_router(reading_list_router, prefix=f"{API_PREFIX}/reading-lists", tags=["Reading Lists"])
app.include_router(source_router,    prefix=f"{API_PREFIX}/sources",     tags=["Sources"])
app.include_router(admin_router,      prefix=f"{API_PREFIX}/admin",       tags=["Admin"])
app.include_router(chatbot_router,    prefix=f"{API_PREFIX}/chatbot",     tags=["Chatbot"])
app.include_router(usage_router,      prefix=f"{API_PREFIX}/usage",       tags=["Usage"])
app.include_router(billing_router,    prefix=f"{API_PREFIX}/billing",     tags=["Billing"])
app.include_router(api_key_router,    prefix=f"{API_PREFIX}/api-keys",    tags=["API Keys"])
app.include_router(digest_router,     prefix=f"{API_PREFIX}/digests",     tags=["Digests"])
app.include_router(newsletter_router, prefix=f"{API_PREFIX}/newsletter",  tags=["Newsletter"])
app.include_router(push_router,       prefix=f"{API_PREFIX}/push",        tags=["Web Push"])
app.include_router(plans_router,      prefix=f"{API_PREFIX}/plans",       tags=["Plans"])
app.include_router(media_router,      prefix=f"{API_PREFIX}/media",       tags=["Media"])
app.include_router(webhook_router,    prefix=f"{API_PREFIX}/webhooks",    tags=["Webhooks"])
app.include_router(ingest_router,     prefix=f"{API_PREFIX}/admin/ingest", tags=["Ingest"])
app.include_router(analytics_router,  prefix=f"{API_PREFIX}/analytics",   tags=["Analytics"])
app.include_router(org_router,        prefix=f"{API_PREFIX}/org",          tags=["Organization"])



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
        from src.services.cache_service import cache_service
        redis_client = await cache_service._get_client()
        await redis_client.ping()
        redis_status = "ok"
    except Exception as exc:
        redis_status = f"error: {exc}"

    # 3. Test Elasticsearch
    try:
        from src.data.repositories.search_repository import SearchRepository
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
