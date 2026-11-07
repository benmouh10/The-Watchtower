"""Cron-driven background loop for the scheduled digest engine.

Started from the FastAPI lifespan only when DIGEST_ENABLED is true. Supports
the practical five-field subset of cron: `*`, single integers, `a,b` lists,
`a-b` ranges, and `*/n` steps, per field (minute, hour, day-of-month, month,
day-of-week). The loop wakes on aligned minute boundaries so a schedule never
drifts, and a run can never double-fire within the same minute.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set

from src.core.config import settings

logger = logging.getLogger("src.services.digest_scheduler")

MINUTE, HOUR, DOM, MONTH, DOW = range(5)


def parse_cron(expression: str) -> List[List[Set[int]]]:
    """Turn a 5-field cron expression into allowed values per field.

    Raises ValueError on a malformed expression so a bad DIGEST_CRON fails
    loudly at startup instead of silently never running.
    """
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(
            f"DIGEST_CRON must have 5 fields, got {len(fields)}: {expression!r}"
        )
    bounds = [
        (0, 59),   # minute
        (0, 23),   # hour
        (1, 31),   # day of month
        (1, 12),   # month
        (0, 6),    # day of week (Sunday = 0)
    ]
    parsed = []
    for index, field in enumerate(fields):
        low, high = bounds[index]
        values: Set[int] = set()
        for part in field.split(","):
            part = part.strip()
            if not part:
                raise ValueError(f"empty cron field: {expression!r}")
            step = 1
            if "/" in part:
                base, step_raw = part.split("/", 1)
                try:
                    step = int(step_raw)
                except ValueError:
                    raise ValueError(f"bad step in cron field {part!r}")
                if step < 1:
                    raise ValueError(f"bad step in cron field {part!r}")
                part = base
            if part == "*":
                start, end = low, high
            elif "-" in part:
                start_raw, end_raw = part.split("-", 1)
                try:
                    start, end = int(start_raw), int(end_raw)
                except ValueError:
                    raise ValueError(f"bad range in cron field {part!r}")
                if not (low <= start <= end <= high):
                    raise ValueError(f"range out of bounds in cron field {part!r}")
            else:
                try:
                    start = end = int(part)
                except ValueError:
                    raise ValueError(f"bad cron field part {part!r}")
                if not (low <= start <= high):
                    raise ValueError(f"value out of bounds in cron field {part!r}")
            values.update(range(start, end + 1, step))
        parsed.append(sorted(values))
    return parsed


def cron_matches(fields: List[List[Set[int]]], moment: datetime) -> bool:
    """True when `moment` falls on the schedule. Sunday = 0 in the DOW field."""
    # The cron DOW field says Sunday = 0 (Monday = 1, ...); datetime.weekday()
    # says Monday = 0 (Sunday = 6). (weekday() + 1) % 7 maps the two.
    dow = (moment.weekday() + 1) % 7
    return (
        moment.minute in fields[MINUTE]
        and moment.hour in fields[HOUR]
        and moment.day in fields[DOM]
        and moment.month in fields[MONTH]
        and dow in fields[DOW]
    )


def _parse_configured_cron(cron: Optional[str] = None) -> List[List[Set[int]]]:
    return parse_cron(cron or settings.DIGEST_CRON or "0 6 * * *")


async def _run_digest(frequency: str) -> None:
    """Generate a digest of `frequency` and enqueue its email/push deliveries."""
    from src.core.database import get_db
    from src.services.digest_service import DigestService

    async for db in get_db():
        try:
            digest = await DigestService(db).generate_digest(frequency=frequency)
            if digest is not None:
                logger.info(
                    "Scheduled %s digest %s generated with %s event(s)",
                    frequency, digest.id, digest.event_count,
                )
                try:
                    from src.services.newsletter_service import NewsletterService
                    enqueued = await NewsletterService(db).enqueue_digest(digest)
                    if enqueued:
                        logger.info(
                            "Scheduled %s digest %s queued to %s subscriber(s)",
                            frequency, digest.id, enqueued,
                        )
                except Exception as enqueue_exc:
                    logger.warning(
                        "Newsletter enqueue for %s digest %s failed: %s",
                        frequency, digest.id, enqueue_exc,
                    )
                try:
                    from src.services.push_service import PushService
                    pushes = await PushService(db).enqueue_digest(digest)
                    if pushes:
                        logger.info(
                            "Scheduled %s digest %s queued to %s device(s)",
                            frequency, digest.id, pushes,
                        )
                except Exception as push_exc:
                    logger.warning(
                        "Push enqueue for %s digest %s failed: %s",
                        frequency, digest.id, push_exc,
                    )
        finally:
            await db.close()
        break


async def _scheduler_loop(frequency: str, cron: str) -> None:
    """Tick on `cron`, firing the matching digest frequency, until cancelled."""
    fields = _parse_configured_cron(cron)
    logger.info(
        "%s digest scheduler enabled on %r",
        frequency.capitalize(), cron,
    )
    fired_key: Optional[str] = None
    while True:
        now = datetime.now(timezone.utc)
        next_aligned = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        await asyncio.sleep(max(1.0, (next_aligned - now).total_seconds()))

        moment = next_aligned
        key = moment.strftime("%Y-%m-%dT%H:%M")
        if key == fired_key or not cron_matches(fields, moment):
            fired_key = key
            continue

        fired_key = key
        logger.info("%s digest scheduler fire at %s", frequency.capitalize(), moment.isoformat())
        try:
            await _run_digest(frequency)
        except Exception as exc:
            logger.error("%s digest run failed", frequency.capitalize(), exc_info=exc)


async def run_digest_scheduler() -> None:
    """Run the configured digest schedules until cancelled.

    The daily comprehensive brief always follows DIGEST_CRON when DIGEST_ENABLED;
    the weekly deep-dive additionally follows DIGEST_WEEKLY_CRON when
    DIGEST_WEEKLY_ENABLED. Raises at startup on a malformed cron so the operator
    fixes it before the loops start; afterwards every per-run failure is logged
    and swallowed so one bad run cannot kill the schedule.
    """
    if not settings.DIGEST_ENABLED:
        logger.info("Digest scheduler disabled (DIGEST_ENABLED=false)")
        return

    loops = [_scheduler_loop("daily", settings.DIGEST_CRON or "0 6 * * *")]
    if getattr(settings, "DIGEST_WEEKLY_ENABLED", False):
        if not cron_matches(_parse_configured_cron(settings.DIGEST_WEEKLY_CRON), datetime.now(timezone.utc)):
            pass  # validity is checked inside _scheduler_loop at startup
        loops.append(_scheduler_loop("weekly", settings.DIGEST_WEEKLY_CRON or "0 7 * * 0"))

    await asyncio.gather(*loops)