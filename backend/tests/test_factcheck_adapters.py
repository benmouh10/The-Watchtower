"""Roadmap item 3: PolitiFact + FactCheck.org adapters for claim reviews."""
from types import SimpleNamespace

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

from src.services import factcheck_adapter
from src.services.factcheck_adapter import (
    FactCheckOrgAdapter,
    GoogleFactCheckAdapter,
    PolitiFactAdapter,
    build_factcheck_adapter,
    enrich_claims_with_reviews,
)

_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>PolitiFact</title>
  <entry>
    <title>Barack Obama said the economy added 2 million jobs.</title>
    <link href="https://example.org/pf/economy"/>
    <updated>2026-09-01T12:00:00+00:00</updated>
    <summary>An analysis of the president's claim.</summary>
    <category term="mostly-true" label="Mostly True"/>
  </entry>
  <entry>
    <title>Unrelated headline about tomato imports.</title>
    <link href="https://example.org/pf/tomato"/>
    <updated>2026-09-02T12:00:00+00:00</updated>
    <summary>Nothing to do with the query.</summary>
    <category term="pants-on-fire" label="Pants on Fire"/>
  </entry>
</feed>
"""

_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>FactCheck.org</title>
  <item>
    <title>Did the president claim 2 million new jobs?</title>
    <link>https://example.org/fc/jobs</link>
    <pubDate>Wed, 02 Sep 2026 10:00:00 +0000</pubDate>
    <description>A detailed fact check.</description>
  </item>
  <item>
    <title>Do aliens ride bicycles?</title>
    <link>https://example.org/fc/aliens</link>
    <pubDate>Wed, 01 Sep 2026 10:00:00 +0000</pubDate>
    <description>An unrelated item.</description>
  </item>
</channel></rss>
"""


def _client_for(body: str) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _break_client() -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _claim(text: str):
    return SimpleNamespace(text=text, confidence=0.7, status="Disputed")


async def test_politifact_adapter_parses_verdicts():
    adapter = PolitiFactAdapter(
        base_url="https://politifact.test", http_client=_client_for(_ATOM)
    )
    reviews = await adapter.search("Obama said the economy added 2 million jobs")
    assert len(reviews) == 1
    assert reviews[0]["publisher"] == "PolitiFact"
    assert reviews[0]["title"].startswith("Barack Obama")
    assert reviews[0]["verdict"] == "Mostly True"
    assert reviews[0]["review_date"] == "2026-09-01"


async def test_factcheckorg_adapter_parses_headlines():
    adapter = FactCheckOrgAdapter(
        base_url="https://factcheck.test", http_client=_client_for(_RSS)
    )
    reviews = await adapter.search("president claimed jobs rose by 2 million")
    assert len(reviews) == 1
    assert reviews[0]["publisher"] == "FactCheck.org"
    assert reviews[0]["title"].startswith("Did the president")
    assert reviews[0]["verdict"] == ""
    assert reviews[0]["review_date"] == "2026-09-02"


async def test_no_vocabulary_overlap_returns_no_reviews():
    adapter = PolitiFactAdapter(
        base_url="https://politifact.test", http_client=_client_for(_ATOM)
    )
    reviews = await adapter.search("quantum robotics in mars orbit")
    assert reviews == []


async def test_enrich_uses_injected_politifact_adapter():
    adapter = PolitiFactAdapter(
        base_url="https://politifact.test", http_client=_client_for(_ATOM)
    )
    result = await enrich_claims_with_reviews([await _claim("Obama economy jobs added")], adapter)
    assert result["status"]["adapter"] == "politifact"
    assert result["status"]["status"] == "connected"
    assert result["status"]["reviews_found"] == 1
    assert result["claims"][0]["reviewed_by"] == "PolitiFact"
    assert result["claims"][0]["is_scored"] is True
    assert result["claims"][0]["confidence"] == 0.7


async def test_enrich_reports_degraded_on_transport_error():
    adapter = PolitiFactAdapter(
        base_url="https://politifact.test", http_client=_break_client()
    )
    result = await enrich_claims_with_reviews([await _claim("Obama economy jobs added")], adapter)
    assert result["status"]["status"] == "degraded"
    assert result["claims"][0]["external_reviews"] == []
    assert result["claims"][0]["reviewed_by"] is None


async def test_build_factcheck_adapter_disabled_by_default():
    assert build_factcheck_adapter() is None


async def test_build_factcheck_adapter_supports_new_providers(monkeypatch):
    monkeypatch.setattr(factcheck_adapter.settings, "FACTCHECK_PROVIDER", "politifact")
    adapter = build_factcheck_adapter()
    assert isinstance(adapter, PolitiFactAdapter)

    monkeypatch.setattr(factcheck_adapter.settings, "FACTCHECK_PROVIDER", "factcheck.org")
    adapter = build_factcheck_adapter()
    assert isinstance(adapter, FactCheckOrgAdapter)

    monkeypatch.setattr(factcheck_adapter.settings, "FACTCHECK_PROVIDER", "google")
    monkeypatch.setattr(factcheck_adapter.settings, "GOOGLE_FACTCHECK_API_KEY", "")
    with pytest.raises(ValueError):
        build_factcheck_adapter()

    monkeypatch.setattr(factcheck_adapter.settings, "FACTCHECK_PROVIDER", "nope")
    with pytest.raises(ValueError):
        build_factcheck_adapter()


async def test_enrich_reports_misconfigured_when_provider_keyed_but_empty(monkeypatch):
    monkeypatch.setattr(factcheck_adapter.settings, "FACTCHECK_PROVIDER", "google")
    monkeypatch.setattr(factcheck_adapter.settings, "GOOGLE_FACTCHECK_API_KEY", "")
    result = await enrich_claims_with_reviews([await _claim("a fairly long claim")])
    assert result["status"]["status"] == "misconfigured"
    assert result["status"]["adapter"] == "google"


async def test_google_adapter_still_parses_api_payload():
    payload = {
        "claims": [
            {
                "text": "The economy added two million jobs.",
                "claimReview": [
                    {
                        "publisher": {"name": "AP Fact Check", "site": "apnews.com"},
                        "url": "https://example.org/ap",
                        "title": "The economy added two million jobs",
                        "textualRating": "Mostly True",
                        "reviewDate": "2026-08-30T00:00:00Z",
                        "languageCode": "en",
                    }
                ],
            }
        ]
    }
    adapter = GoogleFactCheckAdapter(api_key="k")
    reviews = adapter._parse(payload, {"economy", "added", "million", "jobs"})
    assert len(reviews) == 1
    assert reviews[0]["publisher"] == "AP Fact Check"
    assert reviews[0]["verdict"] == "Mostly True"