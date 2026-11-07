"""
GlobeLens AI — MediaService (roadmap #10: video path + audio summarization).

The video path takes a publisher-declared og:video/twitter:player URL, fetches
its transcript (YouTube captions today via youtube-transcript-api) and hands the
transcript to the LLM for a cited, digestible summary. Nothing is faked: a video
host we cannot subtitle, a missing transcript library, or an unconfigured LLM
all raise MediaProviderUnavailable instead of inventing content.
"""
import logging
from typing import Optional, Tuple

from src.services.ai_providers import build_llm_provider
from src.services.media_providers import MediaProviderUnavailable

logger = logging.getLogger("src.services.media_service")

_SUPPORTED_HOSTS = ("youtube.com", "youtu.be")

VideoId = str


def video_id_for(url: str) -> Optional[VideoId]:
    """Extract a YouTube video id from a watch/shorts/embed/you.tube URL."""
    if not url:
        return None
    lowered = url.strip()
    if not lowered.startswith(("http://", "https://")):
        return None
    from urllib.parse import parse_qs, urlparse

    parts = urlparse(lowered)
    host = (parts.hostname or "").lower()
    if not any(h in host for h in _SUPPORTED_HOSTS):
        return None
    if "youtu.be" in host:
        vid = parts.path.strip("/")
        return vid or None
    if "/shorts/" in parts.path or "/embed/" in parts.path:
        chunks = [c for c in parts.path.split("/") if c]
        return chunks[-1] or None
    query = parse_qs(parts.query)
    return (query.get("v") or [None])[0]


def is_supported_video_url(url: str) -> bool:
    return video_id_for(url) is not None


class MediaService:
    async def fetch_transcript(self, video_url: str) -> Tuple[str, str]:
        """
        Return (transcript_text, source_label) for a supported video URL.
        Raises MediaProviderUnavailable for unsupported hosts or when the
        transcript library is not installed.
        """
        video_id = video_id_for(video_url)
        if video_id is None:
            raise MediaProviderUnavailable(
                "No supported video transcript provider covers this URL. "
                "Supported hosts: YouTube (youtube.com, youtu.be)."
            )
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
        except ImportError:
            raise MediaProviderUnavailable(
                "the youtube-transcript-api package is not installed; add it to "
                "requirements.txt to enable the video path."
            )
        try:
            fetched = YouTubeTranscriptApi.get_transcript(video_id)
        except Exception as exc:
            raise MediaProviderUnavailable(
                f"Could not fetch a transcript for {video_id}: {exc}"
            )
        chunks = [
            seg.get("text", "")
            for seg in (fetched or [])
            if isinstance(seg, dict) and seg.get("text")
        ]
        if not chunks:
            raise MediaProviderUnavailable(
                f"The video {video_id} exposes no captions to summarize."
            )
        return "\n".join(chunks), f"youtube:{video_id}"

    async def summarize_transcript(self, transcript: str) -> Tuple[str, str]:
        """
        Summarize a transcript with the configured LLM. Returns (summary,
        model). Raises MediaProviderUnavailable when no LLM is configured.
        """
        provider = build_llm_provider()
        if not provider.is_configured():
            raise MediaProviderUnavailable(
                "No LLM provider is configured (there is no LLM provider "
                "setting set), so the transcript cannot be summarized."
            )
        preview = transcript[:12000] if transcript else ""
        if not preview:
            raise MediaProviderUnavailable("Empty transcript; nothing to summarize.")
        system = (
            "You summarize video transcripts from news publishers for a "
            "fact-checking intelligence product. Return a concise, neutral "
            "summary (4-8 sentences) covering who, what, where and why it "
            "matters. Never invent facts not present in the transcript. Use no "
            "markdown."
        )
        raw = await provider.complete(
            system,
            f"TRANSCRIPT:\n{preview}",
            json_mode=False,
            temperature=0.2,
            max_tokens=1500,
        )
        return raw.strip() or "No summary could be generated.", provider.model