"""Generated illustrations for events.

An illustration is decorative, never evidentiary: it is produced from an event's
own headline and summary, and it is labelled as a generated image wherever it is
shown. That is why nothing here reads article bodies, and why the prompt is
derived from public editorial fields only.

Three things this module is careful about:

* **Cost.** Image generation bills per image, so batches are bounded, events
  below an importance threshold are skipped rather than generated, and an event
  that already has a READY row is never re-billed. A row is claimed with a
  conditional UPDATE before any request goes out, so two workers racing over the
  same event cannot both pay for it.
* **Honesty of the record.** The prompt, the model that ran and an attribution
  line are persisted next to the file, so an editor can see afterwards what the
  image was asked to depict and which model produced it.
* **Not trusting the response.** The provider's declared content type is
  ignored in favour of sniffing the bytes, and nothing is written to disk until
  it is known to be an image. Prompt text from an LLM is sanitised before being
  handed to the image model, since a stray newline or quote changes the render
  and diffusion models reliably garble any lettering they are asked for.
"""
import asyncio
import base64
import binascii
import hashlib
import os
import struct
import time
import uuid
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import structlog
from openai import AsyncOpenAI
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.entities.models import Event, Illustration, IllustrationStatus

logger = structlog.get_logger()

# Sniffed rather than believed: a provider can label a PNG as JPEG, and the
# browser is the one that has to render it.
_SIGNATURES: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)

# Appended to every prompt, after the art direction and before the aspect
# clause. Diffusion models will happily render garbled lettering when asked for
# text, and a news illustration covered in fake headlines is worse than no
# illustration, so text and logos are refused explicitly rather than left to
# chance. Real people are refused too: a generated picture of a named official
# is a fabrication of something that was never photographed.
#
# It carries no positive style instruction on purpose. The art direction the
# model writes supplies the palette, light and mood per story; a house look
# stacked on top of that is what makes three hundred images look like one.
_STYLE_SUFFIX = (
    "editorial news illustration, not a photograph, "
    "no text, no lettering, no numbers, no logos, no watermarks, "
    "no flags, no recognisable public figures"
)

_ASPECT_BY_SHAPE = {
    "wide": "a wide horizontal composition, subject centred with space on both sides",
    "tall": "a tall vertical composition",
    "square": "a balanced square composition",
}

# Providers reachable over the network, each needing its own credential.
_REMOTE_PROVIDERS = ("openai", "grok", "nvidia", "pollinations")
# A self-hosted server on this machine or your own GPU box: same wire format,
# no API key, and no per-image bill. The licence still follows the weights you
# load, so hosting locally does not make a restricted model commercial.
_LOCAL_PROVIDER = "local"

# The keyless Pollinations image host: no credential and no per-image bill, but
# it answers with the JPEG body directly and rate-limits to roughly one image a
# minute, so it does not speak the OpenAI JSON shape at all.
_FREE_PROVIDER = "pollinations-free"

# The free host silently caps output here whatever size is requested, so asking
# for more just wastes response time.
_FREE_MAX_EDGE = 768


class IllustrationUnavailable(RuntimeError):
    """Raised when the configured provider cannot produce an image."""


def sniff_image_type(data: bytes) -> Optional[str]:
    """Return the image content type implied by the bytes, or None."""
    for signature, content_type in _SIGNATURES:
        if data.startswith(signature):
            return content_type
    # RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_dimensions(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    """Read pixel dimensions from the file header, without an image library."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if data.startswith(b"\xff\xd8"):
            # Walk the JPEG segment chain to the first SOF marker.
            pos = 2
            while pos + 9 < len(data):
                if data[pos] != 0xFF:
                    pos += 1
                    continue
                marker = data[pos + 1]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
                    return int(width), int(height)
                pos += 2 + struct.unpack(">H", data[pos + 2 : pos + 4])[0]
    except Exception:
        return None, None
    return None, None


def sanitize_prompt(text: str, max_chars: int) -> str:
    """Flatten LLM prose into a single image-prompt line.

    Newlines become spaces, quotes and backslashes are dropped so the prompt
    cannot break out of the provider's string handling, and runs of whitespace
    collapse. The result is truncated on a word boundary: a prompt clipped
    mid-word reads as a different instruction to the model.
    """
    cleaned = text.replace("\\", " ").replace('"', " ").replace("\n", " ").replace("\r", " ")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= max_chars:
        return cleaned
    clipped = cleaned[:max_chars]
    if " " in clipped:
        clipped = clipped[: clipped.rindex(" ")]
    return clipped.strip()


