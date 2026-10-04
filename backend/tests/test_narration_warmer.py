"""
Hermetic tests for the narration warmer.

The warmer exists because local speech synthesis runs at roughly half real time:
without it the first press of Listen waits for the whole clip. These tests pin the
behaviour that makes pre-warming worthwhile — it skips what is already cached, it
never synthesises the same item twice, it leaves already-warm items alone, and it
survives a broken model, a dead Redis or a failing query without taking the API
down or surfacing an error to a listener.
"""
import asyncio
import uuid

import pytest

from app.services import narration_warmer as warmer_module
from app.services.narration_warmer import NarrationWarmer


class _Recorder:
    """Stands in for the global TTSService singleton."""

    def __init__(self, configured=True, error=None):
        self._configured = configured
        self._error = error
        self.narrated = []
        self.busy = False

    @property
    def configured(self):
        return self._configured

    def cache_key(self, text):
        return f"tts:local:model:voice:{text}"

    async def narrate(self, text):
        if self._error is not None:
            raise self._error
        self.narrated.append(text)
        return b"\xff\xf3" + b"\x00" * 32, "audio/mpeg"

    async def warm_narration(self, text):
        """Mirrors the real service: opportunistic, and None when the CPU is busy."""
        if self._error is not None:
            raise self._error
        if self.busy:
            return None
        return await self.narrate(text)


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(warmer_module, "tts_service", rec)
    monkeypatch.setattr(warmer_module.settings, "TTS_PREWARM_ENABLED", True)
    monkeypatch.setattr(warmer_module.settings, "TTS_PREWARM_ITEM_DELAY_SECONDS", 0.0)
    return rec


@pytest.fixture
def cache(monkeypatch):
    store = {}

    class _Cache:
        async def get(self, key):
            return store.get(key)

        async def set(self, key, value, ex=None):
            store[key] = value
            return True

    monkeypatch.setattr(warmer_module, "cache_service", _Cache())
    return store


def _patch_text(monkeypatch, mapping):
    """Make _load_text return canned text for (kind, id) lookups."""
    text_by_item = mapping

    async def _load_text(self, kind, item_id):
        return text_by_item.get((kind, str(item_id)), "")

    monkeypatch.setattr(NarrationWarmer, "_load_text", _load_text)


class TestWarmOne:
    @pytest.mark.asyncio
    async def test_uncached_text_is_synthesised(self, recorder, cache, monkeypatch):
        article_id = uuid.uuid4()
        _patch_text(monkeypatch, {("article", str(article_id)): "Headline. Body."})
        warmer = NarrationWarmer()

        await warmer._warm_one("article", article_id)

        assert recorder.narrated == ["Headline. Body."]

    @pytest.mark.asyncio
    async def test_a_busy_cpu_defers_the_item(self, recorder, cache, monkeypatch):
        article_id = uuid.uuid4()
        _patch_text(monkeypatch, {("article", str(article_id)): "Headline. Body."})
        warmer = NarrationWarmer()
        recorder.busy = True

        await warmer._warm_one("article", article_id)

        assert recorder.narrated == []
        assert cache == {}, "a deferred item must not be treated as warmed"

    @pytest.mark.asyncio
    async def test_already_cached_text_is_skipped(self, recorder, cache, monkeypatch):
        article_id = uuid.uuid4()
        _patch_text(monkeypatch, {("article", str(article_id)): "Headline. Body."})
        warmer = NarrationWarmer()
        # The real cache key is what the warmer checks, so seed it exactly.
        cache[recorder.cache_key("Headline. Body.")] = "cached"

        await warmer._warm_one("article", article_id)

        assert recorder.narrated == []

    @pytest.mark.asyncio
    async def test_empty_text_is_not_synthesised(self, recorder, cache, monkeypatch):
        _patch_text(monkeypatch, {})
        warmer = NarrationWarmer()

        await warmer._warm_one("article", uuid.uuid4())

        assert recorder.narrated == []

    @pytest.mark.asyncio
    async def test_unconfigured_provider_does_nothing(self, monkeypatch, cache):
        rec = _Recorder(configured=False)
        monkeypatch.setattr(warmer_module, "tts_service", rec)
        warmer = NarrationWarmer()

        await warmer._warm_one("article", uuid.uuid4())

        assert rec.narrated == []

    @pytest.mark.asyncio
    async def test_very_long_text_is_left_to_the_play_button(self, recorder, cache, monkeypatch):
        """Warming a 20k-character article would burn minutes of CPU for nothing."""
        article_id = uuid.uuid4()
        _patch_text(monkeypatch, {("article", str(article_id)): "word " * 9000})
        monkeypatch.setattr(warmer_module.settings, "TTS_PREWARM_MAX_CHARS", 6000)
        warmer = NarrationWarmer()

        await warmer._warm_one("article", article_id)

        assert recorder.narrated == []

    @pytest.mark.asyncio
    async def test_synthesis_failure_is_logged_not_raised(self, monkeypatch, cache):
        rec = _Recorder(error=RuntimeError("model exploded"))
        monkeypatch.setattr(warmer_module, "tts_service", rec)
        article_id = uuid.uuid4()
        _patch_text(monkeypatch, {("article", str(article_id)): "Headline."})
        warmer = NarrationWarmer()

        # A failed warm-up must never propagate: the play button reports it instead.
        await warmer._warm_one("article", article_id)

        assert rec.narrated == []

    @pytest.mark.asyncio
    async def test_lookup_failure_is_logged_not_raised(self, recorder, cache, monkeypatch):
        async def _boom(self, kind, item_id):
            raise RuntimeError("database is down")

        monkeypatch.setattr(NarrationWarmer, "_load_text", _boom)
        warmer = NarrationWarmer()

        await warmer._warm_one("article", uuid.uuid4())

        assert recorder.narrated == []


