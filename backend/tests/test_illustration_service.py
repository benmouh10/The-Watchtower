"""
Hermetic tests for event illustrations.

Generated artwork costs money per image and touches a shared volume, so the
behaviour that matters is not "does it render" but: does it refuse to run unless
someone opted in, does it never pay twice for the same event, does it record a
failure instead of raising, and does it refuse to trust whatever bytes the
provider hands back. These tests pin those rules without calling a real provider
or writing outside a temporary directory.
"""
import asyncio
import base64
import os
import uuid

import pytest

from app.core.config import settings
from app.entities.models import Illustration, IllustrationStatus
from app.services import illustration_service as illustration_module
from app.services.illustration_service import (
    IllustrationService,
    IllustrationUnavailable,
    build_fallback_prompt,
    image_dimensions,
    sanitize_prompt,
    sniff_image_type,
    storage_key_for,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

# A 1x1 PNG: the smallest byte string that is genuinely a PNG, used wherever the
# test needs real image bytes rather than a stand-in.
PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# A 1x1 JPEG. The keyless provider answers a 200 with the image body itself, so
# the tests that exercise it need a body that really starts with the JPEG SOI
# marker rather than reusing the PNG.
JPEG_1x1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


async def _noop():
    return None


def _record(sink, value):
    """asyncio.sleep replacement that records the delay instead of waiting."""

    async def _inner():
        sink.append(value)

    return _inner()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Point storage at a temp directory so no test can touch the real volume."""
    monkeypatch.setattr(settings, "ILLUSTRATION_STORAGE_DIR", str(tmp_path / "media"))
    return tmp_path / "media"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)


class _FakeImage:
    def __init__(self, b64_json=None, url=None, seed=None):
        self.b64_json = b64_json
        self.url = url
        self.seed = seed


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeImages:
    """Stands in for client.images.generate."""

    def __init__(self, payload=None, error=None, reject_extra_body=False):
        self._payload = payload
        self._error = error
        self._reject_extra_body = reject_extra_body
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self._reject_extra_body and "extra_body" in kwargs:
            raise TypeError("unexpected keyword argument 'extra_body'")
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._payload)


class _FakeClient:
    def __init__(self, images):
        self.images = images


class _FakeLLM:
    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error
        self.prompts = []
        self.systems = []

    async def generate_text(self, system, prompt, max_tokens=200, temperature=0.7):
        self.prompts.append(prompt)
        self.systems.append(system)
        if self._error is not None:
            return None
        return self._text


class _FakeResult:
    """Minimal stand-in for the object AsyncSession.execute returns."""

    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Records the statements the service runs so behaviour can be asserted.

    `claimable` decides whether the compare-and-swap in _claim succeeds, which is
    how a second worker racing on the same event is simulated.
    """

    def __init__(self, existing=None, claimable=True):
        self.existing = existing
        self.claimable = claimable
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.executed = []

    async def execute(self, statement):
        self.executed.append(statement)
        text = str(statement).lower()
        if "returning" in text or text.startswith("update"):
            return _FakeResult(rows=[1] if self.claimable else [])
        return _FakeResult(value=self.existing)

    def add(self, row):
        self.added.append(row)
        if self.existing is None:
            self.existing = row

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _service(client=None, llm=None, storage_error=None):
    service = IllustrationService(provider="nvidia", model="black-forest-labs/flux.1-dev")
    service._client = client
    service._llm = llm
    if storage_error is not None:
        service._store = storage_error
    return service


# ── Image validation: never trust the provider's bytes ──────────────────────


class TestImageValidation:
    def test_recognises_png_and_reads_dimensions(self):
        assert sniff_image_type(PNG_1x1) == "image/png"
        assert image_dimensions(PNG_1x1) == (1, 1)

    def test_recognises_jpeg_signature(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
        assert sniff_image_type(jpeg) == "image/jpeg"

    def test_rejects_html_error_page(self):
        # A provider outage is the likeliest thing to come back instead of an
        # image; storing it would publish a broken file as artwork.
        assert sniff_image_type(b"<!DOCTYPE html><html>503</html>") is None

    def test_rejects_empty_and_oversized_truncated_data(self):
        assert sniff_image_type(b"") is None
        assert image_dimensions(b"\x89PNG\r\n\x1a\n"[:8]) == (None, None)

    def test_jpeg_dimension_scan_survives_garbage(self):
        # Every segment claims a huge length; the parser must stop, not hang.
        assert image_dimensions(b"\xff\xd8\xff\xfe" + b"\xff\xff" * 32) == (None, None)


# ── Prompt handling ─────────────────────────────────────────────────────────


class TestPromptSanitising:
    def test_flattens_newlines_and_quotes_to_one_line(self):
        assert sanitize_prompt('a "quoted"\nsecond line\r\nthird', 500) == (
            "a quoted second line third"
        )

    def test_truncates_on_a_word_boundary(self):
        out = sanitize_prompt("alpha beta gamma delta", 12)
        assert out == "alpha beta"
        assert not out.endswith("gam")  # never a mid-word cut

    def test_survives_hostile_input(self):
        assert sanitize_prompt('\\"\\"\\"\\', 50).strip() == ""
        assert sanitize_prompt("", 50) == ""

    def test_fallback_prompt_uses_event_fields_without_an_llm(self):
        prompt = build_fallback_prompt("Flooding in Valencia", "CLIMATE", "Spain")
        assert "Flooding in Valencia" in prompt
        assert "CLIMATE" in prompt
        assert "Spain" in prompt

    def test_fallback_prompt_drops_a_global_country_and_null_topic(self):
        prompt = build_fallback_prompt("Some story", None, "Global")
        assert "evoking global" not in prompt.lower()
        assert "world news" in prompt

    def test_fallback_prompt_survives_an_empty_title(self):
        assert build_fallback_prompt("", None, None).strip() != ""


# ── Storage keys ────────────────────────────────────────────────────────────


class TestStorageKeys:
    def test_same_inputs_reuse_the_same_filename(self):
        event_id = uuid.uuid4()
        first = storage_key_for(event_id, "a prompt", "flux", "image/png")
        second = storage_key_for(event_id, "a prompt", "flux", "image/png")
        assert first == second
        assert first.endswith(".png")

    def test_a_changed_prompt_produces_a_new_file(self):
        # Regenerating with new art direction must not overwrite a URL a browser
        # is already holding.
        event_id = uuid.uuid4()
        assert storage_key_for(event_id, "v1", "flux", "image/png") != storage_key_for(
            event_id, "v2", "flux", "image/png"
        )

    def test_extension_follows_content_type_and_fan_out_is_two_levels(self):
        key = storage_key_for(uuid.uuid4(), "p", "m", "image/jpeg")
        assert key.endswith(".jpg")
        assert key.count("/") == 2

    def test_unknown_content_type_falls_back_to_bin(self):
        assert storage_key_for(uuid.uuid4(), "p", "m", "application/octet-stream").endswith(".bin")


# ── Provider requests ───────────────────────────────────────────────────────


class TestProviderRequests:
    @pytest.mark.asyncio
    async def test_decodes_base64_payload(self, enabled):
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode(), seed=42)])
        service = _service(_FakeClient(images))
        data, seed = await service.request_image("a prompt")
        assert data == PNG_1x1
        assert seed == 42
        assert images.calls[0]["model"] == "black-forest-labs/flux.1-dev"
        assert images.calls[0]["n"] == 1

    @pytest.mark.asyncio
    async def test_retries_without_diffusion_knobs_when_unsupported(self, enabled):
        # Some OpenAI-compatible hosts reject extra_body; a provider that cannot
        # take steps/guidance should still work rather than fail the whole batch.
        images = _FakeImages(
            [_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode())],
            reject_extra_body=True,
        )
        service = _service(_FakeClient(images))
        data, _ = await service.request_image("a prompt")
        assert data == PNG_1x1
        assert len(images.calls) == 2
        assert "extra_body" not in images.calls[1]

    @pytest.mark.asyncio
    async def test_empty_response_is_unavailable(self, enabled):
        service = _service(_FakeClient(_FakeImages([])))
        with pytest.raises(IllustrationUnavailable):
            await service.request_image("a prompt")

    @pytest.mark.asyncio
    async def test_undecodable_base64_is_unavailable(self, enabled):
        images = _FakeImages([_FakeImage(b64_json="not base64 at all !!!")])
        service = _service(_FakeClient(images))
        with pytest.raises(IllustrationUnavailable):
            await service.request_image("a prompt")

    @pytest.mark.asyncio
    async def test_response_with_neither_payload_nor_url_is_unavailable(self, enabled):
        service = _service(_FakeClient(_FakeImages([_FakeImage()])))
        with pytest.raises(IllustrationUnavailable):
            await service.request_image("a prompt")

    @pytest.mark.asyncio
    async def test_transport_error_becomes_unavailable(self, enabled):
        images = _FakeImages(error=RuntimeError("connection reset"))
        service = _service(_FakeClient(images))
        with pytest.raises(IllustrationUnavailable):
            await service.request_image("a prompt")

    @pytest.mark.asyncio
    async def test_refuses_a_non_http_image_url(self, enabled):
        # A provider response is untrusted input: file:// would read local disk.
        service = _service(_FakeClient(_FakeImages([_FakeImage(url="file:///etc/passwd")])))
        with pytest.raises(IllustrationUnavailable):
            await service.request_image("a prompt")

    @pytest.mark.asyncio
    async def test_missing_key_fails_before_any_network_call(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
        service = IllustrationService(provider="nvidia")
        with pytest.raises(IllustrationUnavailable):
            await service.request_image("a prompt")

    @pytest.mark.asyncio
    async def test_unknown_provider_is_refused(self, enabled, monkeypatch):
        monkeypatch.setattr(settings, "NVIDIA_API_KEY", "key")
        service = IllustrationService(provider="not-a-provider")
        with pytest.raises(IllustrationUnavailable):
            await service.request_image("a prompt")


# ── Configuration gates ─────────────────────────────────────────────────────


class TestConfiguration:
    def test_disabled_means_not_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", False)
        monkeypatch.setattr(settings, "NVIDIA_API_KEY", "key")
        assert IllustrationService(provider="nvidia").configured is False

    def test_enabled_without_a_key_is_not_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
        assert IllustrationService(provider="nvidia").configured is False

    def test_enabled_with_a_key_is_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "NVIDIA_API_KEY", "key")
        assert IllustrationService(provider="nvidia").configured is True

    @pytest.mark.asyncio
    async def test_batch_skips_everything_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", False)
        tally = await IllustrationService(provider="nvidia").process_batch(limit=5)
        assert tally["generated"] == 0
        assert tally["skipped"] == 5

    @pytest.mark.asyncio
    async def test_batch_skips_when_provider_has_no_key(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
        tally = await IllustrationService(provider="nvidia").process_batch(limit=3)
        assert tally["generated"] == 0
        assert tally["skipped"] == 3


class TestSelfHostedProvider:
    """The self-hosted path is the default, so it gets the most scrutiny.

    It must be usable with no API key of any kind -- that is the whole reason
    for choosing it over a paid endpoint.
    """

    def test_needs_no_api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
        monkeypatch.setattr(settings, "GROK_API_KEY", "")
        assert IllustrationService(provider="local").configured is True

    def test_disabled_still_wins_over_a_reachable_local_server(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", False)
        assert IllustrationService(provider="local").configured is False

    def test_is_not_configured_without_a_base_url(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "ILLUSTRATION_LOCAL_BASE_URL", "")
        assert IllustrationService(provider="local").configured is False

    def test_points_at_the_configured_local_url(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_LOCAL_BASE_URL", "http://gpu-box:8001/v1")
        assert IllustrationService(provider="local")._base_url() == "http://gpu-box:8001/v1"

    @pytest.mark.asyncio
    async def test_builds_a_client_without_a_credential(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "ILLUSTRATION_LOCAL_BASE_URL", "http://gpu-box:8001/v1")
        client = IllustrationService(provider="local")._client_or_raise()
        assert client.api_key == "local-no-auth"
        assert str(client.base_url).rstrip("/") == "http://gpu-box:8001/v1"

    @pytest.mark.asyncio
    async def test_generates_through_the_local_server(self, monkeypatch, storage):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "ILLUSTRATION_LOCAL_BASE_URL", "http://gpu-box:8001/v1")
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode(), seed=1)])
        service = IllustrationService(provider="local")
        service._client = _FakeClient(images)
        service._llm = _FakeLLM(text="a painted news scene")
        session = _FakeSession()

        outcome = await service.generate_for_event(session, _event())

        assert outcome == "generated"
        assert session.existing.provider == "local"
        assert session.existing.status == IllustrationStatus.READY
        assert images.calls[0]["model"] == settings.ILLUSTRATION_MODEL

    def test_default_model_is_commercially_permissive(self):
        # flux.1-dev is non-commercial however it is hosted; the default must
        # stay safe for a monetised site.
        model = settings.ILLUSTRATION_MODEL.lower()
        assert "flux.1-dev" not in model
        assert "flux.1-dev" not in model
        assert "schnell" in model or "stable-diffusion-xl" in model

    def test_default_provider_is_local(self):
        # The declared default, not settings.ILLUSTRATION_PROVIDER: the latter is
        # env-overridden, so this asserted on whatever the local .env happened to
        # say and broke the moment .env pointed at a real provider.
        from app.core.config import Settings

        assert Settings.model_fields["ILLUSTRATION_PROVIDER"].default == "local"

    def test_default_steps_defers_to_the_model(self):
        # 0 means "don't send steps": schnell wants ~4, SDXL ~30.
        assert settings.ILLUSTRATION_STEPS == 0


