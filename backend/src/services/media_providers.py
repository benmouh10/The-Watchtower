"""
GlobeLens AI — Config-gated vision/audio adapters
==================================================
Thin, honestly-refusing adapters for image captioning and audio transcription.
These mirror the chat/embedding provider rules in ai_providers:

  * Nothing is fabricated. With VISION_PROVIDER/AUDIO_PROVIDER empty (the
    default), or when the chosen provider's SDK or credentials are absent, the
    adapter raises MediaProviderUnavailable and the media controller turns that
    into an honest 503. No fake caption, no "transcribed" stub.
  * SDKs are imported lazily; the app boots without them and picks the provider
    from config (reusing the shared credential keys).
  * Unknown provider names are refused, never silently routed elsewhere.
"""
import asyncio
import base64
from dataclasses import dataclass
from typing import Optional

from src.core.config import Settings


class MediaProviderUnavailable(RuntimeError):
    """Raised when a vision/audio operation cannot be performed honestly."""


def _is_truthy(value: Optional[str]) -> bool:
    return bool(value and str(value).strip())


@dataclass
class CaptionResult:
    """One completed image-caption pass: the caption plus provenance."""
    caption: str
    model: str
    provider: str


@dataclass
class TranscriptResult:
    """One completed transcription pass: the text plus provenance."""
    text: str
    model: str
    provider: str


# ── Vision providers ──────────────────────────────────────────────────────

class VisionProvider:
    """Base adapter. The empty config case answers an honest refusal."""

    name: str = ""
    model: str = ""

    def is_configured(self) -> bool:
        return False

    async def caption_image(
        self,
        image_bytes: bytes,
        media_type: str,
        *,
        hint: str = "",
    ) -> CaptionResult:
        raise MediaProviderUnavailable(
            "No VISION_PROVIDER is configured for image captioning. Set "
            "VISION_PROVIDER (e.g. 'openai' or 'gemini') plus its credential "
            "to enable AI captions."
        )


