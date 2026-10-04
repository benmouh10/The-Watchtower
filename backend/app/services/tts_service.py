"""
GlobeLens AI — TTSService
========================
Turns article and event text into playable audio for
GET /articles/{id}/audio and GET /events/{id}/audio.

Four things shape the implementation:

* Cost — the default "local" provider runs Kokoro-82M on CPU inside the backend
  image, so narration costs nothing per play. Hosted providers stay available as
  upgrades and are billed per call, so text is sanitised, truncated and chunked
  before it goes upstream, and every result is cached in Redis under a hash of
  provider + model + voice + text: an unchanged article is narrated once per TTL,
  not once per press of a play button.
* Concatenation — hosted APIs cap a speech request at 4,000 characters, so long
  articles are split and the chunks are joined. MP3 is frame based, so joining
  frames plays back as one continuous stream; each chunk's ID3 header is dropped
  so tags are not re-announced mid-file. The local engine emits WAV, where only
  the frames after the first header may be appended.
* Providers — "openai" and "grok" both expose an OpenAI-compatible
  /audio/speech endpoint; "gemini" uses the Generative Language TTS endpoint and
  returns inline PCM/WAV; "local" is Kokoro via onnxruntime and needs no network.
  Anything else reports itself as unconfigured so the route can answer 501.
* Blocking work — synthesis is CPU bound and synchronous, so it runs in a worker
  thread instead of stalling the event loop.
"""
import asyncio
import base64
import hashlib
import inspect
import io
import os
import re
import time
import wave
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Tuple

import structlog
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.services.cache_service import cache_service

logger = structlog.get_logger()

SUPPORTED_PROVIDERS = ("local", "openai", "grok", "gemini")

# OpenAI-specific defaults, swapped when TTS_PROVIDER is set to another vendor
# without also overriding the model/voice (which would otherwise 400 upstream).
_DEFAULT_MODEL = "gpt-4o-mini-tts"
_OPENAI_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer", "sage", "coral", "verse"}
_GEMINI_MODEL = "gemini-2.5-flash-preview-tts"
_GEMINI_VOICE = "Kore"
_GEMINI_SAMPLE_RATE = 24000

# One engine per worker process: loading Kokoro costs a few seconds and ~1 GB, and
# reloading it per request would dominate the latency budget.
_LOCAL_ENGINE = None