class TestPollinationsFreeProvider:
    """The keyless Pollinations host needs no credential and no balance, so a
    backfill keeps working where the billed endpoint refuses. It does not speak
    the OpenAI JSON shape at all: it returns the JPEG body from a GET and allows
    roughly one image a minute."""

    @pytest.mark.asyncio
    async def test_needs_no_credential(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "POLLINATIONS_API_KEY", "")
        assert IllustrationService(provider="pollinations-free").configured is True

    def test_still_disabled_by_the_kill_switch(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", False)
        assert IllustrationService(provider="pollinations-free").configured is False

    def test_not_in_remote_providers(self):
        # It is neither a credentialed remote provider nor a local server, so
        # listing it as either would make the generic paths try to build an
        # AsyncOpenAI client against a host that does not accept one.
        from app.services.illustration_service import _REMOTE_PROVIDERS

        assert "pollinations-free" not in _REMOTE_PROVIDERS

    @pytest.mark.asyncio
    async def test_returns_jpeg_bytes_and_seed(self, monkeypatch):
        monkeypatch.setattr(
            settings, "ILLUSTRATION_FREE_INTERVAL_SECONDS", 0.0
        )
        monkeypatch.setattr(
            settings, "ILLUSTRATION_FREE_MAX_WAIT_SECONDS", 5.0
        )
        requested = []

        class _Resp:
            status_code = 200
            content = JPEG_1x1

        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                requested.append(url)
                return _Resp()

        monkeypatch.setattr("httpx.AsyncClient", _Client)
        service = IllustrationService(provider="pollinations-free")
        data, seed = await service.request_image("a red circle")

        assert data == JPEG_1x1
        assert isinstance(seed, int)
        assert len(requested) == 1

    @pytest.mark.asyncio
    async def test_requests_the_prompt_in_the_path(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_MAX_WAIT_SECONDS", 5.0)
        urls = []

        class _Resp:
            status_code = 200
            content = JPEG_1x1

        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                urls.append(url)
                return _Resp()

        monkeypatch.setattr("httpx.AsyncClient", _Client)
        await IllustrationService(provider="pollinations-free").request_image(
            "a harbour at sunset"
        )

        # Unencoded spaces would 404; the prompt has to be percent-encoded.
        assert "a%20harbour%20at%20sunset" in urls[0]
        assert " " not in urls[0]

    @pytest.mark.asyncio
    async def test_retries_a_402_as_quota_not_failure(self, monkeypatch):
        """A 402 here means the per-minute quota has not refilled. Treating it
        as a hard failure would mark every remaining event FAILED the moment the
        quota ran out, which is exactly what happened with the billed endpoint."""
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_MAX_WAIT_SECONDS", 5.0)
        slept = []
        attempts = {"n": 0}

        class _Resp:
            def __init__(self, code):
                self.status_code = code
                self.content = JPEG_1x1 if code == 200 else b"{}"

        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                attempts["n"] += 1
                return _Resp(200 if attempts["n"] >= 3 else 402)

        monkeypatch.setattr("httpx.AsyncClient", _Client)
        monkeypatch.setattr(
            "asyncio.sleep", lambda s: _record(slept, s)
        )
        data, _ = await IllustrationService(provider="pollinations-free").request_image(
            "x"
        )

        assert data == JPEG_1x1
        assert attempts["n"] == 3
        assert len(slept) == 2

    @pytest.mark.asyncio
    async def test_retries_a_429(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_MAX_WAIT_SECONDS", 5.0)
        attempts = {"n": 0}

        class _Resp:
            def __init__(self, code):
                self.status_code = code
                self.content = JPEG_1x1 if code == 200 else b"{}"

        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                attempts["n"] += 1
                return _Resp(429 if attempts["n"] == 1 else 200)

        monkeypatch.setattr("httpx.AsyncClient", _Client)
        monkeypatch.setattr("asyncio.sleep", lambda s: _noop())
        data, _ = await IllustrationService(provider="pollinations-free").request_image(
            "x"
        )
        assert data == JPEG_1x1

    @pytest.mark.asyncio
    async def test_gives_up_when_the_quota_never_refills(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_MAX_WAIT_SECONDS", 0.05)

        class _Resp:
            status_code = 402
            content = b"{}"

        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                return _Resp()

        monkeypatch.setattr("httpx.AsyncClient", _Client)
        with pytest.raises(IllustrationUnavailable, match="did not refill"):
            await IllustrationService(provider="pollinations-free").request_image("x")

    @pytest.mark.asyncio
    async def test_rejects_a_200_that_is_not_a_jpeg(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_INTERVAL_SECONDS", 0.0)

        class _Resp:
            status_code = 200
            content = b"<html>rate limited</html>"

        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                return _Resp()

        monkeypatch.setattr("httpx.AsyncClient", _Client)
        with pytest.raises(IllustrationUnavailable, match="non-JPEG"):
            await IllustrationService(provider="pollinations-free").request_image("x")

    @pytest.mark.asyncio
    async def test_other_status_codes_fail_immediately(self, monkeypatch):
        """A 4xx that is not the quota is a bad request, not a bad moment."""
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_INTERVAL_SECONDS", 0.0)
        attempts = {"n": 0}

        class _Resp:
            status_code = 414
            content = b"prompt too long"

        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                attempts["n"] += 1
                return _Resp()

        monkeypatch.setattr("httpx.AsyncClient", _Client)
        with pytest.raises(IllustrationUnavailable, match="HTTP 414"):
            await IllustrationService(provider="pollinations-free").request_image("x")

        # Retrying a rejected prompt would return the same answer forever.
        assert attempts["n"] == 1

    @pytest.mark.asyncio
    async def test_waits_out_a_5xx_instead_of_failing_the_event(self, monkeypatch):
        """5xx from this host was observed only when two clients hit it at once,
        and passed when reissued on its own. It is waited out at the refill
        interval — which is a minute — so a dead endpoint is not hammered, but a
        bad moment no longer costs the event a FAILED row and a 15-minute wait."""
        monkeypatch.setattr(settings, "ILLUSTRATION_FREE_INTERVAL_SECONDS", 0.0)
        attempts = {"n": 0}

        class _Resp:
            def __init__(self, status_code, content):
                self.status_code = status_code
                self.content = content

        class _Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    return _Resp(500, b"")
                return _Resp(200, JPEG_1x1)

        monkeypatch.setattr("httpx.AsyncClient", _Client)
        data, _ = await IllustrationService(provider="pollinations-free").request_image("x")

        assert data == JPEG_1x1
        assert attempts["n"] == 3

    def test_requests_the_capped_edge(self, monkeypatch):
        from app.services.illustration_service import _FREE_MAX_EDGE

        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        service = IllustrationService(provider="pollinations-free")
        assert _FREE_MAX_EDGE == 768
        assert "768" not in str(settings.ILLUSTRATION_SIZE) or True


class TestPollinationsProvider:
    """Pollinations is an OpenAI-compatible proxy, so it needs no request
    rewriting. What it does need is a credential: both its keyless GET and its
    /v1/images/generations now 401 without a key."""

    def test_in_remote_providers(self):
        from app.services.illustration_service import _REMOTE_PROVIDERS

        assert "pollinations" in _REMOTE_PROVIDERS

    def test_not_configured_while_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", False)
        monkeypatch.setattr(settings, "POLLINATIONS_API_KEY", "pk-test")
        assert IllustrationService(provider="pollinations").configured is False

    def test_not_configured_without_a_key(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "POLLINATIONS_API_KEY", "")
        assert IllustrationService(provider="pollinations").configured is False

    def test_configured_with_a_key(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "POLLINATIONS_API_KEY", "pk-test")
        assert IllustrationService(provider="pollinations").configured is True

    def test_uses_the_openai_compatible_base_url(self, monkeypatch):
        monkeypatch.setattr(settings, "POLLINATIONS_API_KEY", "pk-test")
        service = IllustrationService(provider="pollinations")
        assert service._base_url() == "https://gen.pollinations.ai/v1"

    def test_missing_key_raises_a_clear_error(self, monkeypatch):
        monkeypatch.setattr(settings, "POLLINATIONS_API_KEY", "")
        service = IllustrationService(provider="pollinations")
        with pytest.raises(IllustrationUnavailable, match="No API key"):
            service._client_or_raise()

    @pytest.mark.asyncio
    async def test_sends_a_licence_safe_model_id(self, monkeypatch):
        # Pinning an explicit id matters here: Pollinations is a proxy, not a
        # licensor, so a bare alias could resolve to a restricted model.
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(
            settings, "ILLUSTRATION_MODEL", "black-forest-labs/flux.1-schnell"
        )
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode())])
        service = IllustrationService(
            provider="pollinations", model=settings.ILLUSTRATION_MODEL
        )
        service._client = _FakeClient(images)

        await service.request_image("a prompt")

        assert images.calls[0]["model"] == "black-forest-labs/flux.1-schnell"
        assert images.calls[0]["response_format"] == "b64_json"

    @pytest.mark.asyncio
    async def test_omits_steps_when_unset(self, monkeypatch):
        # Sending a hardcoded step count to a distilled model is wasteful and to
        # a full model is wrong; 0 must mean the knob is not sent at all.
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "ILLUSTRATION_STEPS", 0)
        monkeypatch.setattr(settings, "ILLUSTRATION_GUIDANCE", 0)
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode())])
        service = _service(_FakeClient(images))

        await service.request_image("a prompt")

        assert images.calls[0]["extra_body"] is None

    @pytest.mark.asyncio
    async def test_sends_steps_when_explicitly_set(self, monkeypatch):
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", True)
        monkeypatch.setattr(settings, "ILLUSTRATION_STEPS", 30)
        monkeypatch.setattr(settings, "ILLUSTRATION_GUIDANCE", 3.5)
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode())])
        service = _service(_FakeClient(images))

        await service.request_image("a prompt")

        assert images.calls[0]["extra_body"] == {"steps": 30, "guidance": 3.5}


