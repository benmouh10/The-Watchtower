"""
GlobeLens AI — External fact-check adapter
==========================================
*                *                 *
Third-party fact-checker feed behind the genuine "reviewed by" attribution on
analysed claims. Ground rules, so the feature can never invent a review:

  * No provider configured -> the adapter is None and NO network call is made;
    claim payloads carry honest nulls. This is the out-of-the-box default.
  * A provider selected without its credentials is refused loudly (ValueError),
    exactly like the push/email senders — never silently skipped, never routed
    to another provider's key.
  * Every "reviewed by" comes from a real API response. A configured provider
    that times out or errors is reported as degraded and yields nulls, never a
    fabricated attribution.
  * A returned claim is only attached to a query when they share real
    vocabulary. Fact Check Tools search is fuzzy, so without this filter an
    unrelated fact-check could be shown as reviewing the claim.

The HTTP base URL and client are injectable so the adapter is testable against
a local stub without any live API key; the production default is Google's
public Fact Check Tools API.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import httpx
import structlog

from src.core.config import settings

logger = structlog.get_logger()

# Tokens too common to distinguish one fact-check from its query.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "while",
    "of", "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "to", "from",
    "up", "down", "in", "out", "on", "off", "over", "under", "again", "further",
    "once", "here", "there", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should",
    "may", "might", "must", "can", "could", "says", "said", "say", "report",
    "reports", "reported", "according", "sources", "source", "claim", "claims",
})

# A returned claim must share at least this many significant tokens with the
# query before it may be shown as a review of that query.
_MIN_SHARED_TOKENS = 2
# The search query is capped: the leading part of a claim carries its subject.
_MAX_QUERY_CHARS = 300


def _significant_tokens(text: str) -> set:
    tokens = set()
    for token in text.lower().split():
        cleaned = "".join(ch for ch in token if ch.isalnum())
        if len(cleaned) >= 3 and cleaned not in _STOPWORDS:
            tokens.add(cleaned)
    return tokens


@dataclass
class ExternalReview:
    """One fact-checking article returned by the Google Fact Check Tools API."""

    publisher: str
    url: str
    title: str
    verdict: str
    review_date: str
    language: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GoogleFactCheckAdapter:
    """
    Adapter over ``claims:search``. Search returns the claim itself plus one or
    more claimReview articles; each review maps to an ExternalReview.
    """

    provider = "google"

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = (base_url or "https://factchecktools.googleapis.com").rstrip("/")
        self._timeout = timeout if timeout is not None else settings.FACTCHECK_TIMEOUT_SECONDS
        # Injectable client enables local-stub testing; ownership stays with the
        # caller when provided, otherwise the adapter creates and closes its own.
        self._client = http_client

    async def search(
        self,
        query: str,
        language: str = "en",
        page_size: int = 5,
        max_age_days: int = 365,
    ) -> List[Dict[str, Any]]:
        """
        Look up third-party fact-checks for one claim sentence.

        Returns a list of ExternalReview dicts (most recent first, deduplicated
        by review URL). Transport and API errors raise: the enricher converts
        any raise into a degraded report, so a 403 is never silently conflated
        with "no reviews found".
        """
        tokens = _significant_tokens(query)
        if len(tokens) < _MIN_SHARED_TOKENS:
            return []

        params: Dict[str, Any] = {
            "key": self._api_key,
            "query": (query or "").strip()[:_MAX_QUERY_CHARS],
            "languageCode": language,
            "pageSize": max(1, min(20, page_size or 5)),
        }
        if max_age_days:
            params["maxAgeDays"] = max_age_days

        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.get(
                f"{self._base_url}/v1alpha1/claims:search",
                params=params,
            )
        finally:
            if own_client:
                await client.aclose()

        # Let every failure surface to the enricher, which reports the adapter
        # as degraded instead of a tidy-but-untrue "no reviews found".
        response.raise_for_status()
        payload = response.json()

        return self._parse(payload, tokens)

    def _parse(self, payload: Dict[str, Any], tokens: set) -> List[Dict[str, Any]]:
        reviews: List[ExternalReview] = []
        seen: set = set()

        for claim in payload.get("claims", []) or []:
            claim_text = (claim.get("text") or "").strip()
            # The API searches broadly; only attach reviews whose own claim
            # shares real vocabulary with the query.
            if len(tokens & _significant_tokens(claim_text)) < _MIN_SHARED_TOKENS:
                continue
            for review in claim.get("claimReview", []) or []:
                url = (review.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                publisher = review.get("publisher") or {}
                reviews.append(ExternalReview(
                    publisher=(publisher.get("name") or publisher.get("site") or "Unknown").strip(),
                    url=url,
                    title=(review.get("title") or "").strip(),
                    verdict=(review.get("textualRating") or "").strip(),
                    review_date=(review.get("reviewDate") or "").strip(),
                    language=(review.get("languageCode") or "").strip(),
                ))

        # RFC3339 timestamps sort lexicographically, so newest review first.
        reviews.sort(key=lambda r: r.review_date, reverse=True)
        return [r.to_dict() for r in reviews]


_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _parse_atom_entries(xml_text: str) -> List[Dict[str, Any]]:
    """Parse Atom entries into {title, url, updated, summary, ruling}.

    PolitiFact's /factchecks/atom.xml carries each ruling in the entry's
    <category label="..."/>, so the label becomes the ExternalReview verdict.
    """
    root = ET.fromstring(xml_text)
    entries: List[Dict[str, Any]] = []
    for entry in root.findall(".//atom:entry", _ATOM_NS):
        title = (entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or "").strip()
        link_el = entry.find("atom:link", _ATOM_NS)
        url = ((link_el.get("href") if link_el is not None else "") or "").strip()
        updated = (entry.findtext("atom:updated", default="", namespaces=_ATOM_NS) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=_ATOM_NS) or "").strip()
        category = entry.find("atom:category", _ATOM_NS)
        ruling = ((category.get("label") if category is not None else "") or "").strip()
        if title and url:
            entries.append({
                "title": title,
                "url": url,
                "date": updated,
                "summary": summary,
                "ruling": ruling,
            })
    return entries


def _parse_rss_entries(xml_text: str) -> List[Dict[str, Any]]:
    """Parse RSS 2.0 items into {title, url, date, summary}.

    FactCheck.org publishes its checks as regular articles without a machine
    readable ruling, so the verdict stays empty and the headline (itself the
    claim under the microscope) is the honest description of the check.
    """
    root = ET.fromstring(xml_text)
    entries: List[Dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if title and url:
            entries.append({
                "title": title,
                "url": url,
                "date": (item.findtext("pubDate") or "").strip(),
                "summary": description,
                "ruling": "",
            })
    return entries


def _normalise_date(raw: str) -> Optional[str]:
    """RFC3339 or RFC822 -> ISO date (YYYY-MM-DD); None when unparseable."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        if "T" in raw or raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        parsed = parsedate_to_datetime(raw)
        if parsed is None:
            return None
        return parsed.astimezone(timezone.utc).date().isoformat()
    except (ValueError, TypeError):
        return None


