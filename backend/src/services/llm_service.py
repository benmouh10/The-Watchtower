"""
GlobeLens AI — LLMService
===========================
Prompts, parsing and persistence rules that sit above the config-gated provider
adapters (openai / anthropic / grok / gemini / nvidia / bedrock / ollama /
cohere). See src.services.ai_providers for the transport layer.
Pipeline Step 4: CLUSTERED → PROCESSED
"""
import uuid
import asyncio
import re
import structlog
from typing import Any, List, Tuple

from src.core.config import settings
from src.core.database import AsyncSessionFactory
from src.data.repositories.article_repository import ArticleRepository
from src.data.repositories.event_repository import EventRepository
from src.api.schemas.intelligence import (
    EventIntelligenceResponse,
    FactCheckResponse,
    IntelligenceTopic,
    IntelligenceBiasLean,
    ClaimAnalysis,
      HistoricalMatch
  )

logger = structlog.get_logger()


_CITATION_PATTERN = re.compile(r"\[(\d+)\]")


def sanitize_citations(summary: str, article_count: int) -> Tuple[str, List[int]]:
    """
    Strip inline [N] markers that point outside the cluster and report the indices
    actually referenced.

    A hallucinated index would otherwise render in the UI as a dead citation that
    looks identical to a real one, so it is removed rather than shown.
    """
    valid: set = set()

    def _replace(match: "re.Match") -> str:
        index = int(match.group(1))
        if 1 <= index <= article_count:
            valid.add(index)
            return match.group(0)
        return ""

    cleaned = _CITATION_PATTERN.sub(_replace, summary or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    return cleaned, sorted(valid)


def sanitize_claims(claims: Any, article_count: int) -> List[ClaimAnalysis]:
    """
    Drop per-claim citation indices that point outside the cluster.

    The model is asked to attribute each claim to supporting article indices, and
    it can return indices that do not exist. Unlike an inline [N] marker, which
    is just removed from the text, a per-claim index is rendered as "this outlet
    reported this" — so an out-of-range value would be a fabricated attribution
    on exactly the surface meant to make attribution trustworthy.

    An out-of-range index is dropped, and a claim left with no valid index keeps
    an empty list, which the UI shows as "no source in this cluster supports
    this claim" rather than silently borrowing someone else's citation.
    """
    if not claims or not isinstance(claims, list):
        return []

    cleaned: List[ClaimAnalysis] = []
    for raw in claims:
        if not isinstance(raw, dict):
            continue
        text = (raw.get("text") or "").strip()
        if not text:
            continue

        # Drop anything outside 1..article_count, de-duplicate, keep order.
        indices: List[int] = []
        for value in raw.get("citation_indices") or []:
            try:
                idx = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= idx <= article_count and idx not in indices:
                indices.append(idx)

        # An unparseable status is a validation failure, not something to
        # coerce: ClaimAnalysis will reject it and the whole claim is dropped
        # rather than stored under a guessed verdict.
        try:
            candidate = ClaimAnalysis(
                text=text,
                status=raw.get("status"),
                confidence=raw.get("confidence"),
                citation_indices=indices,
                # Never trust a model-supplied reviewer; no third-party feed is
                # connected, so an attribution would be invented.
                reviewed_by=None,
                review_url=None,
            )
        except Exception as exc:
            logger.warn(
                "Discarding malformed claim from synthesis",
                error=str(exc),
                text=text[:80],
            )
            continue

        cleaned.append(candidate)

    return cleaned



class LLMService:
    """
    Prompts, parsing and persistence above a config-gated provider adapter.
    Selects the adapter from settings.LLM_PROVIDER; see ai_providers.
    """

    def __init__(self) -> None:
        # Provider adapter is chosen purely from settings.LLM_PROVIDER. The
        # adapter owns the client and its transport differences; this service
        # only owns the prompts, the parsing, and the persistence rules.
        from src.services.ai_providers import build_llm_provider
        self._provider = build_llm_provider()
        self._model = self._provider.model
        self._provider_name = self._provider.name
        logger.info("Initializing LLMService with provider", provider=self._provider_name)

    async def analyze_event_cluster(self, articles_content: List[str]) -> EventIntelligenceResponse:
        """
        Synthesize articles content into a structured event intelligence schema.
        Instructs the LLM to act as an objective, cross-border investigative journalist.
        """
        system_prompt = (
            "You are an objective, cross-border investigative journalist. "
            "Your task is to analyze a cluster of news articles about a single event and synthesize them. "
            "You must return a JSON object that strictly adheres to the following JSON Schema. "
            "Ensure the summary is detailed and contains a minimum of 3 paragraphs. "
            "CRITICAL: Whenever you state a claim or fact, you MUST include an inline citation referencing the article index it came from, formatted as [1], [2], etc. (e.g., 'The agreement was signed today [1][3].'). "
            "Only cite article indices that actually exist in the input. Never invent an index.\n\n"
            "You must ALSO break the synthesis into discrete claims in a 'claims' array. "
            "Each claim must state which article indices support it, so a reader can see "
            "exactly which outlets back which specific assertion. Rules for claims:\n"
            "  - 'status' is exactly one of: Corroborated, Partially verified, Disputed, Unverified.\n"
            "    Use 'Partially verified' when some but not all cited articles support the claim; "
            "do not round a partial result up to Corroborated or down to Unverified.\n"
            "  - 'confidence' is your confidence in the verdict from 0.0 to 1.0. Omit it if you "
            "cannot estimate it; a missing value is shown as 'not scored' and is preferable to a guess.\n"
            "  - 'citation_indices' is a list of the 1-based article indices supporting that claim. "
            "Only use indices that exist. An empty list means nothing in the input supports it.\n"
            "  - Do NOT set 'reviewed_by' or 'review_url'. No third-party fact-checker is connected, "
            "so any value there would be invented.\n\n"
            "Required JSON Schema:\n"
            "{\n"
            "  \"summary\": \"string (Objective synthesis, minimum 3 paragraphs, MUST include inline citations like [1] or [1][2] for claims)\",\n"
            "  \"topic\": \"string (Exactly one of: POLITICS, ECONOMY, TECHNOLOGY, SPORTS, HEALTH, WORLD)\",\n"
            "  \"bias_lean\": \"string (Exactly one of: LEFT, CENTER_LEFT, CENTER, CENTER_RIGHT, RIGHT)\",\n"
            "  \"location_country\": \"string (Primary geographic focus country)\",\n"
            "  \"latitude\": \"float (Approximate latitude)\",\n"
            "  \"longitude\": \"float (Approximate longitude)\",\n"
            "  \"importance_score\": \"float (From 0.0 to 10.0 representing significance)\",\n"
            "  \"claims\": [\n"
            "    {\n"
            "      \"text\": \"string (A single discrete assertion from the synthesis)\",\n"
            "      \"status\": \"string (Corroborated | Partially verified | Disputed | Unverified)\",\n"
            "      \"confidence\": \"float (0.0-1.0, optional)\",\n"
            "      \"citation_indices\": [\"int (1-based article indices supporting this claim)\"]\n"
            "    }\n"
            "  ]\n"
            "}"
        )

        indexed_articles = []
        for i, content in enumerate(articles_content, 1):
            indexed_articles.append(f"--- Article [{i}] ---\n{content}")
        concatenated_articles = "\n\n".join(indexed_articles)

        user_prompt = f"Analyze the following articles belonging to the same event cluster and generate the intelligence report:\n\n{concatenated_articles}"

        logger.info(
            "Calling Chat Completions API for event synthesis",
            provider=self._provider_name,
            model=self._model,
            num_articles=len(articles_content)
        )

        try:
            raw_content = await self._provider.complete(
                system_prompt,
                user_prompt,
                json_mode=True,
                temperature=0.1,
            )

            if not raw_content:
                raise RuntimeError("Provider returned an empty completion")
            # Parse and validate using our Pydantic schema
            validated_response = EventIntelligenceResponse.model_validate_json(raw_content)

            validated_response.summary, cited = sanitize_citations(
                validated_response.summary, len(articles_content)
            )
            validated_response.citations = cited

            # Per-claim attribution, sanitized against the same article count so
            # a claim cannot be credited to an article that does not exist.
            validated_response.claims = sanitize_claims(
                validated_response.claims, len(articles_content)
            )

            if not cited:
                logger.warn(
                    "Synthesis contains no resolvable citations",
                    articles=len(articles_content),
                )

            # A claim that survived sanitizing but cites nothing is still real
            # analysis, so it is kept; it will render as unsupported.
            unsupported = sum(1 for c in validated_response.claims if not c.citation_indices)
            if unsupported:
                logger.info(
                    "Claims with no in-cluster supporting source",
                    count=unsupported,
                    total=len(validated_response.claims),
                )

            return validated_response
        except Exception as err:
            # Deliberately no fabricated fallback.
            #
            # This path previously invented a "synthesis" by string-matching
            # keywords for a topic, looking up hard-coded lat/lon, and
            # assigning bias_lean=CENTER and importance_score=7.0. A fabricated
            # CENTER rating is the exact failure the nullable bias columns
            # (migrations b3f1c8a27d90 / e4b7a2c19d63) exist to prevent, and
            # fabricating them here bypassed that guarantee at the one place
            # the value was actually produced.
            #
            # Raising lets the caller report an honest 503 and leaves the event
            # unsynthesised, which is strictly better than a confident-looking
            # fabrication. Callers must handle this.
            logger.error("LLM event analysis failed; no synthesis produced", error=str(err))
            raise RuntimeError(
                "LLM event analysis failed. No synthesis was produced because "
                "substituting invented analysis would be a fabrication."
            ) from err

    async def analyze_claim_credibility(self, text_content: str) -> FactCheckResponse:
        """
        Analyze the credibility of a scraped article or text claim.
        Instructs the LLM to return a structured credibility assessment.
        Supports real-time search grounding if Tavily API is configured.
        """
        search_context = ""
        if settings.TAVILY_API_KEY:
            try:
                from src.services.tavily_service import TavilyService
                tavily = TavilyService()
                search_results = await tavily.search_web(text_content)
                if search_results:
                    search_context = "\n\n=== REAL-TIME WEB SEARCH RESULTS (GROUND TRUTH REFERENCES) ===\n"
                    for idx, result in enumerate(search_results, 1):
                        search_context += f"Reference [{idx}]:\n"
                        search_context += f"  Title: {result.get('title')}\n"
                        search_context += f"  URL: {result.get('url')}\n"
                        search_context += f"  Snippet: {result.get('content')}\n\n"
            except Exception as search_err:
                logger.error("Failed to execute search grounding", error=str(search_err))

        system_prompt = (
            "You are an objective, cross-border fact-checker. "
            "Your task is to analyze the provided article content or text claim and evaluate its credibility. "
            "You must return a JSON object that strictly adheres to the following JSON Schema.\n\n"
        )
        if search_context:
            system_prompt += (
                "You have been provided with real-time web search results (Ground Truth References) related to the claim or article. "
                "Use this context as the ground truth to evaluate whether the claims are Corroborated, Disputed, or Unverified. "
                "The 'independent_cross_references' field should specify the number of unique, independent domain references supporting the claim. "
                "You can also use 'historical_matches' to represent actual related news events found in the search references.\n\n"
            )
        else:
            system_prompt += (
                "Ground your reasoning in objective global news context and check the internal structure/bias. "
                "Evaluate if claims are Corroborated, Disputed, or Unverified based on available metadata.\n\n"
            )

        system_prompt += (
            "Required JSON Schema:\n"
            "{\n"
            "  \"credibility_score\": \"int (Overall trustworthiness from 0 to 100 based on the search references or context)\",\n"
            "  \"trust_risks\": [\"string (Specific risks identified, e.g. Loaded Language, Unverified Authorship, Contradicts Search Results)\"],\n"
            "  \"independent_cross_references\": \"int (Estimated independent corroboration source count from search references)\",\n"
            "  \"claims\": [\n"
            "    {\"text\": \"string (Extracted key claim)\", \"status\": \"string (Exactly one of: Corroborated, Disputed, Unverified)\"}\n"
            "  ],\n"
            "  \"historical_matches\": [\n"
            "    {\"title\": \"string (Historical news event name)\", \"last_active\": \"string (Relative time, e.g. '2 days ago')\", \"match_percentage\": \"int (0-100)\"}\n"
            "  ],\n"
            "  \"summary\": \"string (A brief 2-3 sentence report summary explaining the credibility verdict grounded in the search references if available)\"\n"
            "}"
        )

        user_prompt = f"Perform credibility analysis and fact-checking on the following content:\n\n{text_content}"
        if search_context:
            user_prompt += search_context

        logger.info(
            "Calling Chat Completions API for claim credibility check",
            provider=self._provider_name,
            model=self._model,
            grounded=bool(search_context)
        )

        try:
            raw_content = await self._provider.complete(
                system_prompt,
                user_prompt,
                json_mode=True,
                temperature=0.1,
            )

            if not raw_content:
                raise RuntimeError("Provider returned an empty completion")
            # Validate using Pydantic
            validated = FactCheckResponse.model_validate_json(raw_content)
            return validated
        except Exception as err:
            # Deliberately no fabricated fallback. The previous version invented a
            # credibility score (85 minus arbitrary deductions), marked claims
            # "Corroborated", claimed 4 independent cross-references, and attached
            # two hardcoded historical stories with fixed match percentages that
            # had no relationship to the submitted text. That is invented evidence
            # presented as analysis, and callers persist/display it. Callers convert
            # this into a 503 so the absence of a result is visible instead.
            logger.error("LLM claim credibility check failed; refusing to fabricate a result", error=str(err))
            raise RuntimeError("Claim credibility analysis unavailable") from err

    async def process_pending_events(self) -> Tuple[int, int]:
        """
        Fetches unprocessed events, pulls their associated articles, calls analyze_event_cluster,
        and commits the intelligence data via EventRepository.
        """
        logger.info("Starting processing of pending events")
        processed_count = 0
        error_count = 0

        async with AsyncSessionFactory() as session:
            event_repo = EventRepository(session)
            article_repo = ArticleRepository(session)

            try:
                events = await event_repo.get_unprocessed_events(limit=20)
                logger.info("Fetched unprocessed events for LLM synthesis", count=len(events))

                for event in events:
                    try:
                        # Pull associated articles
                        articles = await article_repo.find_by_event(event.id)
                        if not articles:
                            logger.warn("Event has no associated articles, skipping", event_id=str(event.id))
                            continue
                        
                        # Extract contents, keeping the article id aligned to each
                        # position so the summary's [N] markers stay resolvable.
                        articles_content = []
                        ordered_article_ids: List[str] = []
                        for art in articles:
                            if art.content and art.content.strip():
                                articles_content.append(f"Title: {art.title}\nContent: {art.content}")
                                ordered_article_ids.append(str(art.id))

                        if not articles_content:
                            logger.warn("Articles content empty for event, skipping", event_id=str(event.id))
                            continue

                        # Call LLM Service to analyze cluster
                        intelligence = await self.analyze_event_cluster(articles_content)

                        try:
                            from src.services.contradiction_service import ContradictionService
                            contradictions = await ContradictionService(session).detect_for_event(event.id)
                        except Exception as contra_err:
                            logger.error(
                                "Contradiction detection failed",
                                event_id=str(event.id),
                                error=str(contra_err),
                            )
                            # None, not []. The repository only overwrites
                            # stored contradictions when the value is not None,
                            # so an empty list here would silently erase
                            # previously detected, valid contradictions.
                            contradictions = None

                        # Update event intelligence in repository
                        intel_data = {
                            "summary": intelligence.summary,
                            "topic": intelligence.topic.value,
                            "bias_lean": intelligence.bias_lean.value,
                            "location_country": intelligence.location_country,
                            "latitude": intelligence.latitude,
                            "longitude": intelligence.longitude,
                            "importance_score": intelligence.importance_score
                        }

                        await event_repo.update_event_intelligence(
                            event.id,
                            intel_data,
                            citation_sources=ordered_article_ids,
                            contradictions=contradictions,
                            claims=intelligence.claims,
                        )
                        logger.info(
                            "Event intelligence successfully updated",
                            event_id=str(event.id),
                            cited_articles=len(intelligence.citations),
                            claims=len(intelligence.claims),
                            contradictions=(len(contradictions) if contradictions is not None else "unchanged"),
                        )
                        
                        # Trigger real-time search indexing
                        try:
                            from src.services.search_service import SearchService
                            search_service = SearchService()
                            event_data = {
                                "title": event.title,
                                "summary": intel_data["summary"],
                                "topic": intel_data["topic"],
                                "location_country": intel_data["location_country"],
                                "latitude": intel_data["latitude"],
                                "longitude": intel_data["longitude"],
                                "importance_score": intel_data["importance_score"]
                            }
                            await search_service.index_processed_event(event.id, event_data)
                        except Exception as search_err:
                            logger.error(
                                "Failed to index event in Elasticsearch during real-time sync",
                                event_id=str(event.id),
                                error=str(search_err)
                            )
                        
                        # Real-time feeds: announce the freshly PROCESSED event
                        # to connected SSE clients. Fire-and-forget — a bus
                        # failure is logged and must never abort the batch.
                        try:
                            from src.services.realtime_service import publish_processed_event
                            await publish_processed_event(str(event.id))
                        except Exception as rt_err:
                            logger.error(
                                "Live event broadcast failed",
                                event_id=str(event.id),
                                error=str(rt_err),
                            )

                        # Follower alerting: everyone who opted into this event
                        # gets a push for every device they registered. The
                        # enqueue is idempotent per device and failure only
                        # costs an alert, so it is fire-and-forget too.
                        if settings.PUSH_EVENT_FOLLOW_ENABLED:
                            try:
                                from src.services.push_service import PushService
                                created = await PushService(session).enqueue_follow_event(event)
                                if created:
                                    logger.info(
                                        "Followed-event push queued",
                                        event_id=str(event.id),
                                        deliveries=created,
                                    )
                            except Exception as push_err:
                                logger.error(
                                    "Followed-event push enqueue failed",
                                    event_id=str(event.id),
                                    error=str(push_err),
                                )

                        # Outbound webhooks: fan this PROCESSED event out to every
                        # registered endpoint that asked for it. Idempotent per
                        # (webhook, event); failure only costs one batch slot.
                        try:
                            from src.services.webhook_service import WebhookService
                            webhook_deliveries = await WebhookService(session).queue_for_event(event)
                            if webhook_deliveries:
                                logger.info(
                                    "Webhook deliveries queued",
                                    event_id=str(event.id),
                                    deliveries=webhook_deliveries,
                                )
                        except Exception as webhook_err:
                            logger.error(
                                "Webhook enqueue failed",
                                event_id=str(event.id),
                                error=str(webhook_err),
                            )

                        processed_count += 1
                        await asyncio.sleep(5.0)
                    except Exception as event_err:
                        logger.error(
                            "Failed to process event intelligence for event",
                            event_id=str(event.id),
                            error=str(event_err)
                        )
                        error_count += 1
                        await asyncio.sleep(5.0)
                        continue
            except Exception as batch_err:
                logger.error("Pending events process batch encountered a fatal error", error=str(batch_err))
                await session.rollback()

        logger.info(
            "Finished processing pending events",
            processed=processed_count,
            failed=error_count
        )
        return processed_count, error_count

    async def answer_chatbot_question(self, query: str) -> Tuple[str, str, bool]:
        """
        Answers a user query.
        1. Searches Elasticsearch index for events.
        2. If events are found, calls the LLM with the DB context.
        3. If the LLM returns 'NOT_FOUND_IN_DB' or no DB events exist, queries Tavily for web search results and answers grounded in web context.
        Returns: Tuple[reply_text, source_str, in_database_bool]
        """
        from src.core.elasticsearch import es_client
        from src.core.config import settings
        from src.services.tavily_service import TavilyService

        logger.info("Chatbot query received", query=query)

        # 1. Query Elasticsearch index
        es_results = []
        max_score = 0.0
        try:
            es_res = await es_client.search(
                index=settings.ELASTICSEARCH_INDEX_EVENTS,
                body={
                    "query": {
                        "multi_match": {
                            "query": query,
                            "fields": ["title^3", "summary^2"],
                            "fuzziness": "AUTO",
                        }
                    },
                    "size": 5,
                },
            )
            es_results = es_res["hits"]["hits"]
            max_score = es_res["hits"].get("max_score", 0.0) or 0.0
            logger.info("Elasticsearch search completed in chatbot", hits_count=len(es_results), max_score=max_score)
        except Exception as es_err:
            logger.error("Elasticsearch query failed in chatbot", error=str(es_err))

        # 2. If matching documents exist with score >= 2.0, try DB grounding
        if es_results and max_score >= 2.0:
            db_context = ""
            for idx, hit in enumerate(es_results, 1):
                source = hit["_source"]
                db_context += f"Event [{idx}]:\n"
                db_context += f"  Title: {source.get('title')}\n"
                db_context += f"  Summary: {source.get('summary')}\n"
                db_context += f"  Country: {source.get('location_country')}\n\n"

            system_prompt = (
                "You are AI News-Scout, the official news analyst assistant for GlobeLens AI.\n"
                "Your job is to answer the user's question using ONLY the provided database events context.\n"
                "Do not assume, extrapolate, or use external knowledge. Be objective, direct, and conversational.\n"
                "If the provided database context does not contain enough relevant information to answer the question, "
                "or if the question is about a topic not present in the context, you MUST reply with EXACTLY the phrase: NOT_FOUND_IN_DB\n"
                "Do not write anything else if the information is not found."
            )
            user_prompt = f"Database Events Context:\n{db_context}\n\nUser Question: {query}"

            logger.info("Attempting DB-grounded chatbot answer", model=self._model)
            try:
                reply = (
                    await self._provider.complete(
                        system_prompt,
                        user_prompt,
                        json_mode=False,
                        temperature=0.1,
                    )
                ).strip()

                if "NOT_FOUND_IN_DB" not in reply:
                    logger.info("Successfully generated DB-grounded response")
                    return reply, "database", True
                else:
                    logger.info("LLM reported information not present in DB context. Falling back to Tavily.")

            except Exception as llm_err:
                logger.error("DB-grounded chatbot LLM call failed", error=str(llm_err))

        # 3. Fallback to Tavily Search Grounding
        logger.info("Executing Tavily live search for chatbot fallback", query=query)
        try:
            tavily = TavilyService()
            search_results = await tavily.search_web(query)
            
            if search_results:
                web_context = ""
                for idx, result in enumerate(search_results, 1):
                    web_context += f"Reference [{idx}]:\n"
                    web_context += f"  Title: {result.get('title')}\n"
                    web_context += f"  URL: {result.get('url')}\n"
                    web_context += f"  Snippet: {result.get('content')}\n\n"

                system_prompt = (
                    "You are AI News-Scout, the official news analyst assistant for GlobeLens AI.\n"
                    "The user's query could not be answered using the GlobeLens database events. You have searched the web to gather live information.\n"
                    "Write a clear, concise, and helpful response to the user's question based on the provided live web search context. "
                    "Make sure to ground all details in the search context and keep the tone professional and helpful."
                )
                user_prompt = f"Live Web Context:\n{web_context}\n\nUser Question: {query}"

                logger.info("Attempting web-grounded chatbot answer", model=self._model)
                reply = (
                    await self._provider.complete(
                        system_prompt,
                        user_prompt,
                        json_mode=False,
                        temperature=0.3,
                    )
                ).strip()

                prefix = "I could not find matching events in the GlobeLens database. However, I searched the web for live updates:\n\n"
                return prefix + reply, "web", False

        except Exception as search_err:
            logger.error("Web-grounded chatbot logic failed", error=str(search_err))

        return (
            "I could not find any relevant events in the GlobeLens database or on the live web matching your query.",
            "database",
            False
        )