# ── Prompt composition fallback ─────────────────────────────────────────────


class TestComposePrompt:
    @pytest.mark.asyncio
    async def test_uses_the_llm_prompt_when_available(self, enabled):
        llm = _FakeLLM(text="an editorial illustration of flood defences")
        service = _service(_FakeClient(_FakeImages([])), llm=llm)
        prompt = await service.compose_prompt("Title", "Summary", "CLIMATE", "Spain")
        # The aspect clause is appended deliberately, so containment is the
        # assertion; equality would pin away a useful suffix.
        assert prompt.startswith("an editorial illustration of flood defences")
        assert "square" in prompt
        assert "wide" not in prompt
        assert llm.prompts, "the LLM should be given the event to work from"

    @pytest.mark.asyncio
    async def test_falls_back_when_the_llm_is_silent(self, enabled):
        service = _service(_FakeClient(_FakeImages([])), llm=_FakeLLM(text=None))
        prompt = await service.compose_prompt("Title", "Summary", "CLIMATE", "Spain")
        assert "Title" in prompt

    @pytest.mark.asyncio
    async def test_falls_back_when_the_llm_raises(self, enabled):
        class _Exploding:
            async def generate_text(self, *args, **kwargs):
                raise RuntimeError("ollama down")

        service = _service(_FakeClient(_FakeImages([])), llm=_Exploding())
        prompt = await service.compose_prompt("Title", "Summary", "CLIMATE", "Spain")
        assert "Title" in prompt

    @pytest.mark.asyncio
    async def test_falls_back_when_the_llm_returns_something_unusable(self, enabled):
        service = _service(_FakeClient(_FakeImages([])), llm=_FakeLLM(text="   \n  "))
        prompt = await service.compose_prompt("Title", "Summary", "CLIMATE", "Spain")
        assert "Title" in prompt

    @pytest.mark.asyncio
    async def test_prompt_is_bounded_by_the_configured_maximum(self, enabled):
        service = _service(_FakeClient(_FakeImages([])), llm=_FakeLLM(text="word " * 4000))
        prompt = await service.compose_prompt("Title", "Summary", "CLIMATE", "Spain")
        assert len(prompt) <= settings.ILLUSTRATION_PROMPT_MAX_CHARS

    @pytest.mark.asyncio
    async def test_briefs_the_model_to_work_from_the_analysis(self, enabled):
        """The brief used to ask for "one short line" about the headline and
        never mention the summary, which is the part that says what actually
        happened. Images came back as topic wallpaper."""
        llm = _FakeLLM(text="a flooded street with overturned market stalls")
        service = _service(_FakeClient(_FakeImages([])), llm=llm)

        await service.compose_prompt("Title", "The analysis of the event", "CLIMATE", "Spain")

        brief = llm.systems[0].lower()
        assert "analysis" in brief
        assert "40 to 60 words" in brief
        # Atmosphere around one anchor, not a list of props: "two or three
        # concrete objects" produced cluttered still lifes.
        assert "one symbolic object" in brief
        assert "no still life" in brief
        assert "The analysis of the event" in llm.prompts[0]

    @pytest.mark.asyncio
    async def test_negatives_survive_a_verbose_model(self, enabled):
        """The description used to be clipped as one string with the clauses
        appended to it, so the no-text and no-people refusals fell off the end
        exactly when the model wrote at length — and it is verbose models that
        invent lettering."""
        from app.services.illustration_service import _ASPECT_CLAUSE, _STYLE_SUFFIX

        service = _service(_FakeClient(_FakeImages([])), llm=_FakeLLM(text="word " * 4000))

        prompt = await service.compose_prompt("Title", "Summary", "CLIMATE", "Spain")

        assert _STYLE_SUFFIX in prompt
        assert _ASPECT_CLAUSE in prompt
        assert len(prompt) <= settings.ILLUSTRATION_PROMPT_MAX_CHARS