def build_fallback_prompt(title: str, topic: Optional[str], country: Optional[str]) -> str:
    """Art direction derived from the event's own fields, with no LLM involved.

    Used when the configured LLM is unavailable or returns something unusable.
    Better a plain prompt that renders than no illustration, and it keeps the
    feature working on a local Ollama setup where a coding model is the only
    model present.
    """
    subject = sanitize_prompt(title, 160) or "a world news story"
    where = sanitize_prompt(country, 60) if country and country.lower() != "global" else ""
    what = sanitize_prompt(topic, 60) if topic else "world news"
    parts = [f"{what} news illustration", f"depicting {subject}"]
    if where:
        parts.append(f"evoking {where} without depicting any specific landmark")
    return ", ".join(parts)


def storage_key_for(event_id: uuid.UUID, prompt: str, model: str, content_type: str) -> str:
    """A content-addressed filename: regenerating replaces, it does not accumulate.

    Keyed on the prompt and model as well as the event, so an intentional
    regeneration with different art direction produces a new file and leaves
    any browser holding the previous URL with a valid image.
    """
    extension = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/bmp": "bmp",
    }.get(content_type, "bin")
    digest = hashlib.sha256(f"{event_id}|{prompt}|{model}".encode("utf-8")).hexdigest()[:24]
    # Two-level fan-out keeps directory listings small once there are thousands
    # of events, without needing a real object store.
    return f"{digest[:2]}/{digest[2:4]}/{digest}.{extension}"


def illustration_url(illustration: Optional[Illustration]) -> Optional[str]:
    """Public URL for a READY illustration, or None when there is nothing to show."""
    if illustration is None or illustration.status != IllustrationStatus.READY:
        return None
    if not illustration.storage_key:
        return None
    return f"{settings.ILLUSTRATION_URL_PREFIX.rstrip('/')}/{illustration.storage_key}"


def illustration_payload(illustration: Optional[Illustration]) -> Optional[dict]:
    """The illustration block attached to event and article responses."""
    url = illustration_url(illustration)
    if url is None:
        return None
    return {
        "url": url,
        "width": illustration.width,
        "height": illustration.height,
        "attribution": illustration.attribution or settings.ILLUSTRATION_ATTRIBUTION,
        "provider": illustration.provider,
        "model": illustration.model,
    }


