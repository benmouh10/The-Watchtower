"""
GlobeLens AI — Threat Lens block-and-telemetry middleware.

Sits between the CORS and rate-limit layers. For every non-exempt request it:

1. answers 403 immediately when the client's identity carries a block marker
   (set by the threat worker after enough windowed anomalies), and
2. otherwise lets the request through and enqueues one telemetry event on the
   dedicated "threat" Celery queue so the worker can score it off the request
   path.

Everything is fail-open and completely inert while THREAT_LENS_ENABLED is
false — a fresh checkout sees the middleware do nothing at all.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.services import threat_lens as tl


class ThreatLensMiddleware(BaseHTTPMiddleware):
    """Block-list enforcement + telemetry producer for the Threat Lens worker."""

    async def dispatch(self, request: Request, call_next):
        if not settings.THREAT_LENS_ENABLED or request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path.rstrip("/") or "/"
        if path in tl.THREAT_EXEMPT_PATHS or path.startswith("/media/"):
            return await call_next(request)

        identity = tl.client_identity(request)

        try:
            blocked = await tl.is_identity_blocked(identity)
        except Exception:
            # Service already fails open internally; this belt-and-braces guard
            # keeps an unexpected transport error from turning into a 500.
            blocked = False

        if blocked:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "This client is temporarily blocked because it was "
                        "flagged as abusive traffic. Try again later."
                    )
                },
            )

        response = await call_next(request)

        # Only healthy traffic is telemetried: an unhandled 500 tells us nothing
        # about the *client*, and streaming a media file still telemetries the
        # *request* via call_next having streamed, so the status stays accurate.
        if response.status_code < 500:
            try:
                await tl.enqueue_telemetry(
                    tl.build_telemetry(request, status_code=response.status_code)
                )
            except Exception:
                pass
        return response