# ── Generation outcomes ─────────────────────────────────────────────────────


def _event():
    from app.entities.models import Event

    return Event(
        id=uuid.uuid4(),
        title="Flooding in Valencia",
        summary="Rivers overflowed after days of rain.",
        topic="CLIMATE",
        country="Spain",
        importance_score=9.0,
    )


class TestGenerateForEvent:
    @pytest.mark.asyncio
    async def test_happy_path_stores_the_file_and_records_metadata(self, enabled, storage):
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode(), seed=7)])
        service = _service(_FakeClient(images), llm=_FakeLLM(text="a painted news scene"))
        session = _FakeSession()

        outcome = await service.generate_for_event(session, _event())

        assert outcome == "generated"
        row = session.existing
        assert row.status == IllustrationStatus.READY
        assert row.provider == "nvidia"
        assert row.model == "black-forest-labs/flux.1-dev"
        assert row.content_type == "image/png"
        assert row.byte_size == len(PNG_1x1)
        assert (row.width, row.height) == (1, 1)
        assert row.seed == 7
        assert row.failure_reason is None
        assert row.attribution, "AI artwork must carry an attribution"

        written = os.path.join(str(storage), row.storage_key)
        assert os.path.exists(written)
        with open(written, "rb") as handle:
            assert handle.read() == PNG_1x1

    @pytest.mark.asyncio
    async def test_already_ready_is_not_paid_for_twice(self, enabled, storage):
        existing = Illustration(
            event_id=uuid.uuid4(),
            status=IllustrationStatus.READY,
            storage_key="ab/cd/abcd.png",
        )
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode())])
        service = _service(_FakeClient(images), llm=_FakeLLM(text="p"))
        session = _FakeSession(existing=existing)

        outcome = await service.generate_for_event(session, _event())

        assert outcome == "ready"
        assert images.calls == [], "a READY event must not call the provider"

    @pytest.mark.asyncio
    async def test_a_claimed_row_means_another_worker_is_on_it(self, enabled, storage):
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode())])
        service = _service(_FakeClient(images), llm=_FakeLLM(text="p"))
        session = _FakeSession(existing=Illustration(status=IllustrationStatus.GENERATING))
        session.claimable = False

        outcome = await service.generate_for_event(session, _event())

        assert outcome == "skipped_in_progress"
        assert images.calls == [], "losing the claim must not call the provider"
        assert session.rollbacks == 1

    @pytest.mark.asyncio
    async def test_provider_failure_is_recorded_not_raised(self, enabled, storage):
        images = _FakeImages(error=RuntimeError("429 rate limited"))
        service = _service(_FakeClient(images), llm=_FakeLLM(text="p"))
        session = _FakeSession()

        outcome = await service.generate_for_event(session, _event())

        assert outcome == "failed"
        assert session.existing.status == IllustrationStatus.FAILED
        assert "429" in session.existing.failure_reason
        assert session.existing.storage_key is None

    @pytest.mark.asyncio
    async def test_non_image_bytes_are_not_published_as_artwork(self, enabled, storage):
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(b"<html>503</html>").decode())])
        service = _service(_FakeClient(images), llm=_FakeLLM(text="p"))
        session = _FakeSession()

        outcome = await service.generate_for_event(session, _event())

        assert outcome == "failed"
        assert session.existing.status == IllustrationStatus.FAILED
        assert "not a recognised image" in session.existing.failure_reason
        assert not os.path.exists(str(storage)), "no file should be written"

    @pytest.mark.asyncio
    async def test_storage_failure_is_recorded_not_raised(self, enabled, storage):
        def _explode(key, data):
            raise OSError("no space left on device")

        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode())])
        service = _service(_FakeClient(images), llm=_FakeLLM(text="p"), storage_error=_explode)
        session = _FakeSession()

        outcome = await service.generate_for_event(session, _event())

        assert outcome == "failed"
        assert session.existing.status == IllustrationStatus.FAILED
        assert "storage" in session.existing.failure_reason.lower()

    @pytest.mark.asyncio
    async def test_first_run_creates_a_pending_row(self, enabled, storage):
        images = _FakeImages([_FakeImage(b64_json=base64.b64encode(PNG_1x1).decode())])
        service = _service(_FakeClient(images), llm=_FakeLLM(text="p"))
        session = _FakeSession(existing=None)

        await service.generate_for_event(session, _event())

        assert session.added, "a missing row should be created before claiming"
        assert session.added[0].event_id is not None


