"""Video path + transcript summarization (roadmap #10).

Unit coverage for the pieces that must work without network or paid keys:
YouTube video-id parsing, og:video extraction, and the honest-refusal wiring of
the analyze-video / summarize endpoints against faked transcript + LLM outputs.
"""

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

SAMPLE_TRANSCRIPT = [
    {"text": "The central bank announced a surprise rate cut", "start": 0.0},
    {"text": "citing cooling inflation and slower growth", "start": 3.1},
]


async def test_video_id_parsing():
    from src.services.media_service import video_id_for, is_supported_video_url

    cases = {
        "https://www.youtube.com/watch?v=abc123XYZ-4": "abc123XYZ-4",
        "https://youtu.be/def456Abc-9?t=30": "def456Abc-9",
        "https://m.youtube.com/watch?v=xYz987Qw-1": "xYz987Qw-1",
        "https://www.youtube.com/shorts/ghi789Jk-2": "ghi789Jk-2",
        "https://www.youtube.com/embed/jkl012Mn-3": "jkl012Mn-3",
        "https://example.org/article": None,
        "ftp://youtu.be/abc": None,
        "": None,
    }
    for url, expected in cases.items():
        assert video_id_for(url) == expected, url
        assert is_supported_video_url(url) == (expected is not None)


async def test_og_video_extraction():
    from bs4 import BeautifulSoup

    from src.services.scraper_service import resolve_og_video

    page = BeautifulSoup(
        """
        <html><head>
          <meta property="og:image" content="https://cdn.example.org/pic.jpg">
          <meta property="og:video" content="https://www.youtube.com/watch?v=abc123XYZ-4">
          <meta name="twitter:player" content="https://player.example.org/embed/xyz">
        </head></html>""",
        "html.parser",
    )
    assert resolve_og_video(page) == "https://www.youtube.com/watch?v=abc123XYZ-4"

    no_video = BeautifulSoup("<html><head></head></html>", "html.parser")
    assert resolve_og_video(no_video) is None


async def test_fetch_transcript_success(monkeypatch):
    import sys
    from types import ModuleType

    from src.services.media_service import MediaService

    fake_api = type(
        "API",
        (),
        {"get_transcript": staticmethod(lambda video_id: SAMPLE_TRANSCRIPT)},
    )()
    fake_module = ModuleType("youtube_transcript_api")
    fake_module.YouTubeTranscriptApi = fake_api
    monkeypatch.delitem(sys.modules, "youtube_transcript_api", raising=False)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_module)

    transcript, source = await MediaService().fetch_transcript(
        "https://www.youtube.com/watch?v=abc123XYZ-4"
    )
    assert "central bank" in transcript
    assert source == "youtube:abc123XYZ-4"


async def test_fetch_transcript_library_missing(monkeypatch):
    import builtins
    import sys

    from src.services.media_providers import MediaProviderUnavailable
    from src.services.media_service import MediaService

    real_import = builtins.__import__

    def _block_yt(name, *args, **kwargs):
        if name == "youtube_transcript_api":
            raise ImportError("no such module")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "youtube_transcript_api", raising=False)
    monkeypatch.setattr(builtins, "__import__", _block_yt)

    with pytest.raises(MediaProviderUnavailable) as exc_info:
        await MediaService().fetch_transcript("https://youtu.be/def456Abc-9")
    assert "youtube-transcript-api" in str(exc_info.value)


async def test_fetch_transcript_unsupported_host():
    from src.services.media_providers import MediaProviderUnavailable
    from src.services.media_service import MediaService

    with pytest.raises(MediaProviderUnavailable) as exc_info:
        await MediaService().fetch_transcript("https://vimeo.com/123")
    assert "Supported hosts" in str(exc_info.value)


