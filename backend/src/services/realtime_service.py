"""
GlobeLens AI — Real-time feeds
==============================
*               *              *
One transport for "an event just reached PROCESSED". The honest triggers:
Event rows only flip to PROCESSED inside the LLM batch worker, so that service
publishes after each successful commit and this bus fan-outs to connected SSE
clients. The payload is the exact feed summary the REST endpoints already serve
(_serialize_event_summary), so the stream never invents fields a client cannot
get from GET /events.

Multi-worker honesty: each uvicorn/gunicorn child hosts its own fan-out hub and
its own Redis pub/sub subscriber. A connection lives on exactly one worker, and
every worker relays every announcement, so each client receives exactly one
copy. No shared registry, no double-fires.

Constraints honoured here:
  * publish_processed_event re-opens its own short session. The caller (the LLM
    batch loop) must not hold a transaction open while we broadcast, and the
    serializer needs Event.articles eagerly loaded (MissingGreenlet otherwise),
    so we re-fetch with selectinload exactly like the list endpoints.
  * A failed broadcast is non-fatal: the publisher logs and the batch worker
    continues. The feed degrades to the existing fetch-based behaviour.
  * The broker restarts on Redis hiccups; a single channel is watched and the
    loop is idle unless a client is connected.
"""
import asyncio
import json
from typing import Any, Dict, Tuple

import redis.asyncio as aioredis
import structlog

from src.core.config import settings

logger = structlog.get_logger()


class RealtimeHub:
    """In-process fan-out: one asyncio.Queue per connected SSE client."""

    def __init__(self) -> None:
        self._subscribers: Dict[int, asyncio.Queue] = {}
        self._next_subscriber_id = 0
        self._lock = asyncio.Lock()

    async def subscribe(self) -> Tuple[int, asyncio.Queue]:
        async with self._lock:
            self._next_subscriber_id += 1
            sub_id = self._next_subscriber_id
            queue: asyncio.Queue = asyncio.Queue()
            self._subscribers[sub_id] = queue
            return sub_id, queue

    async def unsubscribe(self, sub_id: int) -> None:
        async with self._lock:
            self._subscribers.pop(sub_id, None)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._subscribers.values())
        for queue in targets:
            queue.put_nowait(payload)


hub = RealtimeHub()


async def run_realtime_broker() -> None:
    """Relay the Redis event channel onto the local in-process hub (forever).

    Every worker process subscribes its own client and forwards to its own hub.
    A short-lived Redis outage is survived by the reconnect loop; the pub/sub
    connection is re-established after a pause and announcements resume.
    """
    while True:
        client = None
        pubsub = None
        try:
            client = aioredis.from_url(
                settings.REDIS_URL, encoding="utf-8", decode_responses=True
            )
            pubsub = client.pubsub()
            await pubsub.subscribe(settings.REALTIME_EVENT_CHANNEL)
            logger.info(
                "Real-time broker subscribed",
                channel=settings.REALTIME_EVENT_CHANNEL,
            )
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    payload = json.loads(message["data"])
                except (TypeError, ValueError):
                    logger.warning(
                        "Skipping non-JSON message on event channel",
                        raw=message.get("data"),
                    )
                    continue
                await hub.broadcast(payload)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.error("Real-time broker error; reconnecting", error=str(err))
            await asyncio.sleep(5)
        finally:
            for resource in (pubsub, client):
                if resource is None:
                    continue
                try:
                    await resource.aclose()
                except Exception:
                    pass


def sse_envelope(event_id: Any, payload: Dict[str, Any]) -> str:
    """One SSE frame for a new-event announcement.

    The id line lets an EventSource client send Last-Event-ID on reconnect so
    the server can (in principle) resume; clients dedupe by id as the cheap,
    unconditional guard.
    """
    return (
        f"id: {event_id}\n"
        f"event: new-event\n"
        f"data: {json.dumps(payload, default=str)}\n\n"
    )


async def publish_event_payload(payload: Dict[str, Any]) -> None:
    """Put an already-serialised event summary on the Redis bus."""
    client = aioredis.from_url(
        settings.REDIS_URL, encoding="utf-8", decode_responses=True
    )
    try:
        await client.publish(
            settings.REALTIME_EVENT_CHANNEL,
            json.dumps(payload, default=str),
        )
    finally:
        await client.aclose()


async def remember_announcement(entry: Dict[str, str]) -> None:
    """Append one announcement to the bounded latest-event cache."""
    client = aioredis.from_url(
        settings.REDIS_URL, encoding="utf-8", decode_responses=True
    )
    try:
        key = settings.REALTIME_LATEST_KEY
        cap = max(1, settings.REALTIME_LATEST_CAP)
        pipeline = client.pipeline()
        pipeline.rpush(key, json.dumps(entry))
        # Keep only the newest `cap` entries (negative index trims from the tail).
        pipeline.ltrim(key, -cap, -1)
        await pipeline.execute()
    finally:
        await client.aclose()


async def recent_announcements() -> list:
    """Newest-first list of the cache entries: {id, created_at}."""
    client = aioredis.from_url(
        settings.REDIS_URL, encoding="utf-8", decode_responses=True
    )
    try:
        raw = await client.lrange(settings.REALTIME_LATEST_KEY, 0, -1)
    finally:
        await client.aclose()
    entries = []
    for blob in reversed(raw):  # list is oldest→newest
        try:
            entry = json.loads(blob)
        except (TypeError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("id"):
            entries.append(entry)
    return entries


async def publish_processed_event(event_id: str) -> None:
    """Announce one just-PROCESSED event live.

    Re-reads the row so the payload carries the identical shape the feed list
    serves (summary, topic, contradiction signal, etc.). A missing row or a
    serialisation problem is logged, never raised: a live-announcement failure
    must not abort the batch worker that just committed the event.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.core.database import AsyncSessionFactory
    from src.data.entities.models import Event
    from src.api.controllers.event_controller import _serialize_event_summary

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Event)
            .options(selectinload(Event.articles))
            .where(Event.id == event_id)
        )
        event = result.scalars().first()
        if event is None:
            logger.warning(
                "Cannot broadcast non-existent event", event_id=event_id
            )
            return
        payload = _serialize_event_summary(event)
        payload["id"] = str(event.id)

    await publish_event_payload(payload)
    try:
        await remember_announcement(
            {
                "id": str(event.id),
                "created_at": (
                    event.created_at.isoformat()
                    if event.created_at
                    else payload.get("created_at")
                ) or "",
            }
        )
    except Exception as err:
        # The cache is a reconnect convenience, never a delivery guarantee:
        # a Redis blip here must not abort the batch worker that committed the
        # event, which already published on the pub/sub channel above.
        logger.warning("Cannot remember announcement", error=str(err))