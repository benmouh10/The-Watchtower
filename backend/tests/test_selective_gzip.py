"""
Tests for selective gzip.

The behaviour that matters is not "is text compressed" but "does a media stream
reach the player before it is finished". These tests pin the first-chunk property
directly, because the failure mode it guards against is invisible in a response
body assertion: a gzipping middleware returns perfect audio, just far too late.
"""
import gzip

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.selective_gzip import SelectiveGZipMiddleware, is_precompressed

MEDIA_CHUNK = b"\xff\xfb" + b"\x00" * 19_998
AUDIO = MEDIA_CHUNK * 10


async def _stream_text():
    for _ in range(4):
        yield b"chunk of prose, compressible. " * 100


async def _stream_media():
    # Enough to be well past any gzip buffer, delivered in small pieces.
    for _ in range(10):
        yield MEDIA_CHUNK


def build_app() -> Starlette:
    routes = [
        Route("/text", lambda request: StreamingResponse(_stream_text(), media_type="text/plain")),
        Route(
            "/audio",
            lambda request: StreamingResponse(_stream_media(), media_type="audio/mpeg"),
        ),
        Route("/json", lambda request: PlainTextResponse('{"ok": true}', media_type="application/json")),
        Route(
            "/encoded",
            lambda request: StreamingResponse(
                _stream_text(),
                media_type="text/plain",
                headers={"Content-Encoding": "br"},
            ),
        ),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000, compresslevel=9)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app())


class TestContentTypeDetection:
    @pytest.mark.parametrize(
        "content_type",
        ["audio/mpeg", "AUDIO/MPEG", "video/mp4", "image/png", "application/zip", "application/octet-stream"],
    )
    def test_compressed_media_is_recognised(self, content_type):
        assert is_precompressed(content_type) is True

    @pytest.mark.parametrize(
        "content_type",
        ["text/plain", "application/json", "text/html; charset=utf-8", "application/javascript"],
    )
    def test_text_is_still_compressible(self, content_type):
        assert is_precompressed(content_type) is False


class TestMediaBypassesCompression:
    def test_audio_is_sent_uncompressed(self, client):
        response = client.get("/audio", headers={"accept-encoding": "gzip"})

        assert response.status_code == 200
        assert "content-encoding" not in response.headers
        assert response.content == AUDIO

    def test_the_first_chunk_is_forwarded_without_buffering(self):
        """The property TestClient cannot show: a chunk leaves as soon as it arrives.

        TestClient collects the whole body before the caller sees any of it, so the
        "did it wait?" question is asked here at the ASGI level instead, where the
        order of send() calls is observable.
        """
        import anyio

        seen = []

        async def send(message):
            seen.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def app(scope, receive_, send_):
            await send_(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"audio/mpeg")],
                }
            )
            await send_({"type": "http.response.body", "body": MEDIA_CHUNK, "more_body": True})
            forwarded_so_far = [m for m in seen if m["type"] == "http.response.body"]
            assert forwarded_so_far, "the first media chunk was held back"
            assert forwarded_so_far[0]["body"] == MEDIA_CHUNK
            await send_({"type": "http.response.body", "body": b"", "more_body": False})

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/audio",
            "headers": [(b"accept-encoding", b"gzip")],
        }
        middleware = SelectiveGZipMiddleware(app, minimum_size=1000)

        anyio.run(lambda: middleware(scope, receive, send))

        assert seen[0]["headers"] == [(b"content-type", b"audio/mpeg")], "no gzip header added"
        assert b"".join(
            m.get("body", b"") for m in seen if m["type"] == "http.response.body"
        ) == MEDIA_CHUNK, "every byte arrived, untouched and in order"

    def test_a_range_request_is_not_gzipped(self, client):
        response = client.get("/audio", headers={"accept-encoding": "gzip", "range": "bytes=0-99"})

        assert "content-encoding" not in response.headers


class TestTextIsStillCompressed:
    def test_a_streaming_text_body_is_gzipped(self, client):
        response = client.get("/text", headers={"accept-encoding": "gzip"})

        assert response.headers["content-encoding"] == "gzip"
        # TestClient decodes transparently, so the body reads as prose again.
        assert b"compressible" in response.content

    def test_a_small_response_is_left_alone(self, client):
        response = client.get("/json", headers={"accept-encoding": "gzip"})

        assert "content-encoding" not in response.headers

    def test_a_client_without_gzip_gets_plain_bytes(self, client):
        response = client.get("/text", headers={"accept-encoding": "identity"})

        assert "content-encoding" not in response.headers
        assert b"compressible" in response.content

    def test_an_already_encoded_response_is_left_alone(self, client):
        response = client.get("/encoded", headers={"accept-encoding": "gzip"})

        assert response.headers.get("content-encoding") == "br"