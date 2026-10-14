"""
GlobeLens AI — Tests for the DRAFT-event enrichment schedule.

Clustering creates an event per unmatched cluster with importance_score=0.0 and
status DRAFT. Only LLMService.process_pending_events ever replaces that
placeholder, and until this schedule existed nothing called it, so every
clustered event stayed unscored and was filtered out of the API by min_score.

These tests pin the wiring rather than the LLM: that beat schedules the task,
that the task is registered, and that the loop drains a backlog and stops
instead of spinning on unprocessable rows.
"""
import asyncio
from pathlib import Path

import pytest

from app.core.config import settings
from app.worker import celery_app as celery_module
from app.worker import tasks as worker_tasks
from app.worker.tasks import enrich_pending_events_task, generate_illustrations_task


class _FakeLock:
    def __init__(self, held: bool = False):
        self.held = held
        self.deleted: list[str] = []
        self.ttl: int | None = None
        # Every SET with a TTL, so a test can see the lease being re-armed.
        self.renewals: list[tuple[str, object, object]] = []

    async def set(self, key, value, nx=False, ex=None):
        if nx and self.held:
            return False
        if not nx:
            self.renewals.append((key, value, ex))
        self.held = True
        self.ttl = ex
        return True

    async def delete(self, key):
        self.deleted.append(key)
        self.held = False
        return 1

    async def aclose(self):
        return None


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Keep the enrichment tests off a live Redis.

    Yields the fake so a test can pre-mark the lock as held; by default the lock
    is free, which is the normal path.
    """
    lock = _FakeLock()
    monkeypatch.setattr(
        "app.worker.tasks.aioredis.from_url", lambda *a, **k: lock
    )
    return lock


class TestBeatSchedule:
    def test_enrichment_is_scheduled(self):
        """The bug this guards: enrichment had no beat entry, so DRAFT events
        were never scored."""
        schedule = celery_module.celery_app.conf.beat_schedule
        assert "enrich-pending-events" in schedule

    def test_enrichment_entry_points_at_the_right_task(self):
        entry = celery_module.celery_app.conf.beat_schedule["enrich-pending-events"]
        assert entry["task"] == "app.worker.tasks.enrich_pending_events_task"

    def test_interval_comes_from_settings(self):
        entry = celery_module.celery_app.conf.beat_schedule["enrich-pending-events"]
        assert entry["schedule"] == float(settings.LLM_ENRICH_INTERVAL_MINUTES * 60)


class TestIllustrationSchedule:
    """Without this entry nothing ever generates an image: enrichment finishes,
    and the artwork waits until someone hands-enqueues a batch."""

    def test_artwork_is_scheduled(self):
        schedule = celery_module.celery_app.conf.beat_schedule
        assert "illustrate-new-events" in schedule

    def test_entry_points_at_the_drain_task(self):
        entry = celery_module.celery_app.conf.beat_schedule["illustrate-new-events"]
        assert entry["task"] == "app.worker.tasks.generate_illustrations_task"

    def test_interval_comes_from_settings(self):
        entry = celery_module.celery_app.conf.beat_schedule["illustrate-new-events"]
        assert entry["schedule"] == float(settings.ILLUSTRATION_INTERVAL_MINUTES * 60)

    def test_interval_is_positive(self):
        assert settings.ILLUSTRATION_INTERVAL_MINUTES > 0

    def test_task_is_registered(self):
        assert "app.worker.tasks.generate_illustrations_task" in celery_module.celery_app.tasks

    def test_disabled_settings_still_exits_the_loop(self, monkeypatch):
        """A scheduled run with the provider switched off must settle in one
        pass rather than re-arming every interval. The service returns a zero
        tally when disabled, so the drain loop breaks without touching the
        provider — assert that rather than letting it reach a real one."""
        monkeypatch.setattr(settings, "ILLUSTRATION_ENABLED", False)
        result = generate_illustrations_task.run()
        assert result["generated"] == 0
        assert result["batches"] == 1
        # The key only appears on the skip path, so its absence proves the task
        # actually ran rather than bailing out at the lock.
        assert "already_running" not in result

    def test_existing_schedules_survived(self):
        schedule = celery_module.celery_app.conf.beat_schedule
        for name in (
            "newsletter-daily-brief",
            "newsletter-weekly-deep-dive",
            "push-daily-briefing",
        ):
            assert name in schedule


class TestTaskRegistration:
    def test_task_is_registered_under_its_name(self):
        assert "app.worker.tasks.enrich_pending_events_task" in (
            celery_module.celery_app.tasks
        )

    def test_registered_task_resolves_to_our_function(self):
        registered = celery_module.celery_app.tasks[
            "app.worker.tasks.enrich_pending_events_task"
        ]
        assert registered.run.__name__ == enrich_pending_events_task.__name__


class TestEnrichmentLoop:
    def test_disabled_short_circuits(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_ENRICH_ENABLED", False)
        result = enrich_pending_events_task.run()
        assert result["processed"] == 0
        assert result["skipped"] == "disabled"

    def test_loops_until_the_backlog_is_drained(self, monkeypatch):
        """A single pass is capped at LLM_ENRICH_BATCH_SIZE, so a scraper burst
        needs several passes. Without the loop the backlog only shrinks by 20
        per cycle."""
        monkeypatch.setattr(settings, "LLM_ENRICH_ENABLED", True)
        monkeypatch.setattr(settings, "LLM_ENRICH_MAX_BATCHES", 10)
        monkeypatch.setattr(settings, "LLM_ENRICH_SLEEP_SECONDS", 0.0)
        monkeypatch.setattr(settings, "LLM_ENRICH_BATCH_SIZE", 20)

        passes = []

        class FakeService:
            async def process_pending_events(self, limit=20):
                passes.append(limit)
                # Three productive passes, then the backlog is empty.
                if len(passes) <= 3:
                    return 20, 0
                return 0, 0

        monkeypatch.setattr(
            "app.worker.tasks.LLMService", lambda *a, **k: FakeService()
        )
        result = enrich_pending_events_task.run()

        assert result["processed"] == 60
        assert result["batches"] == 4
        assert passes == [20, 20, 20, 20]

    def test_stops_on_a_pass_that_scores_nothing(self, monkeypatch):
        """Rows with no article content can never be enriched. Looping on them
        would burn LLM_ENRICH_MAX_BATCHES passes for zero progress."""
        monkeypatch.setattr(settings, "LLM_ENRICH_ENABLED", True)
        monkeypatch.setattr(settings, "LLM_ENRICH_MAX_BATCHES", 10)
        monkeypatch.setattr(settings, "LLM_ENRICH_SLEEP_SECONDS", 0.0)

        passes = []

        class StuckService:
            async def process_pending_events(self, limit=20):
                passes.append(limit)
                return 0, 0

        monkeypatch.setattr(
            "app.worker.tasks.LLMService", lambda *a, **k: StuckService()
        )
        result = enrich_pending_events_task.run()

        assert result["batches"] == 1
        assert len(passes) == 1

    def test_respects_max_batches_on_a_perpetual_backlog(self, monkeypatch):
        """A cap has to exist, or a backlog that never shrinks holds a worker
        open indefinitely."""
        monkeypatch.setattr(settings, "LLM_ENRICH_ENABLED", True)
        monkeypatch.setattr(settings, "LLM_ENRICH_MAX_BATCHES", 3)
        monkeypatch.setattr(settings, "LLM_ENRICH_SLEEP_SECONDS", 0.0)

        class EndlessService:
            async def process_pending_events(self, limit=20):
                return 20, 0

        monkeypatch.setattr(
            "app.worker.tasks.LLMService", lambda *a, **k: EndlessService()
        )
        result = enrich_pending_events_task.run()

        assert result["batches"] == 3
        assert result["processed"] == 60

    def test_errors_alone_do_not_stop_the_loop(self, monkeypatch):
        """A provider hiccup on one event should not abandon the rest."""
        monkeypatch.setattr(settings, "LLM_ENRICH_ENABLED", True)
        monkeypatch.setattr(settings, "LLM_ENRICH_MAX_BATCHES", 5)
        monkeypatch.setattr(settings, "LLM_ENRICH_SLEEP_SECONDS", 0.0)

        passes = []

        class FlakyService:
            async def process_pending_events(self, limit=20):
                passes.append(limit)
                if len(passes) == 1:
                    return 0, 5
                return 0, 0

        monkeypatch.setattr(
            "app.worker.tasks.LLMService", lambda *a, **k: FlakyService()
        )
        result = enrich_pending_events_task.run()

        assert result["errors"] == 5
        assert result["batches"] == 2


class TestSingletonGuard:
    """Beat is interval-based; on a CPU-served model one pass outlasts the
    interval, so without a lock every tick stacks another run and the backlog
    compounds."""

    def test_skips_when_lock_is_held(self, monkeypatch, fake_redis):
        monkeypatch.setattr(settings, "LLM_ENRICH_ENABLED", True)
        fake_redis.held = True

        def explode(*a, **k):
            raise AssertionError("must not process while another run holds the lock")

        monkeypatch.setattr("app.worker.tasks.LLMService", explode)
        result = enrich_pending_events_task.run()

        assert result["skipped"] == "already_running"
        assert result["processed"] == 0

    def test_processes_and_releases_when_lock_is_free(self, monkeypatch, fake_redis):
        monkeypatch.setattr(settings, "LLM_ENRICH_ENABLED", True)
        monkeypatch.setattr(settings, "LLM_ENRICH_MAX_BATCHES", 2)
        monkeypatch.setattr(settings, "LLM_ENRICH_SLEEP_SECONDS", 0.0)

        class Service:
            async def process_pending_events(self, limit=20):
                return 5, 0

        monkeypatch.setattr(
            "app.worker.tasks.LLMService", lambda *a, **k: Service()
        )
        result = enrich_pending_events_task.run()

        assert result["processed"] == 10
        # Leaving the lock set would block every future run until the TTL expired.
        assert fake_redis.deleted == ["globelens:llm_enrich:lock"]

    def test_lock_is_released_even_when_processing_raises(self, monkeypatch, fake_redis):
        monkeypatch.setattr(settings, "LLM_ENRICH_ENABLED", True)

        class Boom:
            async def process_pending_events(self, limit=20):
                raise RuntimeError("llm exploded")

        monkeypatch.setattr("app.worker.tasks.LLMService", lambda *a, **k: Boom())

        with pytest.raises(RuntimeError):
            enrich_pending_events_task.run()

        assert fake_redis.deleted == ["globelens:llm_enrich:lock"]

    def test_lock_is_acquired_with_a_ttl(self, monkeypatch, fake_redis):
        # Without an expiry a crashed worker would wedge enrichment forever.
        monkeypatch.setattr(settings, "LLM_ENRICH_ENABLED", True)
        monkeypatch.setattr(settings, "LLM_ENRICH_MAX_BATCHES", 1)
        monkeypatch.setattr(settings, "LLM_ENRICH_SLEEP_SECONDS", 0.0)

        class Service:
            async def process_pending_events(self, limit=20):
                return 0, 0

        monkeypatch.setattr(
            "app.worker.tasks.LLMService", lambda *a, **k: Service()
        )
        enrich_pending_events_task.run()

        assert fake_redis.ttl == settings.LLM_ENRICH_LOCK_TTL_SECONDS


class TestIllustrationSingletonGuard:
    """A backfill enqueues many batches and the worker runs one process per core,
    so unguarded they all run at once and thrash the shared local LLM."""

    def test_skips_when_a_batch_is_already_running(self, monkeypatch, fake_redis):
        fake_redis.held = True

        def explode(*a, **k):
            raise AssertionError("must not start a parallel batch")

        monkeypatch.setattr("app.worker.tasks.IllustrationService", explode)
        result = generate_illustrations_task.run()

        assert result["already_running"] is True
        assert result["generated"] == 0

    def test_runs_and_releases_when_free(self, monkeypatch, fake_redis):
        monkeypatch.setattr(settings, "ILLUSTRATION_SLEEP_SECONDS", 0.0)

        class FakeService:
            def __init__(self):
                self.calls = 0

            async def process_batch(self, limit=None):
                self.calls += 1
                if self.calls == 1:
                    return {"generated": 7, "failed": 0, "skipped": 0}
                return {"generated": 0, "failed": 0, "skipped": 0}

        monkeypatch.setattr(
            "app.worker.tasks.IllustrationService", lambda *a, **k: FakeService()
        )
        result = generate_illustrations_task.run()

        assert result["generated"] == 7
        assert fake_redis.deleted == ["globelens:illustration:lock"]

    def test_lock_is_released_on_failure(self, monkeypatch, fake_redis):
        class Boom:
            async def process_batch(self, limit=None):
                raise RuntimeError("provider down")

        monkeypatch.setattr("app.worker.tasks.IllustrationService", lambda *a, **k: Boom())

        with pytest.raises(RuntimeError):
            generate_illustrations_task.run()

        assert fake_redis.deleted == ["globelens:illustration:lock"]

    def test_lock_ttl_is_positive(self):
        assert settings.ILLUSTRATION_LOCK_TTL_SECONDS > 0


class TestIllustrationDrainLoop:
    """A backfill must be one looping task, not many racing the lock: eleven of
    twelve tasks no-op'd while a single worker did all the work."""

    def test_loops_until_nothing_is_generated(self, monkeypatch, fake_redis):
        monkeypatch.setattr(settings, "ILLUSTRATION_SLEEP_SECONDS", 0.0)
        calls = []

        class Service:
            async def process_batch(self, limit=None):
                calls.append(limit)
                if len(calls) <= 3:
                    return {"generated": 20, "failed": 0, "skipped": 0}
                return {"generated": 0, "failed": 0, "skipped": 0}

        monkeypatch.setattr(
            "app.worker.tasks.IllustrationService", lambda *a, **k: Service()
        )
        result = generate_illustrations_task.run()

        assert result["generated"] == 60
        assert result["batches"] == 4

    def test_stops_when_everything_fails(self, monkeypatch, fake_redis):
        """A provider outage must not burn the whole ceiling of batches."""
        monkeypatch.setattr(settings, "ILLUSTRATION_SLEEP_SECONDS", 0.0)
        calls = []

        class Service:
            async def process_batch(self, limit=None):
                calls.append(limit)
                return {"generated": 0, "failed": 20, "skipped": 0}

        monkeypatch.setattr(
            "app.worker.tasks.IllustrationService", lambda *a, **k: Service()
        )
        result = generate_illustrations_task.run()

        assert len(calls) == 1
        assert result["failed"] == 20

    def test_respects_max_batches_ceiling(self, monkeypatch, fake_redis):
        monkeypatch.setattr(settings, "ILLUSTRATION_SLEEP_SECONDS", 0.0)

        class Endless:
            async def process_batch(self, limit=None):
                return {"generated": 20, "failed": 0, "skipped": 0}

        monkeypatch.setattr(
            "app.worker.tasks.IllustrationService", lambda *a, **k: Endless()
        )
        result = generate_illustrations_task.run(max_batches=3)

        assert result["batches"] == 3
        assert result["generated"] == 60

    def test_aggregates_counts_across_batches(self, monkeypatch, fake_redis):
        monkeypatch.setattr(settings, "ILLUSTRATION_SLEEP_SECONDS", 0.0)
        calls = []

        class Service:
            async def process_batch(self, limit=None):
                calls.append(limit)
                if len(calls) == 1:
                    return {"generated": 20, "failed": 1, "skipped": 2}
                return {"generated": 0, "failed": 0, "skipped": 0}

        monkeypatch.setattr(
            "app.worker.tasks.IllustrationService", lambda *a, **k: Service()
        )
        result = generate_illustrations_task.run()

        assert result["failed"] == 1
        assert result["skipped"] == 2

    def test_rearms_the_lease_between_batches(self, monkeypatch, fake_redis):
        """A free-provider batch can outlast a fixed TTL — one image a minute
        makes twenty of them take half an hour — so the lock has to be renewed
        per batch or it lapses mid-task and lets a second task run alongside."""
        monkeypatch.setattr(settings, "ILLUSTRATION_SLEEP_SECONDS", 0.0)
        calls = []

        class Service:
            async def process_batch(self, limit=None):
                calls.append(limit)
                if len(calls) <= 2:
                    return {"generated": 20, "failed": 0, "skipped": 0}
                return {"generated": 0, "failed": 0, "skipped": 0}

        monkeypatch.setattr(
            "app.worker.tasks.IllustrationService", lambda *a, **k: Service()
        )
        generate_illustrations_task.run()

        renewals = [r for r in fake_redis.renewals if r[0] == "globelens:illustration:lock"]
        # Two batches that generated, so the lease is re-armed after each; the
        # third generated nothing and broke out without renewing.
        assert len(renewals) == 2
        assert all(r[2] == settings.ILLUSTRATION_LOCK_TTL_SECONDS for r in renewals)

    def test_ttl_exceeds_a_single_batch_at_the_free_quota(self):
        """Sanity check the lease against the slowest provider in play: a
        free batch is ~20 images at about a minute each."""
        per_image = settings.ILLUSTRATION_FREE_INTERVAL_SECONDS
        batch_seconds = settings.ILLUSTRATION_BATCH_LIMIT * per_image
        assert settings.ILLUSTRATION_LOCK_TTL_SECONDS > batch_seconds