_ID3_HEADER_LEN = 10          # b"ID3" + version(2) + flags(1) + size(4)
_MARKUP_HINT = re.compile(
    r"<(p|div|br|a|b|i|u|em|strong|span|ul|ol|li|h[1-6]|img|figure|figcaption|blockquote|table|tr|td|th)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


_WARMUP_PHRASE = "Ready."


class TTSUnavailable(RuntimeError):
    """Narration is not configured, or the provider could not deliver audio."""


class _SynthesisBusy(Exception):
    """Internal: this caller would rather skip than queue behind other work."""


def strip_id3(data: bytes) -> bytes:
    """Drop a leading ID3v2 tag so joined MP3 chunks do not repeat metadata."""
    if len(data) > _ID3_HEADER_LEN and data[:3] == b"ID3":
        size_bytes = data[6:10]
        # ID3v2 sizes are 7-bit synchsafe integers: the high bit of each byte is
        # unused, so bytes must not be OR-ed together directly.
        size = 0
        for byte in size_bytes:
            size = (size << 7) | (byte & 0x7F)
        return data[_ID3_HEADER_LEN + size:]
    return data


def wav_frames(data: bytes) -> Tuple[int, int, int, bytes]:
    """Return (sample_rate, channels, sample_width, frames) for RIFF/WAVE bytes."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        return handle.getframerate(), handle.getnchannels(), handle.getsampwidth(), handle.readframes(
            handle.getnframes()
        )


def join_wav(parts: List[bytes]) -> bytes:
    """Concatenate WAV chunks by appending frames after the first chunk's header."""
    if not parts:
        raise TTSUnavailable("No audio chunks to join")

    rate, channels, width, frames = wav_frames(parts[0])
    for part in parts[1:]:
        other_rate, other_channels, other_width, other_frames = wav_frames(part)
        if (other_rate, other_channels, other_width) != (rate, channels, width):
            raise TTSUnavailable(
                "Audio chunks disagree on sample rate or layout; cannot join narration"
            )
        frames += other_frames

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return buffer.getvalue()


class Mp3StreamEncoder:
    """One MP3 encoder across many pieces of audio.

    A progressive recording is assembled from chunks, and giving each chunk its own
    encoder makes every boundary a flush: LAME discards its delay on flush and pads
    the last frame, so each chunk ends in a half frame and the joined stream has a
    glitch every time the seam is reached. Keeping a single encoder for the whole
    recording means only the very end is flushed, exactly as if it had been encoded
    in one pass.
    """

    def __init__(self, quality: int = 7) -> None:
        import lameenc

        self._encoder = lameenc.Encoder()
        self._encoder.set_in_sample_rate(24000)
        self._encoder.set_channels(1)
        self._encoder.set_quality(quality)
        self._pending = bytearray()

    def add_wav(self, wav_bytes: bytes) -> bytes:
        """Add a mono 16-bit WAV segment, returning whatever MP3 is now ready."""
        import numpy as np

        rate, channels, width, frames = wav_frames(wav_bytes)
        if width != 2:
            raise TTSUnavailable(f"Expected 16-bit PCM for MP3 encoding, got {width * 8}-bit")
        if rate != 24000:
            raise TTSUnavailable(f"Streamed MP3 expects 24kHz audio, got {rate}Hz")

        self._pending += frames
        # Only whole samples can be handed over; a segment can end mid-frame when a
        # provider hands back an odd number of interleaved samples.
        whole = len(self._pending) - (len(self._pending) % (2 * channels))
        if not whole:
            return b""
        segment = bytes(self._pending[:whole])
        self._pending = bytearray(self._pending[whole:])

        samples = np.frombuffer(segment, dtype="<i2")
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1).astype("<i2")
        return bytes(self._encoder.encode(samples.astype("<i2").tobytes()))

    def finish(self) -> bytes:
        """Flush the encoder and return the closing frames."""
        return bytes(self._encoder.flush())


def encode_mp3(wav_bytes: bytes, quality: int) -> bytes:
    """Encode mono WAV to MP3. Raises if the optional encoder is unavailable."""
    stream = Mp3StreamEncoder(quality)
    encoded = stream.add_wav(wav_bytes) + stream.finish()
    if not encoded:
        raise TTSUnavailable("MP3 encoder returned no audio")
    return encoded


def join_audio(parts: List[bytes], content_type: str) -> bytes:
    """Join same-format chunks, choosing the framing rules for that container."""
    if not parts:
        raise TTSUnavailable("No audio chunks to join")

    is_riff = all(part[:4] == b"RIFF" for part in parts)
    if is_riff and ("wav" in content_type or "x-pn-wav" in content_type):
        # Re-wraps, so a single chunk keeps exactly one header.
        return join_wav(parts)
    if "mpeg" in content_type:
        return strip_id3(parts[0]) + b"".join(strip_id3(part) for part in parts[1:])
    if len(parts) == 1:
        return parts[0]
    # Headerless audio (e.g. Gemini's inline PCM) has no framing to repair, so the
    # chunks are appended as-is.
    return b"".join(parts)


def audio_extension(content_type: str) -> str:
    """Filename extension matching a narration content type."""
    return ".wav" if "wav" in (content_type or "") else ".mp3"