class RssFactCheckAdapter:
    """
    Shared search flow for public fact-checker RSS/Atom feeds.

    Both supported publishers expose their latest checks as a feed rather than
    a search API, so a "search" is: fetch the feed, keep entries that share
    real vocabulary with the query, cap by age/amount. No key required, which
    is also why the standalone runtime defaults these providers OFF — an empty
    FACTCHECK_PROVIDER performs no request at all.
    """

    provider: str = "rss"
    publisher: str = ""
    feed_path: str = "/"

    def __init__(
        self,
        base_url: str,
        timeout: Optional[float] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout if timeout is not None else settings.FACTCHECK_TIMEOUT_SECONDS
        self._client = http_client

    async def _fetch_feed(self) -> str:
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.get(f"{self._base_url}{self.feed_path}")
        finally:
            if own_client:
                await client.aclose()
        response.raise_for_status()
        return response.text

    def _parse_entries(self, xml_text: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def _verdict(self, entry: Dict[str, Any]) -> str:
        return entry.get("ruling") or ""

    def _date(self, entry: Dict[str, Any]) -> Optional[str]:
        return _normalise_date(entry.get("date") or "")

    async def search(
        self,
        query: str,
        language: str = "en",
        page_size: int = 5,
        max_age_days: int = 365,
    ) -> List[Dict[str, Any]]:
        tokens = _significant_tokens(query)
        if len(tokens) < _MIN_SHARED_TOKENS:
            return []

        xml_text = await self._fetch_feed()
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, max_age_days or 365))

        reviews: List[ExternalReview] = []
        seen: set = set()
        for entry in self._parse_entries(xml_text):
            checkable = f"{entry.get('title', '')} {entry.get('summary', '')}"
            if len(tokens & _significant_tokens(checkable)) < _MIN_SHARED_TOKENS:
                continue
            url = entry.get("url", "")
            if not url or url in seen:
                continue
            review_date = self._date(entry)
            if review_date and max_age_days:
                try:
                    if datetime.fromisoformat(review_date).date() < cutoff.date():
                        continue
                except ValueError:
                    pass
            seen.add(url)
            reviews.append(ExternalReview(
                publisher=self.publisher,
                url=url,
                title=entry.get("title") or "",
                verdict=self._verdict(entry),
                review_date=review_date or "",
                language=language,
            ))

        reviews.sort(key=lambda r: r.review_date, reverse=True)
        return [r.to_dict() for r in reviews[: max(1, min(50, page_size or 5))]]


