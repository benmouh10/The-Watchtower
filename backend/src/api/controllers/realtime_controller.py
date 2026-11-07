"""
Real-time event feed endpoint (Server-Sent Events).

Serves exactly the same public summaries as GET /events — a stream of newly
PROCESSED event announcements. No authentication is required because the
payload is identical to the public feed list; gating it would add zero security
while forcing an Authorization header the EventSource API cannot send.

The route is registered under the /events router prefix before the parameterised
routes so a literal "stream" segment never collides with /{event_id}.
"""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

from src.core.config import settings

router = APIRouter(tags=["Events"])

# Heartbeat cadence: keeps intermediaries from timing the connection out and
# gives the generator a cheap way to notice the client has gone away.
HEARTBEAT_SECONDS = 15.0


@router.get("/stream", summary="Live new-events stream (SSE)")
async def event_stream(request: Request) -> StreamingResponse:
    if not settings.REALTIME_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Real-time feed is disabled by configuration.",
        )

    from src.services.realtime_service import hub, sse_envelope

    sub_id, queue = await hub.subscribe()

    async def generator():
        try:
            # Literal comment line: the standard SSE handshake most clients use
            # to know the connection is live before the first real event.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield sse_envelope(payload.get("id"), payload)
        finally:
            await hub.unsubscribe(sub_id)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # Prevents a proxy/CDN from buffering the open stream.
            # Declaring an (identity) content-encoding also makes Starlette's
            # GZipMiddleware pass every byte through untouched — a streaming
            # response must never be re-buffered into a gzip frame.
            "Content-Encoding": "identity",
        },
    )