def sniff_content_type(audio: bytes, fallback: str) -> str:
    """Detect the container from magic bytes.

    Providers disagree about what they send back — Gemini can answer with WAV or
    headerless PCM, and a local encode can fall back from MP3 to WAV — so the
    bytes decide what the route advertises rather than the provider name.
    """
    if audio[:4] == b"RIFF":
        return "audio/wav"
    if audio[:3] == b"ID3" or (len(audio) > 1 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    return fallback


def _mp3_available() -> bool:
    try:
        import lameenc  # noqa: F401
    except ImportError:
        return False
    return True


def _build_local_engine():
    """Construct Kokoro with a bounded ONNX thread pool.

    kokoro_onnx opens its session without session options, so ONNX Runtime falls back
    to one intra-op thread per logical CPU. On a twelve-core machine that means a
    single sentence of speech pins all twelve cores for the seconds it takes to
    synthesise it, which starves the database, the search index and the web tier
    sharing the host - and buys very little, because the graph is memory bound and
    scales poorly past a handful of threads.

    Capping the pool keeps synthesis roughly as fast while leaving the rest of the
    machine alone. The patch is applied only for the duration of the constructor and
    then undone, so nothing else in the process inherits the limit by accident.
    """
    import kokoro_onnx
    import onnxruntime as ort
    from kokoro_onnx.session import resolve_providers

    def create_session(model_path: str) -> ort.InferenceSession:
        options = ort.SessionOptions()
        options.intra_op_num_threads = settings.TTS_ONNX_THREADS
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        return ort.InferenceSession(
            model_path, sess_options=options, providers=resolve_providers()
        )

    original = kokoro_onnx.create_session
    kokoro_onnx.create_session = create_session
    try:
        return kokoro_onnx.Kokoro(
            settings.TTS_LOCAL_MODEL_PATH, settings.TTS_LOCAL_VOICES_PATH
        )
    finally:
        kokoro_onnx.create_session = original


def _load_local_engine():
    """Return the process-wide Kokoro engine, loading it on first use."""
    global _LOCAL_ENGINE
    if _LOCAL_ENGINE is None:
        logger.info(
            "Loading local speech model",
            model=settings.TTS_LOCAL_MODEL_PATH,
            voices=settings.TTS_LOCAL_VOICES_PATH,
            threads=settings.TTS_ONNX_THREADS,
        )
        _LOCAL_ENGINE = _build_local_engine()
    return _LOCAL_ENGINE


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap headerless 16-bit mono PCM in a RIFF/WAVE container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm)
    return buffer.getvalue()


def normalize_loudness(samples, target_dbfs: float, ceiling_dbfs: float):
    """Scale speech toward a target RMS, capped so the peak cannot clip.

    Kokoro's raw output is quiet — roughly -22 dBFS RMS for its English voices —
    and on a phone or laptop speaker that reads as muffled rather than quiet, so
    every voice is brought to a common level before encoding. Gain is computed
    from the measured RMS, then reduced if it would push the peak past the ceiling,
    which keeps loud passages intact instead of clipping them.
    """
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return audio

    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    if rms <= 0.0:
        # Digital silence (or a fully cancelled buffer) has no level to match.
        return audio

    gain = float(10.0 ** (target_dbfs / 20.0)) / rms
    peak = float(np.abs(audio).max())
    ceiling = float(10.0 ** (ceiling_dbfs / 20.0))
    if peak * gain > ceiling and peak > 0.0:
        gain = ceiling / peak
    return audio * np.float32(gain)


def _samples_to_wav(samples, sample_rate: int) -> bytes:
    """Wrap Kokoro's float samples in a 16-bit mono RIFF/WAVE container."""
    import numpy as np

    audio = normalize_loudness(
        samples, settings.TTS_LOCAL_TARGET_RMS_DBFS, settings.TTS_LOCAL_PEAK_DBFS
    )
    if audio.size == 0:
        raise TTSUnavailable("Local speech engine returned no audio")
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def to_speech_text(raw: str, max_chars: int) -> str:
    """Reduce an article body to bounded, plain, speakable text.

    Scraped wire copy can carry markup and boilerplate; newsroom articles are
    written as HTML. Both are flattened here so nothing is sent to the provider
    that a listener would have to hear read out.
    """
    text = raw or ""
    if _MARKUP_HINT.search(text):
        soup = BeautifulSoup(text, "html.parser")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        # get_text(" ") keeps block elements from fusing words together.
        text = soup.get_text(" ")

    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) <= max_chars:
        return text

    clipped = text[:max_chars]
    # Prefer ending on a sentence so the narration is not cut mid-clause.
    boundary = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if boundary > max_chars // 2:
        return clipped[: boundary + 1]
    return clipped.rstrip()