class PolitiFactAdapter(RssFactCheckAdapter):
    """PolitiFact's public rulings feed, no API key required."""

    provider = "politifact"
    publisher = "PolitiFact"
    feed_path = "/factchecks/atom.xml"

    def _parse_entries(self, xml_text: str) -> List[Dict[str, Any]]:
        return _parse_atom_entries(xml_text)

    def _verdict(self, entry: Dict[str, Any]) -> str:
        return entry.get("ruling") or ""

    def _date(self, entry: Dict[str, Any]) -> Optional[str]:
        return _normalise_date(entry.get("date") or "")


class FactCheckOrgAdapter(RssFactCheckAdapter):
    """FactCheck.org's announcement feed; checks are headline-detailed articles."""

    provider = "factcheck.org"
    publisher = "FactCheck.org"
    feed_path = "/feed/"

    def _parse_entries(self, xml_text: str) -> List[Dict[str, Any]]:
        return _parse_rss_entries(xml_text)

    def _date(self, entry: Dict[str, Any]) -> Optional[str]:
        return _normalise_date(entry.get("date") or "")


def build_factcheck_adapter():
    """
    Return the configured external fact-check adapter, or None when disabled.

    Disabled means no external call ever happens. Selecting a provider without
    its key is refused loudly — mirroring the push/email sender factories — so a
    misconfiguration becomes an explicit error instead of an unmentioned gap.
    The two RSS/Atom providers (PolitiFact, FactCheck.org) need no credentials,
    so the loud-refusal rule only applies to the keyed Google API.
    """
    provider = (settings.FACTCHECK_PROVIDER or "").strip().lower()
    if not provider:
        return None
    if provider == "google":
        api_key = (settings.GOOGLE_FACTCHECK_API_KEY or "").strip()
        if not api_key:
            raise ValueError(
                "FACTCHECK_PROVIDER='google' but GOOGLE_FACTCHECK_API_KEY is "
                "not set. Refusing to guess another provider's credentials."
            )
        logger.info("External fact-check adapter enabled", provider="google")
        return GoogleFactCheckAdapter(api_key=api_key)
    if provider == "politifact":
        logger.info("External fact-check adapter enabled", provider="politifact")
        return PolitiFactAdapter(base_url=settings.POLITIFACT_BASE_URL)
    if provider == "factcheck.org":
        logger.info("External fact-check adapter enabled", provider="factcheck.org")
        return FactCheckOrgAdapter(base_url=settings.FACTCHECKORG_BASE_URL)
    raise ValueError(
        f"Unknown FACTCHECK_PROVIDER {provider!r}. Supported: 'google', "
        "'politifact', 'factcheck.org' (or leave empty to disable)."
    )