class TestQueue:
    @pytest.mark.asyncio
    async def test_queued_article_is_warmed(self, recorder, cache, monkeypatch):
        article_id = uuid.uuid4()
        _patch_text(monkeypatch, {("article", str(article_id)): "Queued article."})
        warmer = NarrationWarmer()
        warmer._queue = asyncio.Queue()
        monkeypatch.setattr(warmer_module.settings, "TTS_PREWARM_STARTUP_DELAY_SECONDS", 0.0)
        monkeypatch.setattr(warmer_module.settings, "TTS_PREWARM_INTERVAL_SECONDS", 30.0)
        # The startup sweep is covered separately; keep this test on the queue path.
        monkeypatch.setattr(
            warmer_module.NarrationWarmer, "_recent", _recent_returning()
        )

        warmer.start()
        warmer.enqueue_article(article_id)
        # Let the worker pick the item up, then stop it.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if recorder.narrated:
                break
        await warmer.stop()

        assert recorder.narrated == ["Queued article."]

    @pytest.mark.asyncio
    async def test_enqueue_before_start_is_a_no_op(self, recorder, cache):
        warmer = NarrationWarmer()

        warmer.enqueue_article(uuid.uuid4())
        warmer.enqueue_event(uuid.uuid4())

        assert warmer._queue is None

    @pytest.mark.asyncio
    async def test_sweep_runs_at_startup_without_waiting_a_full_interval(
        self, recorder, cache, monkeypatch
    ):
        """A restart must re-warm the newest clips at once, not one interval later."""
        article_id = uuid.uuid4()
        monkeypatch.setattr(
            warmer_module.NarrationWarmer, "_recent", _recent_returning([article_id])
        )
        _patch_text(monkeypatch, {("article", str(article_id)): "Restart warmed."})
        monkeypatch.setattr(warmer_module.settings, "TTS_PREWARM_STARTUP_DELAY_SECONDS", 0.0)
        monkeypatch.setattr(warmer_module.settings, "TTS_PREWARM_INTERVAL_SECONDS", 900.0)
        warmer = NarrationWarmer()

        warmer.start()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if recorder.narrated:
                break
        await warmer.stop()

        assert recorder.narrated == ["Restart warmed."]

    @pytest.mark.asyncio
    async def test_start_is_idempotent_and_disabled_is_honoured(self, recorder, cache, monkeypatch):
        warmer = NarrationWarmer()
        monkeypatch.setattr(warmer_module.settings, "TTS_PREWARM_STARTUP_DELAY_SECONDS", 0.0)
        monkeypatch.setattr(warmer_module.NarrationWarmer, "_recent", _recent_returning())

        warmer.start()
        first_task = warmer._task
        warmer.start()
        assert warmer._task is first_task
        await warmer.stop()

        disabled = NarrationWarmer()
        monkeypatch.setattr(warmer_module.settings, "TTS_PREWARM_ENABLED", False)
        disabled.start()
        assert disabled._task is None
        await disabled.stop()


class TestSweep:
    @pytest.mark.asyncio
    async def test_sweep_warms_recent_items_and_caches_them(
        self, recorder, cache, monkeypatch
    ):
        article_id, event_id = uuid.uuid4(), uuid.uuid4()
        monkeypatch.setattr(
            warmer_module.NarrationWarmer, "_recent", _recent_returning([article_id], [event_id])
        )
        _patch_text(
            monkeypatch,
            {
                ("article", str(article_id)): "Newest article.",
                ("event", str(event_id)): "Newest event.",
            },
        )
        warmer = NarrationWarmer()

        await warmer._sweep()

        assert sorted(recorder.narrated) == ["Newest article.", "Newest event."]

    @pytest.mark.asyncio
    async def test_second_sweep_does_no_work(self, recorder, cache, monkeypatch):
        """The point of the sweep is to run once, not on every timer tick."""
        article_id = uuid.uuid4()
        monkeypatch.setattr(
            warmer_module.NarrationWarmer, "_recent", _recent_returning([article_id])
        )
        _patch_text(monkeypatch, {("article", str(article_id)): "Newest article."})
        warmer = NarrationWarmer()

        # Seed the cache the way narrate() would, since the fake recorder does not.
        cache[recorder.cache_key("Newest article.")] = "cached"
        await warmer._sweep()

        assert recorder.narrated == []

    @pytest.mark.asyncio
    async def test_sweep_survives_a_failing_query(self, recorder, cache, monkeypatch):
        async def _boom(self, batch):
            raise RuntimeError("database is down")

        monkeypatch.setattr(warmer_module.NarrationWarmer, "_recent", _boom)
        warmer = NarrationWarmer()

        await warmer._sweep()

        assert recorder.narrated == []


def _recent_returning(article_ids=(), event_ids=()):
    """Build a stub _recent() that hands the sweep fixed id lists."""

    async def _recent(self, batch):
        return (list(article_ids), list(event_ids))

    return _recent