async def test_summarize_transcript_uses_provider(monkeypatch):
    from src.services import media_service as media_module
    from src.services.media_service import MediaService

    class FakeProvider:
        name = "fake-llm"
        model = "fake-model-1"

        def is_configured(self):
            return True

        async def complete(self, system, user, **kwargs):
            assert "TRANSCRIPT" in user
            return "The bank surprised markets with a rate cut."

    monkeypatch.setattr(media_module, "build_llm_provider", lambda: FakeProvider())
    summary, model = await MediaService().summarize_transcript("rate cut announced")
    assert summary == "The bank surprised markets with a rate cut."
    assert model == "fake-model-1"


async def test_summarize_transcript_no_llm(monkeypatch):
    from src.services import media_service as media_module
    from src.services.media_providers import MediaProviderUnavailable
    from src.services.media_service import MediaService

    class Unconfigured:
        name = ""
        model = ""

        def is_configured(self):
            return False

        async def complete(self, *a, **k):
            raise AssertionError("must not be called")

    monkeypatch.setattr(media_module, "build_llm_provider", lambda: Unconfigured())
    with pytest.raises(MediaProviderUnavailable) as exc_info:
        await MediaService().summarize_transcript("some transcript")
    assert "No LLM provider is configured" in str(exc_info.value)


async def test_summarize_endpoint_uses_fake_provider(client, auth, monkeypatch):
    from src.services import media_service as media_module
    from src.services.media_service import MediaService

    class FakeProvider:
        name = "fake-llm"
        model = "fake-model-1"

        def is_configured(self):
            return True

        async def complete(self, system, user, **kwargs):
            return "A concise summary of the transcript."

    monkeypatch.setattr(media_module, "build_llm_provider", lambda: FakeProvider())
    identity = await auth()
    resp = await client.post(
        "/api/v1/media/summarize",
        data={"text": "The central bank cut rates citing cooling inflation."},
        headers=identity["headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == "A concise summary of the transcript."
    assert body["model"] == "fake-model-1"
    assert body["ai_generated"] is True


async def test_analyze_video_endpoint_persists_article(client, auth, monkeypatch):
    import sys
    from types import ModuleType

    from sqlalchemy import select

    from src.services import media_service as media_module
    from src.core.database import AsyncSessionFactory
    from src.data.entities.models import Article

    class FakeProvider:
        name = "fake-llm"
        model = "fake-model-1"

        def is_configured(self):
            return True

        async def complete(self, system, user, **kwargs):
            return "The Fed surprised markets with a rate cut."

    fake_api = type(
        "API",
        (),
        {
            "get_transcript": staticmethod(
                lambda video_id: [{"text": "Fed announces surprise rate cut", "start": 0.0}]
            )
        },
    )()
    fake_module = ModuleType("youtube_transcript_api")
    fake_module.YouTubeTranscriptApi = fake_api
    monkeypatch.delitem(sys.modules, "youtube_transcript_api", raising=False)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_module)
    monkeypatch.setattr(media_module, "build_llm_provider", lambda: FakeProvider())

    identity = await auth()
    async with AsyncSessionFactory() as db:
        article = Article(
            title="Video article",
            url="https://example.org/video-story",
            content="Placeholder body",
        )
        db.add(article)
        await db.commit()
        await db.refresh(article)
        article_id = article.id

    resp = await client.post(
        "/api/v1/media/analyze-video",
        data={
            "video_url": "https://www.youtube.com/watch?v=abc123XYZ-4",
            "article_id": str(article_id),
        },
        headers=identity["headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transcript"] == "Fed announces surprise rate cut"
    assert body["summary"] == "The Fed surprised markets with a rate cut."
    assert body["persisted_to_article"] is True

    async with AsyncSessionFactory() as db:
        reloaded = (
            await db.execute(select(Article).where(Article.id == article_id))
        ).scalars().first()
        assert reloaded.video_url == "https://www.youtube.com/watch?v=abc123XYZ-4"
        assert reloaded.video_transcript == "Fed announces surprise rate cut"
        assert reloaded.video_summary == "The Fed surprised markets with a rate cut."
        assert reloaded.video_summary_model == "fake-model-1"
        assert reloaded.video_processed_at is not None