class TestPassLimitIsPlumbed:
    def test_process_pending_events_honours_limit(self):
        """The service used to hardcode limit=20, so the batch size could not be
        tuned without editing code."""
        import inspect

        from app.services.llm_service import LLMService

        sig = inspect.signature(LLMService.process_pending_events)
        assert "limit" in sig.parameters
        assert sig.parameters["limit"].default == 20


class TestProcessLoop:
    """Every task used to call asyncio.run(), which builds a loop per call and
    closes it on exit. The async engine is module-level and its pooled asyncpg
    connections belong to the loop that opened them, so the second task in a
    worker process got a connection from a dead loop and failed with "attached
    to a different loop". Beat scheduling artwork alongside enrichment is what
    made two different tasks share a process."""

    def test_reuses_one_loop_across_calls(self):
        seen = []

        async def probe():
            seen.append(asyncio.get_running_loop())

        worker_tasks._run_in_process_loop(probe())
        worker_tasks._run_in_process_loop(probe())

        assert len(seen) == 2
        assert seen[0] is seen[1]

    def test_a_failed_task_leaves_the_loop_usable(self):
        """A provider outage must not cost every later task in the process its
        loop — that would turn one transient failure into a broken worker."""

        async def boom():
            raise ValueError("provider down")

        with pytest.raises(ValueError):
            worker_tasks._run_in_process_loop(boom())

        async def probe():
            return asyncio.get_running_loop()

        loop = worker_tasks._run_in_process_loop(probe())
        assert loop is worker_tasks._PROCESS_LOOP
        assert not loop.is_closed()

    def test_no_task_builds_its_own_loop(self):
        """Pinned as an AST check rather than a text one: asyncio.run() is
        still mentioned in comments explaining why it is not used."""
        import ast

        tree = ast.parse(Path(worker_tasks.__file__).read_text(encoding="utf-8"))
        offenders = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "asyncio"
        ]
        assert offenders == []