"""
Selective gzip: compress text, never media.

Narration is delivered as a live MP3 stream so playback can start on the first
chunk. Compressing it defeats that. Starlette's GZipMiddleware writes each chunk
into a `gzip.GzipFile`, which does not hand bytes to the underlying buffer until
its own buffer fills, so a media response arrives at the player only after most or
all of the recording has been synthesised. It also cannot help: MP3 is already
compressed, so gzip spends CPU for a fraction of a percent and quietly corrupts
byte ranges for anyone who tries to seek.

So media, byte-range requests, and anything already encoded pass straight through.
"""
from typing import Awaitable, Callable, MutableMapping

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipResponder
from starlette.types import ASGIApp, Message, Receive, Scope, Send

Scope = MutableMapping[str, object]
Message = MutableMapping[str, object]

# Already compressed, or compressible only marginally: gzip either wastes CPU or
# breaks seeking.
PRECOMPRESSED_PREFIXES = (
    "audio/",
    "video/",
    "image/",
    "application/zip",
    "application/gzip",
    "application/x-gzip",
    "application/octet-stream",
    "application/vnd.apple.mpegurl",
)


def is_precompressed(content_type: str) -> bool:
    return content_type.lower().startswith(PRECOMPRESSED_PREFIXES)


class SelectiveGZipMiddleware:
    """GZipMiddleware, with pass-through for media and range requests."""

    def __init__(
        self, app: ASGIApp, minimum_size: int = 500, compresslevel: int = 9
    ) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        wants_gzip = "gzip" in headers.get("accept-encoding", "")
        # A range request is a seek; the bytes it asks for must stay addressable.
        seeks = "range" in headers

        if not wants_gzip or seeks:
            await self.app(scope, receive, send)
            return

        responder = _SelectiveResponder(self.app, self.minimum_size, self.compresslevel)
        await responder(scope, receive, send)


class _SelectiveResponder:
    """Runs the app once and routes each message to gzip or straight through."""

    def __init__(self, app: ASGIApp, minimum_size: int, compresslevel: int) -> None:
        self.app = app
        self.gzip = GZipResponder(app, minimum_size, compresslevel=compresslevel)
        self.send: Send = _unattached_send
        self.passthrough: bool = False
        self.decided: bool = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.send = send
        self.gzip.send = send
        await self.app(scope, receive, self.send_selectively)

    async def send_selectively(self, message: Message) -> None:
        if not self.decided and message["type"] == "http.response.start":
            content_type = Headers(raw=message.get("headers", [])).get("content-type", "")
            self.passthrough = is_precompressed(content_type)
            self.decided = True

        if self.passthrough:
            await self.send(message)
        else:
            await self.gzip.send_with_gzip(message)


async def _unattached_send(message: Message) -> None:
    raise RuntimeError("send awaitable not set")  # pragma: no cover