def _claim_entry(claim: Any, reviews: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build one claim payload, always preserving the model's own confidence.

    `confidence`/`is_scored` come from the LLM analysis and must survive every
    path (external reviews disabled, misconfigured, connected, degraded), so
    the confidence meter renders independently of third-party review feeds.
    """
    confidence = getattr(claim, "confidence", None)
    return {
        "text": getattr(claim, "text", str(claim)),
        "status": getattr(claim, "status", None),
        "confidence": confidence,
        "is_scored": confidence is not None,
        "reviewed_by": (reviews[0] or {}).get("publisher") if reviews else None,
        "review_url": (reviews[0] or {}).get("url") if reviews else None,
        "external_reviews": reviews or [],
    }


def _attach(claims: List[Any], claim_reviews: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Merge per-claim external reviews onto the claim payload shape."""
    return [_claim_entry(claim, reviews) for claim, reviews in zip(claims, claim_reviews)]


async def enrich_claims_with_reviews(
    claims: List[Any],
    adapter: Optional[GoogleFactCheckAdapter] = None,
) -> Dict[str, Any]:
    """
    Attach real third-party reviews to the analysed claims.

    Returns {"claims": [...], "status": {adapter, status, reviews_found}} where
    status is one of:
      * "disabled"      — no provider configured; no call was made, all nulls.
      * "misconfigured" — a provider was chosen without its key; refused.
      * "connected"     — lookups ran and no provider error occurred.
      * "degraded"      — lookups ran, some/none failed; errors logged, nulls on
                          the affected claims.

    The optional ``adapter`` is an injection seam for tests; production callers
    leave it None and the configured factory decides between disabled and live.
    ``FACTCHECK_MAX_LOOKUPS_PER_REQUEST`` caps how many claims bill a lookup.
    """
    disabled_status = {"adapter": None, "status": "disabled", "reviews_found": 0}

    if adapter is None:
        try:
            adapter = build_factcheck_adapter()
        except ValueError as cfg_err:
            logger.error("External fact-check adapter refused to start", error=str(cfg_err))
            return {
                "claims": [_claim_entry(claim) for claim in claims],
                "status": {
                    "adapter": (settings.FACTCHECK_PROVIDER or "").strip(),
                    "status": "misconfigured",
                    "reviews_found": 0,
                },
            }
        if adapter is None:
            return {"claims": [_claim_entry(claim) for claim in claims], "status": disabled_status}

    cap = max(0, int(settings.FACTCHECK_MAX_LOOKUPS_PER_REQUEST))
    found_by_claim: List[Dict[str, Any]] = []
    degraded = False
    reviews_found = 0

    for idx, claim in enumerate(claims):
        reviews: List[Dict[str, Any]] = []
        if idx < cap:
            try:
                reviews = await adapter.search(getattr(claim, "text", str(claim)))
            except Exception as err:  # provider outage must not kill the result
                degraded = True
                logger.error("External fact-check lookup failed", error=str(err))
                reviews = []
            if reviews:
                reviews_found += 1
        found_by_claim.append(reviews)

    if cap and len(claims) > cap:
        logger.info(
            "External fact-check lookups capped",
            lookups=cap,
            claims=len(claims),
        )

    return {
        "claims": _attach(claims, found_by_claim),
        "status": {
            "adapter": getattr(adapter, "provider", "google"),
            "status": "degraded" if degraded else "connected",
            "reviews_found": reviews_found,
        },
    }