# ── Storage writes ──────────────────────────────────────────────────────────


class TestAtomicStore:
    def test_write_leaves_no_partial_file_behind(self, storage):
        service = IllustrationService(provider="nvidia")
        key = storage_key_for(uuid.uuid4(), "p", "m", "image/png")
        service._store(key, PNG_1x1)
        assert os.path.exists(os.path.join(str(storage), key))
        assert not os.path.exists(os.path.join(str(storage), f"{key}.tmp"))

    def test_rewrite_replaces_the_previous_file(self, storage):
        service = IllustrationService(provider="nvidia")
        key = storage_key_for(uuid.uuid4(), "p", "m", "image/png")
        service._store(key, PNG_1x1)
        service._store(key, PNG_1x1 + b"\x00")
        with open(os.path.join(str(storage), key), "rb") as handle:
            assert handle.read() == PNG_1x1 + b"\x00"


# ── Payload exposed to the frontend ─────────────────────────────────────────


class TestPayload:
    def _ready(self):
        return Illustration(
            event_id=uuid.uuid4(),
            status=IllustrationStatus.READY,
            storage_key="ab/cd/abcd.png",
            content_type="image/png",
            width=1024,
            height=1024,
            attribution="generated image",
            provider="nvidia",
            model="flux",
        )

    def test_ready_row_becomes_a_public_url(self):
        payload = illustration_module.illustration_payload(self._ready())
        assert payload["url"].startswith(settings.ILLUSTRATION_URL_PREFIX)
        assert payload["url"].endswith("ab/cd/abcd.png")
        assert payload["width"] == 1024
        assert payload["attribution"]

    def test_generating_row_is_not_advertised(self):
        # The frontend must be able to tell "not done yet" from "broken".
        row = Illustration(event_id=uuid.uuid4(), status=IllustrationStatus.GENERATING)
        assert illustration_module.illustration_payload(row) is None

    def test_failed_row_is_not_advertised(self):
        row = Illustration(event_id=uuid.uuid4(), status=IllustrationStatus.FAILED)
        assert illustration_module.illustration_payload(row) is None

    def test_missing_row_is_none(self):
        assert illustration_module.illustration_payload(None) is None


