"""
GlobeLens AI — Celery application.

The worker and beat processes import `celery_app` from here. Broker and result
backend default to the project's Redis (see settings.celery_broker), so enabling
background jobs requires no additional configuration.

Beat drives these cadences:
  - event enrichment → every LLM_ENRICH_INTERVAL_MINUTES, which is what turns
    DRAFT clustering placeholders into scored, summarised, publishable events
  - event artwork → every ILLUSTRATION_INTERVAL_MINUTES, so an event that has
    just been enriched gets an image without anyone triggering it by hand
  - daily  brief   → every day at NEWSLETTER_DAILY_HOUR_UTC
  - weekly deep dive → NEWSLETTER_WEEKLY_DAY at NEWSLETTER_WEEKLY_HOUR_UTC
  - push briefing  → every day at PUSH_DAILY_BRIEFING_HOUR_UTC
"""
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "globelens",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "enrich-pending-events": {
        "task": "app.worker.tasks.enrich_pending_events_task",
        "schedule": float(settings.LLM_ENRICH_INTERVAL_MINUTES * 60),
    },
    # Artwork for events enriched since the last pass. The task drains what it
    # finds and stops itself when a batch generates nothing, so this is a
    # re-check rather than a workload — and the Redis lock means a run still in
    # progress is skipped instead of doubling up.
    "illustrate-new-events": {
        "task": "app.worker.tasks.generate_illustrations_task",
        "schedule": float(settings.ILLUSTRATION_INTERVAL_MINUTES * 60),
    },
    "newsletter-daily-brief": {
        "task": "app.worker.tasks.send_digest_task",
        "schedule": crontab(hour=settings.NEWSLETTER_DAILY_HOUR_UTC, minute=0),
        "args": ("daily",),
    },
    "newsletter-weekly-deep-dive": {
        "task": "app.worker.tasks.send_digest_task",
        "schedule": crontab(
            day_of_week=settings.NEWSLETTER_WEEKLY_DAY,
            hour=settings.NEWSLETTER_WEEKLY_HOUR_UTC,
            minute=0,
        ),
        "args": ("weekly",),
    },
    "push-daily-briefing": {
        "task": "app.worker.tasks.send_push_briefings_task",
        "schedule": crontab(hour=settings.PUSH_DAILY_BRIEFING_HOUR_UTC, minute=0),
    },
}

# Ensures app.worker.tasks is imported when the worker starts.
celery_app.autodiscover_tasks(["app.worker"])