def _peel_chunk(text: str, chunk_chars: int) -> Tuple[str, str]:
    """Split off the first chunk of text, returning it and whatever is left.

    The remainder is taken from the splitter's own partition rather than by slicing the
    original string at len(head): the splitter normalises whitespace and sentence
    terminators, so a character count into the original text does not line up with it,
    and slicing there silently drops or repeats words.
    """
    parts = split_for_speech(text, chunk_chars)
    if not parts:
        return "", text
    return parts[0], " ".join(parts[1:])


def stream_chunks(text: str, first_chars: int, rest_chars: int) -> List[str]:
    """Split narration for progressive playback: a short lead-in, then long chunks.

    Every synthesis call carries a fixed cost, so the first one decides how long the
    listener waits and should be as small as a whole sentence. Everything after it
    only has to keep ahead of playback, so it is cut as coarsely as seams allow.
    """
    if first_chars <= 0 or rest_chars <= 0:
        raise ValueError("chunk sizes must be positive")

    lead_in = split_for_speech(text, first_chars)
    if not lead_in:
        return []
    if len(lead_in) == 1:
        return lead_in

    # Ramp: a short chunk is expensive per character — measured on this model, ~60
    # characters costs about as much wall time as it yields audio, while a 300-character
    # chunk yields about twice its own build time — and the opener is one short
    # sentence. So playback starts with only a couple of seconds of buffer and then has
    # to wait out a full-size chunk, which is where a listener hears a pause a few words
    # in. Two intermediate chunks, each cheap enough to land quickly, build a cushion
    # before the first expensive one arrives.
    #
    # Only those two are ramped. Applying the small sizes to the whole text would drop
    # every later chunk back to the slow per-chunk rate, which on wall-clock time loses
    # to not ramping at all.
    opener = len(lead_in[0])
    remaining = " ".join(lead_in[1:])
    chunks = [lead_in[0]]
    for share in (0.7, 1.5):
        size = max(40, min(rest_chars, round(opener * share)))
        head, remaining = _peel_chunk(remaining, size)
        chunks.append(head)
        if not remaining.strip():
            return chunks
    chunks.extend(split_for_speech(remaining, rest_chars))
    return chunks


def split_for_speech(text: str, chunk_chars: int) -> List[str]:
    """Split text into chunks of at most chunk_chars, preferring natural breaks."""
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")

    chunks: List[str] = []
    for paragraph in text.split("\n\n"):
        block = paragraph.strip()
        if not block:
            continue
        if len(block) <= chunk_chars:
            chunks.append(block)
            continue

        sentence_run = ""
        for sentence in _SENTENCE_END.split(block):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > chunk_chars:
                # A single monster sentence (minified boilerplate): flush the
                # pending run, then hard-split on word boundaries.
                if sentence_run:
                    chunks.append(sentence_run)
                    sentence_run = ""
                words = sentence.split()
                piece = ""
                for word in words:
                    candidate = f"{piece} {word}".strip()
                    if len(candidate) > chunk_chars and piece:
                        chunks.append(piece)
                        piece = word
                    else:
                        piece = candidate
                if piece:
                    sentence_run = piece
                continue

            candidate = f"{sentence_run} {sentence}".strip()
            if len(candidate) > chunk_chars:
                if sentence_run:
                    chunks.append(sentence_run)
                sentence_run = sentence
            else:
                sentence_run = candidate

        if sentence_run:
            chunks.append(sentence_run)

    return chunks


def article_narration_text(article) -> str:
    """Spoken text for an article: headline followed by its body.

    Shared by the audio route and the background warmer so both hash the same
    string — if they disagreed by a byte, every pre-warmed article would miss the
    cache and the listener would still wait.
    """
    return f"{article.title}. {article.content or ''}"


def event_narration_text(title: str, summary: str) -> str:
    """Spoken text for an event briefing, matching GET /events/{id}/audio."""
    return f"{title or 'Event briefing'}. {summary or ''}"


async def _read_binary(response) -> bytes:
    """Normalise the OpenAI SDK's binary response wrapper to bytes."""
    data = getattr(response, "content", None)
    if data is None:
        read = getattr(response, "read", None)
        if read is None:
            raise TTSUnavailable("speech provider returned no audio")
        data = read()
    if inspect.isawaitable(data):
        data = await data
    return bytes(data)