class IllustrationService:
    """Generates and stores one illustration per event."""

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None) -> None:
        self._provider = (provider or settings.ILLUSTRATION_PROVIDER or "").strip().lower()
        self._model = model or settings.ILLUSTRATION_MODEL
        self._client: Optional[AsyncOpenAI] = None
        # Built on first use so importing this module never opens a client.
        self._llm = None

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def configured(self) -> bool:
        """Whether generation could even be attempted.

        Checked before a batch starts so an operator gets one clear message
        instead of the same failure recorded against every event in the run.
        A self-hosted endpoint needs no credential, so only the remote providers
        require one.
        """
        if not settings.ILLUSTRATION_ENABLED:
            return False
        # Takes no credential, so configuredness is the whole check: there is
        # nothing for an operator to set up before it can be used.
        if self._provider == _FREE_PROVIDER:
            return True
        if self._provider == _LOCAL_PROVIDER:
            return bool(self._base_url())
        if self._provider in _REMOTE_PROVIDERS:
            return bool(self._credential())
        return False

    def _credential(self) -> Optional[str]:
        return {
            "openai": settings.OPENAI_API_KEY,
            "grok": settings.GROK_API_KEY,
            # Kept for completeness but unusable for images: NVIDIA's hosted
            # catalog serves no image model and its SDXL NIM is deprecated.
            "nvidia": settings.NVIDIA_API_KEY,
            "pollinations": settings.POLLINATIONS_API_KEY,
        }.get(self._provider)

    def _base_url(self) -> Optional[str]:
        return {
            "nvidia": settings.NVIDIA_API_URL,
            "grok": "https://api.x.ai/v1",
            # OpenAI-compatible /v1/images/generations, and the same shape this
            # service already speaks, so no request rewriting is needed.
            "pollinations": settings.POLLINATIONS_API_URL,
            _LOCAL_PROVIDER: settings.ILLUSTRATION_LOCAL_BASE_URL,
        }.get(self._provider)

    def _client_or_raise(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        if self._provider not in _REMOTE_PROVIDERS and self._provider != _LOCAL_PROVIDER:
            raise IllustrationUnavailable(f"Unsupported illustration provider {self._provider!r}")
        key = self._credential()
        if not key:
            if self._provider != _LOCAL_PROVIDER:
                raise IllustrationUnavailable(
                    f"No API key for illustration provider {self._provider!r}"
                )
            # A local server has no key, but the SDK refuses to construct a
            # client without a non-empty string. This value is never sent as a
            # credential anywhere; it only satisfies the constructor.
            key = "local-no-auth"
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=self._base_url(),
            timeout=float(settings.ILLUSTRATION_TIMEOUT_SECONDS),
        )
        return self._client

    async def compose_prompt(
        self,
        title: str,
        summary: Optional[str],
        topic: Optional[str],
        country: Optional[str],
    ) -> str:
        """Write art direction for an event, falling back to a template.

        The model is asked for a scene, not a description of the headline: naming
        real people or reproducing a headline verbatim produces an unusable
        image and, in the case of real people, a misleading one.

        It is given the enriched summary rather than just the headline because
        that summary is the analysis — the entities, the cause and the stake of
        the story — and it is what separates "a war" from a picture of the thing
        the war is actually about. Asked for one short line it returned a
        preamble and a paragraph about symbolism; asked for a specified
        structure of concrete detail it describes objects instead.
        """
        fallback = build_fallback_prompt(title, topic, country)
        try:
            llm = self._llm
            if llm is None:
                from app.services.llm_service import LLMService

                llm = LLMService()
                # Built once per service, not once per event: a batch would
                # otherwise open a fresh HTTP client for every image.
                self._llm = llm

            raw = await llm.generate_text(
                system=(
                    "You are an art director at an international news magazine. "
                    "You are given the headline, topic, country and the written "
                    "analysis of a news event. Read the analysis first: it holds "
                    "the objects, the cause and the stake of the story, and it "
                    "is what you illustrate.\n\n"
                    "From it choose the single most telling visual idea and "
                    "describe the picture that would carry it, as one line of "
                    "45 to 70 words containing: the scene and its setting; two "
                    "or three concrete objects or details taken from the "
                    "analysis; the light and the colour palette; and the mood. "
                    "Ground every element in something the analysis actually "
                    "says rather than illustrating the topic in general.\n\n"
                    "Refuse, without exception: any real person by name or by "
                    "face; any flag, logo or brand; and any writing of any "
                    "kind. Never describe an object that carries writing - a "
                    "sign, banner, placard, poster, screen, newspaper, book or "
                    "headline - because the picture is to contain no lettering "
                    "at all. People may appear only as silhouettes, from "
                    "behind, or at a distance; no close-up faces. Never state "
                    "that it is a photograph. Do not open with a label such as "
                    "'Illustration:' or 'Abstract editorial illustration:' - "
                    "begin with the scene itself. Reply with that one line and "
                    "nothing else, in English."
                ),
                prompt=(
                    f"Headline: {title}\n"
                    f"Topic: {topic or 'world news'}\n"
                    f"Country: {country or 'unspecified'}\n"
                    f"Analysis: {(summary or '')[:1500]}"
                ),
                # A 70-word line is ~450 characters, well past 120 tokens; the
                # old limit cut the description off in the middle of its
                # palette.
                max_tokens=300,
                temperature=0.6,
            )
        except Exception as err:  # a prompt failure must not fail the image
            logger.warn("Illustration prompt generation failed", error=str(err))
            raw = None

        cleaned = sanitize_prompt(raw, self._prompt_budget()) if raw else ""
        # A model that ignored the brief and handed back prose, a refusal, or a
        # single word makes a worse prompt than the template does.
        if len(cleaned) < 25 or cleaned.lower().startswith(("i cannot", "i can't", "sorry", "as an ai")):
            cleaned = fallback
        # The description is clipped to its budget on its own, then the fixed
        # clauses are appended after it. Clipping the assembled string instead
        # would drop those clauses off the end whenever the model was verbose,
        # which is precisely when the negatives are most needed.
        body = sanitize_prompt(cleaned, self._prompt_budget())
        return f"{body}, {_STYLE_SUFFIX}, {_ASPECT_BY_SHAPE['wide']}"

    @staticmethod
    def _prompt_budget() -> int:
        """Room left for the scene description once the fixed clauses are added."""
        suffix = 2 + len(_STYLE_SUFFIX) + 2 + len(_ASPECT_BY_SHAPE["wide"])
        return max(settings.ILLUSTRATION_PROMPT_MAX_CHARS - suffix, 60)

    async def request_image(self, prompt: str) -> Tuple[bytes, Optional[int]]:
        """Ask the provider for one image, returning (bytes, seed)."""
        if self._provider == _FREE_PROVIDER:
            return await self._request_free_image(prompt)
        client = self._client_or_raise()
        size = settings.ILLUSTRATION_SIZE
        # Only send the diffusion knobs when they are actually set. A distilled
        # model like FLUX.1-schnell wants ~4 steps and a full model wants ~30,
        # so ILLUSTRATION_STEPS=0 means "let the provider pick its own default"
        # rather than forcing one model's ideal onto another.
        knobs = {}
        if settings.ILLUSTRATION_STEPS > 0:
            knobs["steps"] = settings.ILLUSTRATION_STEPS
        if settings.ILLUSTRATION_GUIDANCE > 0:
            knobs["guidance"] = settings.ILLUSTRATION_GUIDANCE

        try:
            response = await client.images.generate(
                model=self._model,
                prompt=prompt,
                n=1,
                size=size,
                response_format="b64_json",
                extra_body=knobs or None,
            )
        except TypeError:
            # Not every OpenAI-compatible host accepts extra_body; a provider that
            # rejects the diffusion knobs should still be usable with defaults.
            response = await client.images.generate(
                model=self._model,
                prompt=prompt,
                n=1,
                size=size,
                response_format="b64_json",
            )
        except Exception as err:
            raise IllustrationUnavailable(f"Image provider call failed: {err}") from err

        if not getattr(response, "data", None):
            raise IllustrationUnavailable("Image provider returned no image data")

        first = response.data[0]
        seed = getattr(first, "seed", None)
        payload = getattr(first, "b64_json", None)
        if payload:
            try:
                return base64.b64decode(payload, validate=True), seed
            except (binascii.Error, ValueError) as err:
                raise IllustrationUnavailable(f"Provider sent undecodable image data: {err}") from err

        url = getattr(first, "url", None)
        if not url:
            raise IllustrationUnavailable("Provider response had neither b64_json nor url")
        return await self._download(url), seed

    async def _request_free_image(self, prompt: str) -> Tuple[bytes, Optional[int]]:
        """Fetch one image from the keyless Pollinations endpoint.

        The authenticated endpoint bills per image and refuses once the balance
        hits zero, which is a poor fit for a bulk backfill on a hobby budget. The
        legacy image host takes no credential at all, so it keeps serving after
        the balance is gone. It answers in three seconds and returns the JPEG
        body directly rather than JSON, and it silently caps output at 768px
        regardless of the size asked for.

        It allows roughly one image per minute per client: a burst gets one 200
        and then 402s with an empty body, and the quota refills on its own within
        about a minute. So this waits its turn rather than firing a batch, and
        treats 402 as "not yet" instead of as a hard failure — otherwise a batch
        would mark every remaining event FAILED the moment the quota ran out.
        """
        import httpx

        width = height = _FREE_MAX_EDGE
        deadline = time.monotonic() + float(settings.ILLUSTRATION_FREE_MAX_WAIT_SECONDS)

        async with httpx.AsyncClient(
            timeout=float(settings.ILLUSTRATION_TIMEOUT_SECONDS),
            follow_redirects=True,
        ) as client:
            while True:
                seed = int(time.time() * 1000) % 2_000_000_000
                url = (
                    f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
                    f"?width={width}&height={height}&seed={seed}&nologo=true"
                )
                try:
                    response = await client.get(url)
                except httpx.HTTPError as err:
                    raise IllustrationUnavailable(f"Image provider call failed: {err}") from err

                if response.status_code == 200:
                    if not response.content[:3] == b"\xff\xd8\xff":
                        raise IllustrationUnavailable(
                            "Image provider returned a non-JPEG body"
                        )
                    return response.content, seed

                # 402 with no body is the quota, not a payment problem, so it is
                # retried after the refill interval instead of failing the event.
                # 5xx is treated the same way. The host answers 200 five times in
                # a row when left alone and 500s only appeared when two clients
                # hit it together, so a 500 here means "not now" rather than "not
                # ever" — and the wait is the full refill interval, so a broken
                # prompt cannot turn into a tight loop against a dead endpoint.
                quota = response.status_code in (402, 429)
                if quota or 500 <= response.status_code <= 599:
                    if time.monotonic() >= deadline:
                        waited = settings.ILLUSTRATION_FREE_MAX_WAIT_SECONDS
                        if quota:
                            raise IllustrationUnavailable(
                                f"Free image quota did not refill within {waited:.0f}s"
                            )
                        raise IllustrationUnavailable(
                            f"Free image host kept answering HTTP "
                            f"{response.status_code} after {waited:.0f}s"
                        )
                    await asyncio.sleep(float(settings.ILLUSTRATION_FREE_INTERVAL_SECONDS))
                    continue

                raise IllustrationUnavailable(
                    f"Image provider call failed: HTTP {response.status_code}"
                )

    async def _download(self, url: str) -> bytes:
        """Fetch an image the provider returned by URL rather than inline.

        Only http(s) is followed: a provider response is untrusted input, and
        blindly fetching whatever scheme it names would let it read local files.
        """
        if not url.lower().startswith(("http://", "https://")):
            raise IllustrationUnavailable("Provider returned a non-http image URL")
        import httpx

        async with httpx.AsyncClient(timeout=float(settings.ILLUSTRATION_TIMEOUT_SECONDS)) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    def _store(self, key: str, data: bytes) -> None:
        """Write the file, creating parents, without leaving a partial image behind."""
        path = os.path.join(settings.ILLUSTRATION_STORAGE_DIR, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = f"{path}.tmp"
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    async def _claim(self, session: AsyncSession, event_id: uuid.UUID) -> Optional[Illustration]:
        """Take ownership of an event's illustration, or return None if taken.

        A conditional UPDATE with RETURNING is the compare-and-swap: whoever flips
        PENDING/FAILED/SKIPPED to GENERATING gets a row back, and a second worker
        gets nothing and backs off. Re-reading the row afterwards would not be
        enough — both racers would see GENERATING — and two workers on the same
        event would otherwise both call the provider and bill for it twice.

        A GENERATING row older than the claim timeout is also fair game, so a
        worker killed mid-image does not strand the event forever.
        """
        stale_before = datetime.now(timezone.utc) - timedelta(
            minutes=settings.ILLUSTRATION_CLAIM_TIMEOUT_MINUTES
        )
        claimed = await session.execute(
            update(Illustration)
            .where(
                Illustration.event_id == event_id,
                or_(
                    Illustration.status.in_(
                        [
                            IllustrationStatus.PENDING,
                            IllustrationStatus.FAILED,
                            IllustrationStatus.SKIPPED,
                        ]
                    ),
                    and_(
                        Illustration.status == IllustrationStatus.GENERATING,
                        Illustration.updated_at < stale_before,
                    ),
                ),
            )
            .values(status=IllustrationStatus.GENERATING, failure_reason=None)
            .returning(Illustration.id)
        )
        if claimed.first() is None:
            await session.rollback()
            return None
        row = (
            await session.execute(
                select(Illustration).where(Illustration.event_id == event_id)
            )
        ).scalar_one()
        await session.commit()
        return row

    async def generate_for_event(self, session: AsyncSession, event: Event) -> str:
        """Generate and store one event's illustration, returning the final status.

        Never raises: a provider failure is recorded on the row so an operator can
        see the reason, and one bad event does not abort a batch.
        """
        existing = (
            await session.execute(select(Illustration).where(Illustration.event_id == event.id))
        ).scalar_one_or_none()
        if existing is not None and existing.status == IllustrationStatus.READY:
            return "ready"

        if existing is None:
            existing = Illustration(event_id=event.id, status=IllustrationStatus.PENDING)
            session.add(existing)
            await session.commit()

        claimed = await self._claim(session, event.id)
        if claimed is None:
            return "skipped_in_progress"

        try:
            prompt = await self.compose_prompt(
                event.title, event.summary, event.topic, event.country
            )
            data, seed = await self.request_image(prompt)
        except Exception as err:
            reason = str(err)[:500]
            logger.warn("Illustration generation failed", event_id=str(event.id), error=reason)
            claimed.status = IllustrationStatus.FAILED
            claimed.failure_reason = reason
            await session.commit()
            return "failed"

        content_type = sniff_image_type(data)
        if content_type is None:
            claimed.status = IllustrationStatus.FAILED
            claimed.failure_reason = "Provider response was not a recognised image"
            await session.commit()
            return "failed"

        width, height = image_dimensions(data)
        key = storage_key_for(event.id, prompt, self._model, content_type)
        try:
            await asyncio.to_thread(self._store, key, data)
        except OSError as err:
            logger.error("Illustration could not be stored", event_id=str(event.id), error=str(err))
            claimed.status = IllustrationStatus.FAILED
            claimed.failure_reason = f"Could not write image to storage: {err}"[:500]
            await session.commit()
            return "failed"

        claimed.status = IllustrationStatus.READY
        claimed.prompt = prompt
        claimed.provider = self._provider
        claimed.model = self._model
        claimed.attribution = settings.ILLUSTRATION_ATTRIBUTION
        claimed.storage_key = key
        claimed.content_type = content_type
        claimed.byte_size = len(data)
        claimed.width = width
        claimed.height = height
        claimed.seed = seed
        claimed.failure_reason = None
        await session.commit()
        logger.info(
            "Illustration generated",
            event_id=str(event.id),
            provider=self._provider,
            model=self._model,
            bytes=len(data),
            width=width,
            height=height,
        )
        return "generated"

    async def ensure_rows(self, session: AsyncSession, events: List[Event]) -> None:
        """Create a PENDING row per event that lacks one, so work is visible."""
        for event in events:
            exists = (
                await session.execute(
                    select(Illustration.id).where(Illustration.event_id == event.id)
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(Illustration(event_id=event.id, status=IllustrationStatus.PENDING))
        await session.commit()

    async def process_batch(self, limit: Optional[int] = None) -> Dict[str, int]:
        """Generate illustrations for the next batch of events lacking one.

        Returns a tally rather than raising, because this runs from a background
        task and an exception would be lost. Events are processed one at a time:
        image endpoints rate-limit aggressively, and a batch that fans out is how a
        run ends up half-failed on HTTP 429.
        """
        limit = limit or settings.ILLUSTRATION_BATCH_LIMIT
        tally = {"generated": 0, "failed": 0, "skipped": 0, "ready": 0}

        if not settings.ILLUSTRATION_ENABLED:
            logger.info("Illustration batch skipped: ILLUSTRATION_ENABLED is off")
            tally["skipped"] = limit
            return tally
        if not self.configured:
            logger.warn(
                "Illustration batch skipped: provider not configured",
                provider=self._provider,
                enabled=settings.ILLUSTRATION_ENABLED,
            )
            tally["skipped"] = limit
            return tally

        async with AsyncSessionFactory() as session:
            candidates = (
                await session.execute(
                    select(Event)
                    .outerjoin(Illustration, Illustration.event_id == Event.id)
                    .where(
                        Event.importance_score >= settings.ILLUSTRATION_MIN_IMPORTANCE,
                        (Illustration.id.is_(None))
                        | (Illustration.status != IllustrationStatus.READY),
                    )
                    .order_by(Event.importance_score.desc(), Event.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
            if not candidates:
                logger.info("No events awaiting illustration")
                return tally

            logger.info(
                "Illustration batch starting",
                events=len(candidates),
                provider=self._provider,
                model=self._model,
            )
            for event in candidates:
                outcome = await self.generate_for_event(session, event)
                if outcome in tally:
                    tally[outcome] += 1

        logger.info("Illustration batch finished", **tally)
        return tally


illustration_service = IllustrationService()