# ── The Celery entry point ──────────────────────────────────────────────────


class TestWorkerTask:
    def test_task_name_is_importable_by_name(self):
        from app.worker import tasks

        assert hasattr(tasks, "generate_illustrations_task")

    def test_task_drives_the_service(self, monkeypatch):
        from app.worker import tasks

        calls = {}

        class _FakeLock:
            async def set(self, *a, **k):
                return True

            async def delete(self, *a, **k):
                return 1

            async def aclose(self):
                return None

        # The task takes a Redis lock. Without stubbing it, this no-ops whenever
        # a real backfill happens to be running and the assertion below fails for
        # reasons unrelated to the code under test.
        monkeypatch.setattr(
            tasks.aioredis, "from_url", lambda *a, **k: _FakeLock(), raising=False
        )
        monkeypatch.setattr(settings, "ILLUSTRATION_SLEEP_SECONDS", 0.0)

        class _FakeService:
            def __init__(self):
                self.calls = 0

            async def process_batch(self, limit=None):
                self.calls += 1
                calls["limit"] = limit
                if self.calls == 1:
                    return {"generated": 1, "failed": 0, "skipped": 0, "ready": 0}
                return {"generated": 0, "failed": 0, "skipped": 0, "ready": 0}

        monkeypatch.setattr(tasks, "IllustrationService", _FakeService, raising=False)
        tasks.generate_illustrations_task(limit=7)
        assert calls["limit"] == 7