class OpenAICompatVisionProvider(VisionProvider):
    """Image captioning through the OpenAI chat-completions image_url input."""

    name = "openai"

    def __init__(self, settings: Settings, model: str) -> None:
        self.settings = settings
        self.model = model or "gpt-4o-mini"

    def is_configured(self) -> bool:
        return _is_truthy(self.settings.OPENAI_API_KEY)

    async def caption_image(
        self,
        image_bytes: bytes,
        media_type: str,
        *,
        hint: str = "",
    ) -> CaptionResult:
        if not self.is_configured():
            raise MediaProviderUnavailable(
                "Vision provider 'openai' is selected but OPENAI_API_KEY is "
                "not set. Refusing to generate a fake caption."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - openai is a hard dep
            raise MediaProviderUnavailable(
                "openai SDK is not installed; cannot caption images."
            ) from exc

        client = AsyncOpenAI(api_key=self.settings.OPENAI_API_KEY)
        data_url = (
            f"data:{media_type or 'image/jpeg'};base64,"
            f"{base64.b64encode(image_bytes).decode('ascii')}"
        )
        instruction = (
            "Write a concise, neutral photo caption (max 45 words) for this "
            "news image. Describe only what is visible: subjects, action, "
            "setting, and any on-screen text. Do not speculate about context, "
            "tone, or events the image does not show."
        )
        if hint:
            instruction += f"\n\nContext from the article (reference only): {hint}"

        response = await client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            max_tokens=150,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise MediaProviderUnavailable("Vision provider returned an empty caption.")
        return CaptionResult(caption=text, model=self.model, provider=self.name)


class GeminiVisionProvider(VisionProvider):
    """Image captioning via google-generativeai inline image parts."""

    name = "gemini"

    def __init__(self, settings: Settings, model: str) -> None:
        self.settings = settings
        self.model = model or "gemini-2.0-flash"

    def is_configured(self) -> bool:
        return _is_truthy(self.settings.GEMINI_API_KEY)

    async def caption_image(
        self,
        image_bytes: bytes,
        media_type: str,
        *,
        hint: str = "",
    ) -> CaptionResult:
        if not self.is_configured():
            raise MediaProviderUnavailable(
                "Vision provider 'gemini' is selected but GEMINI_API_KEY is "
                "not set. Refusing to generate a fake caption."
            )
        try:
            import google.generativeai as genai
        except ImportError as exc:  # pragma: no cover - google-generativeai is a hard dep
            raise MediaProviderUnavailable(
                "google-generativeai SDK is not installed; cannot caption images."
            ) from exc

        genai.configure(api_key=self.settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(self.model)
        instruction = (
            "Write a concise, neutral photo caption (max 45 words) for this "
            "news image. Describe only what is visible: subjects, action, "
            "setting, and any on-screen text. Do not speculate about context, "
            "tone, or events the image does not show."
        )
        if hint:
            instruction += f"\n\nContext from the article (reference only): {hint}"

        response = await asyncio.to_thread(
            model.generate_content,
            [
                genai.types.Content(
                    role="user",
                    parts=[
                        {"mime_type": media_type or "image/jpeg", "data": image_bytes},
                        {"text": instruction},
                    ],
                )
            ],
        )
        text = (response.text or "").strip()
        if not text:
            raise MediaProviderUnavailable("Vision provider returned an empty caption.")
        return CaptionResult(caption=text, model=self.model, provider=self.name)


# ── Audio providers ───────────────────────────────────────────────────────

class AudioProvider:
    """Base adapter. The empty config case answers an honest refusal."""

    name: str = ""
    model: str = ""

    def is_configured(self) -> bool:
        return False

    async def transcribe(
        self,
        audio_bytes: bytes,
        media_type: str,
        *,
        language: Optional[str] = None,
    ) -> TranscriptResult:
        raise MediaProviderUnavailable(
            "No AUDIO_PROVIDER is configured for transcription. Set "
            "AUDIO_PROVIDER (e.g. 'openai') plus its credential to enable it."
        )


class OpenAIAudioProvider(AudioProvider):
    """Speech-to-text through the OpenAI transcriptions API."""

    name = "openai"

    def __init__(self, settings: Settings, model: str) -> None:
        self.settings = settings
        self.model = model or "whisper-1"

    def is_configured(self) -> bool:
        return _is_truthy(self.settings.OPENAI_API_KEY)

    async def transcribe(
        self,
        audio_bytes: bytes,
        media_type: str,
        *,
        language: Optional[str] = None,
    ) -> TranscriptResult:
        if not self.is_configured():
            raise MediaProviderUnavailable(
                "Audio provider 'openai' is selected but OPENAI_API_KEY is not "
                "set. Refusing to simulate a transcription."
            )
        try:
            import tempfile

            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - openai is a hard dep
            raise MediaProviderUnavailable(
                "openai SDK is not installed; cannot transcribe audio."
            ) from exc

        client = AsyncOpenAI(api_key=self.settings.OPENAI_API_KEY)
        suffix = ".ogg"
        if "mp3" in media_type or "mpeg" in media_type:
            suffix = ".mp3"
        elif "wav" in media_type:
            suffix = ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            with open(tmp.name, "rb") as handle:
                kwargs: dict = {"file": handle, "model": self.model}
                if language:
                    kwargs["language"] = language
                transcript = await client.audio.transcriptions.create(**kwargs)
        text = getattr(transcript, "text", None) or (transcript if isinstance(transcript, str) else "")
        text = text.strip()
        if not text:
            raise MediaProviderUnavailable("Audio provider returned an empty transcript.")
        return TranscriptResult(text=text, model=self.model, provider=self.name)


# ── Factories ─────────────────────────────────────────────────────────────

_VISION_FACTORIES = {
    "openai": lambda s, m: OpenAICompatVisionProvider(s, m),
    "gemini": lambda s, m: GeminiVisionProvider(s, m),
}

_AUDIO_FACTORIES = {
    "openai": lambda s, m: OpenAIAudioProvider(s, m),
}


def build_vision_provider(settings: Optional[Settings] = None) -> VisionProvider:
    cfg = settings if settings is not None else _global_settings()
    name = (cfg.VISION_PROVIDER or "").strip().lower()
    factory = _VISION_FACTORIES.get(name)
    if factory is None:
        if not name:
            return VisionProvider()
        return VisionProvider()  # unknown names degrade to the honest refusal
    return factory(cfg, cfg.VISION_MODEL)


def build_audio_provider(settings: Optional[Settings] = None) -> AudioProvider:
    cfg = settings if settings is not None else _global_settings()
    name = (cfg.AUDIO_PROVIDER or "").strip().lower()
    factory = _AUDIO_FACTORIES.get(name)
    if factory is None:
        return AudioProvider()
    return factory(cfg, cfg.AUDIO_MODEL)


def _global_settings() -> Settings:
    from src.core.config import settings
    return settings