class TTSService:
    """Narrates article text, memoising each result in Redis."""

    def __init__(self) -> None:
        self._provider = (settings.TTS_PROVIDER or "").strip().lower()
        self._client = None
        # Built lazily on purpose: this singleton is constructed at import time, and
        # AsyncOpenAI raises when handed an empty key. A checkout without speech
        # credentials must still boot and report 501, not fail to start.
        if self._provider == "openai" and settings.OPENAI_API_KEY:
            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        elif self._provider == "grok" and settings.GROK_API_KEY:
            self._client = AsyncOpenAI(
                api_key=settings.GROK_API_KEY,
                base_url="https://api.x.ai/v1",
            )
        # Filled in per event loop by _gate(): this service is a module-level
        # singleton built at import time, long before a loop exists.
        self._synthesis_gate: Optional[asyncio.Semaphore] = None
        self._synthesis_loop = None
        self._inflight: Dict[str, asyncio.Task] = {}

    def _gate(self) -> asyncio.Semaphore:
        """The lock that bounds how much synthesis runs at once.

        Rebuilt when the loop changes, since asyncio primitives bind to the first
        loop they are awaited on and this object outlives several loops in tests.
        """
        loop = asyncio.get_running_loop()
        if self._synthesis_loop is not loop:
            self._synthesis_gate = asyncio.Semaphore(
                max(1, int(settings.TTS_MAX_CONCURRENT_SYNTHESIS))
            )
            self._synthesis_loop = loop
            self._inflight.clear()
        return self._synthesis_gate

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def configured(self) -> bool:
        """True when a supported provider has the credentials it needs."""
        if self._provider not in SUPPORTED_PROVIDERS:
            return False
        if self._provider == "local":
            # No API key needed, but the model and voice pack must be on disk.
            model = settings.TTS_LOCAL_MODEL_PATH
            voices = settings.TTS_LOCAL_VOICES_PATH
            return bool(model and voices) and os.path.isfile(model) and os.path.isfile(voices)
        if self._provider == "gemini":
            key = settings.GEMINI_API_KEY or ""
        elif self._provider == "grok":
            key = settings.GROK_API_KEY or ""
        else:
            key = settings.OPENAI_API_KEY or ""
        return bool(key) and "your_" not in key

    @property
    def _model(self) -> str:
        if self._provider == "local":
            return os.path.basename(settings.TTS_LOCAL_MODEL_PATH or "")
        if self._provider == "gemini" and settings.TTS_MODEL == _DEFAULT_MODEL:
            return _GEMINI_MODEL
        return settings.TTS_MODEL

    @property
    def _voice(self) -> str:
        if self._provider == "local":
            return settings.TTS_LOCAL_VOICE
        voice = (settings.TTS_VOICE or "").strip()
        if self._provider == "gemini" and voice.lower() in _OPENAI_VOICES:
            # "alloy" means nothing to Gemini; fall back to a neutral prebuilt.
            return _GEMINI_VOICE
        return voice

    def cache_key(self, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"tts:{self._provider}:{self._model}:{self._voice}:{digest}"

    async def narrate(self, raw_text: str, opportunistic: bool = False) -> Tuple[bytes, str]:
        """Return (audio, content_type) for raw_text, synthesising once per TTL.

        With opportunistic set, a _SynthesisBusy is raised instead of queueing behind
        whatever is already being voiced. That is for background warm-ups: a clip is
        cheap to build later, so it must never sit in front of a listener.
        """
        if not self.configured:
            raise TTSUnavailable(
                f"Text-to-speech is not configured (provider={self._provider or 'unset'})"
            )

        text = to_speech_text(raw_text, settings.TTS_MAX_CHARS)
        if not text:
            raise TTSUnavailable("Article has no text to narrate")

        key = self.cache_key(text)
        cached = await self._cached_audio(key)
        if cached is not None:
            logger.info("Narration cache hit", provider=self._provider, key=key)
            return cached, sniff_content_type(cached, self._content_type)

        parts: List[bytes] = []
        async with self._clip_slot(opportunistic):
            # Whoever held the slot before us may have just built this exact text.
            # Re-reading the cache here is what turns three simultaneous listeners
            # into one synthesis instead of three.
            cached = await self._cached_audio(key)
            if cached is not None:
                logger.info("Narration cache hit while queued", provider=self._provider, key=key)
                return cached, sniff_content_type(cached, self._content_type)
            for chunk in split_for_speech(text, settings.TTS_CHUNK_CHARS):
                parts.append(await self._synthesize(chunk))

        media_type = sniff_content_type(parts[0], self._content_type)
        audio = join_audio(parts, media_type)
        media_type = sniff_content_type(audio, media_type)
        await self._remember_audio(key, audio, len(parts))
        return audio, media_type

    async def warm_narration(self, raw_text: str) -> Optional[Tuple[bytes, str]]:
        """Build narration ahead of the first play, yielding to any live listener.

        Returns None when the CPU is already busy, so the caller can move on to the
        next candidate instead of delaying the play button by a whole clip.
        """
        try:
            return await self.narrate(raw_text, opportunistic=True)
        except _SynthesisBusy:
            logger.info(
                "Narration warm-up deferred",
                provider=self._provider,
                reason="synthesis already in use",
            )
            return None

    @property
    def _content_type(self) -> str:
        """Container this provider emits, after any local re-encoding."""
        if self._provider == "local" and _mp3_available():
            return "audio/mpeg"
        if self._provider == "gemini":
            return "audio/wav"
        return "audio/mpeg"

    @property
    def streams_progressively(self) -> bool:
        """True when chunks can be concatenated as a live MP3 stream.

        MP3 is frame based, so a player can start on the first chunk and continue as
        later ones arrive. WAV is not: its header declares the total length, so a WAV
        response has to be buffered whole or the player sees a truncated file.

        Only the local provider qualifies. Hosted providers hand back a finished clip
        per call, already framed and flushed by their own encoder, so there is nothing
        to stream into continuously and they are buffered instead. Local falls back to
        WAV when the MP3 encoder is missing, so the encoder decides.
        """
        return self._provider == "local" and _mp3_available()

    async def iter_audio(self, raw_text: str):
        """Yield narration chunk by chunk, then memoise the finished recording.

        Playback starts on the first chunk instead of after the last. Local
        synthesis runs at about half real time with roughly two seconds of fixed
        cost per call, so a ninety-second briefing would otherwise hold the player
        silent for the better part of a minute.

        The first chunk is deliberately smaller than the rest: it sets how long the
        listener waits, while later chunks only need to arrive faster than they are
        played. A short lead-in and long chunks behind it give both.

        A cached recording is yielded whole, so warm items behave exactly as before.
        """
        text = to_speech_text(raw_text, settings.TTS_MAX_CHARS)
        if not text:
            raise TTSUnavailable("Article has no text to narrate")
        if not self.configured:
            raise TTSUnavailable(
                f"Text-to-speech is not configured (provider={self._provider or 'unset'})"
            )

        key = self.cache_key(text)
        cached = await self._cached_audio(key)
        if cached is not None:
            logger.info("Narration cache hit", provider=self._provider, key=key)
            yield cached
            return

        media_type = self._content_type
        parts: List[bytes] = []
        # A progressive MP3 is encoded as one continuous stream, so its chunks are
        # raw PCM segments rather than complete files; anything else (WAV, or a
        # hosted provider that returns a finished clip per call) is encoded per chunk
        # and repaired on join.
        stream = Mp3StreamEncoder(settings.TTS_LOCAL_MP3_QUALITY) if self.streams_progressively else None

        # The slot is held across every chunk of this clip, so a warm-up running on
        # another item cannot interleave with it and starve the player's buffer.
        async with self._clip_slot():
            # The clip we queued behind may have just finished this exact text.
            cached = await self._cached_audio(key)
            if cached is not None:
                logger.info("Narration cache hit while queued", provider=self._provider, key=key)
                yield cached
                return
            for index, chunk in enumerate(stream_chunks(text, settings.TTS_STREAM_FIRST_CHUNK_CHARS, settings.TTS_STREAM_CHUNK_CHARS)):
                if stream is not None:
                    # Same sharing as the buffered path: the play button streams this,
                    # so it must not be able to bypass it.
                    part = stream.add_wav(
                        await self._shared(
                            self.cache_key(chunk),
                            lambda text=chunk: self._synthesize_local_wav(text),
                        )
                    )
                else:
                    part = await self._synthesize(chunk)
                    if index and "mpeg" in media_type:
                        # Only the first chunk may carry an ID3 tag.
                        part = strip_id3(part)
                if part:
                    parts.append(part)
                    yield part

            if stream is not None:
                tail = stream.finish()
                if tail:
                    parts.append(tail)
                    yield tail

            audio = join_audio(parts, media_type)
            # Written before the slot is released, so whoever queued behind us reads a
            # finished recording instead of voicing the same text again.
            await self._remember_audio(key, audio, len(parts))

    async def preload_local(self) -> bool:
        """Load the local model ahead of the first request.

        Reading a few hundred megabytes of ONNX weights takes several seconds; paying
        that inside the first listener's request is pure waiting, and the model stays
        resident afterwards. Safe to call repeatedly and never raises.
        """
        if self._provider != "local":
            return False
        try:
            started = time.perf_counter()
            await run_in_threadpool(_load_local_engine)
            loaded = round(time.perf_counter() - started, 2)

            # Loading the weights is only half the warm-up. The first real inference
            # also pays for onnxruntime's lazy work — arena growth, kernel selection,
            # first-touch of the weights — and measured on this model that made the
            # opening chunk run at roughly 1x real time instead of the ~1.7x every
            # later chunk gets. On a streamed narration that is the difference between
            # the listener hearing a pause a few words in and hearing none, so it is
            # paid here, at startup, on a throwaway phrase.
            voiced = time.perf_counter()
            await self._synthesize_local_wav(_WARMUP_PHRASE)
            logger.info(
                "Local speech model ready",
                seconds=loaded,
                inference_seconds=round(time.perf_counter() - voiced, 2),
            )
            return True
        except Exception as exc:  # a failed warm-up must not break the first play
            logger.warn("Local speech model warm-up failed", error=str(exc))
            return False

    async def _cached_audio(self, key: str):
        """Return cached audio bytes, or None. A Redis failure is never fatal."""
        try:
            cached = await cache_service.get(key)
        except Exception as cache_err:  # Redis hiccup must not block narration
            logger.warn("Narration cache lookup failed", error=str(cache_err))
            return None
        if not cached:
            return None
        return base64.b64decode(cached)

    async def _remember_audio(self, key: str, audio: bytes, chunks: int = 1) -> None:
        """Store a finished recording and log how it was produced."""
        media_type = sniff_content_type(audio, self._content_type)
        logger.info(
            "Narration synthesised",
            provider=self._provider,
            model=self._model,
            voice=self._voice,
            chunks=chunks,
            bytes=len(audio),
            content_type=media_type,
        )
        if settings.TTS_CACHE_TTL_SECONDS <= 0:
            return
        try:
            await cache_service.set(
                key, base64.b64encode(audio).decode("ascii"), settings.TTS_CACHE_TTL_SECONDS
            )
        except Exception as cache_err:
            logger.warn("Narration cache write failed", error=str(cache_err))

    async def _shared(self, key: str, factory, opportunistic: bool = False) -> bytes:
        """Run factory() once per key, shared with anyone asking for the same text.

        Two listeners asking for the same recording used to run two copies of the
        model over it, which doubled the cores for no benefit and left each of them
        waiting twice as long. Identical work is therefore done once and awaited by
        everyone.

        The shield matters for the same reason: a browser that navigates away
        mid-stream cancels its request, and that must not take the shared run — or
        the cache entry it is about to fill — down with it.
        """
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self._inflight[key] = task
            task.add_done_callback(lambda done, k=key: self._finish_inflight(k, done))
        return await asyncio.shield(task)

    def _finish_inflight(self, key: str, task: asyncio.Task) -> None:
        self._inflight.pop(key, None)
        if not task.cancelled():
            # Mark a failure as retrieved so an error nobody awaited any more (every
            # listener having cancelled) is not reported as an unhandled exception.
            task.exception()

    @asynccontextmanager
    async def _clip_slot(self, opportunistic: bool = False):
        """Hold the synthesis slot for a whole clip.

        The slot is taken per clip, not per chunk, so a background warm-up can never
        slip a chunk between two chunks of someone's narration: that pause is exactly
        what a listener hears as stuttering. An opportunistic caller gives up instead
        of waiting, because its work is worth less than the wait.
        """
        gate = self._gate()
        if opportunistic and gate.locked():
            raise _SynthesisBusy()
        await gate.acquire()
        try:
            yield
        finally:
            gate.release()

    async def _synthesize(self, chunk: str) -> bytes:
        return await self._shared(
            self.cache_key(chunk),
            lambda: self._synthesize_with_provider(chunk),
        )

    async def _synthesize_with_provider(self, chunk: str) -> bytes:
        if self._provider == "local":
            return await self._synthesize_local(chunk)
        if self._provider == "gemini":
            return await self._synthesize_gemini(chunk)
        if self._client is None:
            raise TTSUnavailable(f"No client initialised for provider {self._provider}")
        try:
            response = await self._client.audio.speech.create(
                model=self._model,
                voice=self._voice,
                input=chunk,
                response_format="mp3",
                speed=settings.TTS_SPEED,
            )
        except Exception as exc:
            logger.error("Speech provider call failed", provider=self._provider, error=str(exc))
            raise TTSUnavailable(f"Speech provider rejected the request: {exc}") from exc
        return await _read_binary(response)

    async def _synthesize_local(self, chunk: str) -> bytes:
        """Synthesise with Kokoro on CPU and return WAV, re-encoded to MP3 if possible."""
        wav_bytes = await self._synthesize_local_wav(chunk)
        if not _mp3_available():
            return wav_bytes
        try:
            return encode_mp3(wav_bytes, settings.TTS_LOCAL_MP3_QUALITY)
        except TTSUnavailable:
            raise
        except Exception as exc:
            # A missing/broken encoder should not cost the listener their narration.
            logger.warn("MP3 encoding unavailable, serving WAV", error=str(exc))
            return wav_bytes

    async def _synthesize_local_wav(self, chunk: str) -> bytes:
        """Run Kokoro on CPU and return normalised WAV, for the streaming encoder."""
        def run() -> bytes:
            engine = _load_local_engine()
            samples, sample_rate = engine.create(
                chunk, voice=self._voice, speed=settings.TTS_SPEED, lang="en-us"
            )
            return _samples_to_wav(samples, sample_rate)

        try:
            wav_bytes = await run_in_threadpool(run)
        except TTSUnavailable:
            raise
        except Exception as exc:
            logger.error("Local speech synthesis failed", error=str(exc))
            raise TTSUnavailable(f"Local speech synthesis failed: {exc}") from exc

        # _samples_to_wav already levels each chunk to the same target loudness, so
        # chunk-to-chunk volume matches even though they were voiced separately.
        return wav_bytes

    async def _synthesize_gemini(self, chunk: str) -> bytes:
        """Gemini returns inline audio instead of streaming MP3."""
        import httpx

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": chunk}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}}
                },
            },
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY or ""},
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            logger.error("Gemini TTS call failed", error=str(exc))
            raise TTSUnavailable(f"Gemini TTS request failed: {exc}") from exc

        try:
            inline = body["candidates"][0]["content"]["parts"][0]["inlineData"]
            audio = base64.b64decode(inline["data"])
            mime = (inline.get("mimeType") or "").lower()
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise TTSUnavailable(f"Gemini TTS response had no audio: {body}") from exc

        if not audio:
            raise TTSUnavailable("Gemini TTS response had no audio")

        if "wav" in mime:
            return audio
        # Gemini can answer with headerless PCM (audio/L16). Wrapping it keeps the
        # response truthful about its container and lets chunks be joined by frames.
        logger.info("Wrapping headerless Gemini audio", mime=mime)
        return _pcm_to_wav(audio, _GEMINI_SAMPLE_RATE)


tts_service = TTSService()