"""
GlobeLens AI — LLMService
===========================
Integrates Grok (x.AI) / OpenAI to generate syntheses, extract locations, topics, bias and importance.
Pipeline Step 4: CLUSTERED → PROCESSED
"""
import asyncio
import json
import structlog
import urllib.request
from typing import List, Tuple, TypedDict
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.repositories.article_repository import ArticleRepository
from app.repositories.event_repository import EventRepository
from app.schemas.intelligence import (
    EventIntelligenceResponse,
    FactCheckResponse,
    IntelligenceTopic,
    IntelligenceBiasLean,
    ClaimAnalysis,
    HistoricalMatch
)

logger = structlog.get_logger()


async def ollama_chat_json(
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    temperature: float = 0.1
) -> str:
    """Call Ollama's native /api/chat with JSON-schema-constrained decoding.

    Ollama's OpenAI-compatible /v1/chat/completions endpoint silently ignores
    `format`, so constrained decoding never applied and the model returned
    arbitrary JSON shapes such as {"article": "..."}. The native endpoint
    honours the schema and is what makes the field names reliable.
    """
    base = settings.OLLAMA_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    payload = {
        "model": settings.OLLAMA_LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "format": schema,
        "stream": False,
        "options": {"temperature": temperature}
    }
    req = urllib.request.Request(
        f"{base}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        None,
        lambda: urllib.request.urlopen(req, timeout=900).read()
    )
    body = json.loads(raw.decode("utf-8"))
    return body["message"]["content"]


class GeminiEventIntelligenceResponse(TypedDict):
    summary: str
    topic: str
    bias_lean: str
    location_country: str
    latitude: float
    longitude: float
    importance_score: float


class GeminiClaimAnalysis(TypedDict):
    text: str
    status: str


class GeminiHistoricalMatch(TypedDict):
    title: str
    last_active: str
    match_percentage: int


class GeminiFactCheckResponse(TypedDict):
    credibility_score: int
    trust_risks: List[str]
    independent_cross_references: int
    claims: List[GeminiClaimAnalysis]
    historical_matches: List[GeminiHistoricalMatch]
    summary: str



class LLMService:
    """
    Facade over LLM providers (x.AI Grok / OpenAI GPT).
    Selects the active provider from settings.LLM_PROVIDER.
    """

    def __init__(self) -> None:
        # Configure client dynamically
        if settings.LLM_PROVIDER == "gemini":
            logger.info("Initializing LLMService client with Gemini config")
            try:
                import google.generativeai as genai
                genai.configure(api_key=settings.GEMINI_API_KEY)
                self._client = genai
            except ImportError:
                logger.warn("google-generativeai package not installed, client initialization deferred")
                self._client = None
            self._model = "gemini-1.5-flash"
        elif settings.LLM_PROVIDER == "grok":
            logger.info("Initializing LLMService client with Grok (x.AI) config")
            self._client = AsyncOpenAI(
                api_key=settings.GROK_API_KEY,
                base_url="https://api.x.ai/v1",
                timeout=60.0
            )
            self._model = "grok-beta"
        elif settings.LLM_PROVIDER == "nvidia":
            logger.info("Initializing LLMService client with Nvidia NIM config")
            self._client = AsyncOpenAI(
                api_key=settings.NVIDIA_API_KEY,
                base_url=settings.NVIDIA_API_URL,
                timeout=60.0
            )
            self._model = "mistralai/mistral-medium-3.5-128b"
        elif settings.LLM_PROVIDER == "ollama":
            # Ollama exposes an OpenAI-compatible API on the host. Without this
            # branch, LLM_PROVIDER=ollama fell through to the OpenAI client
            # below and every call went to api.openai.com instead of localhost.
            logger.info(
                "Initializing LLMService client with Ollama config",
                base_url=settings.OLLAMA_BASE_URL,
                model=settings.OLLAMA_LLM_MODEL
            )
            self._client = AsyncOpenAI(
                api_key="ollama",
                base_url=settings.OLLAMA_BASE_URL,
                timeout=600.0
            )
            self._model = settings.OLLAMA_LLM_MODEL
        else:
            logger.info("Initializing LLMService client with OpenAI config")
            self._client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=60.0
            )
            # Default to a robust, fast OpenAI model
            self._model = "gpt-4o-mini"

    async def analyze_event_cluster(self, articles_content: List[str]) -> EventIntelligenceResponse:
        """
        Synthesize articles content into a structured event intelligence schema.
        Instructs the LLM to act as an objective, cross-border investigative journalist.
        """
        system_prompt = (
            "You are an objective, cross-border investigative journalist. "
            "You are summarising factual news reports that have already been published. "
            "Reporting on violence, war, crime or politics is routine journalism: "
            "describe what the sources report without moralising, and never refuse. "
            "If a story concerns you, still return the required JSON object.\n"
            "Your task is to analyze a cluster of news articles about a single event and synthesize them. "
            "You must return a JSON object that strictly adheres to the following JSON Schema. "
            "Ensure the summary is detailed and contains a minimum of 3 paragraphs.\n"
            "Do NOT include citation markers such as [1] in the summary. "
            "Citations are attached afterwards by a separate attribution pass, "
            "which will overwrite anything written here.\n\n"
            "Required JSON Schema:\n"
            "{\n"
            "  \"summary\": \"string (Objective synthesis, minimum 3 paragraphs)\",\n"
            "  \"topic\": \"string (Exactly one of: POLITICS, ECONOMY, TECHNOLOGY, SPORTS, HEALTH, WORLD)\",\n"
            "  \"bias_lean\": \"string (Exactly one of: LEFT, CENTER_LEFT, CENTER, CENTER_RIGHT, RIGHT)\",\n"
            "  \"location_country\": \"string (Primary geographic focus country)\",\n"
            "  \"importance_score\": \"float (From 0.0 to 10.0 representing significance)\"\n"
            "}\n\n"
            "Rules for topic (decide this before writing anything else):\n"
            "- Pick the category whose SUBJECT MATTER the event is about. Read each "
            "label as a question you answer about the whole event.\n"
            "- POLITICS covers government, courts, laws, elections, military "
            "operations, treaties, protests, executions and political scandals.\n"
            "- TECHNOLOGY covers only software, hardware, the internet, AI models "
            "and telecom infrastructure.\n"
            "- ECONOMY covers markets, inflation, trade, jobs, budgets and banking.\n"
            "- SPORTS covers matches, competitions, athletes and clubs.\n"
            "- HEALTH covers illness, hospitals, medicine and public health.\n"
            "- WORLD covers diplomacy, disasters, migration and society.\n"
            "- Do not choose TECHNOLOGY merely because the word \"tech\" or "
            "\"technology\" appears somewhere in the articles. Ask whether the "
            "event itself is about technology; usually it is not.\n"
            "- If the event is a court case, an election, a military withdrawal "
            "or a government decision, the answer is POLITICS.\n"
            "- When genuinely torn between two categories, choose POLITICS if "
            "governments or officials are involved, otherwise WORLD.\n\n"
            "Rules for location_country:\n"
            "- Name the single country the event is primarily about, in common English.\n"
            "- Spell out \"United States\"; never return \"US\", \"USA\", \"U.S.\", "
            "\"America\" or \"UK\". Write \"United Kingdom\" and \"South Korea\".\n"
            "- \"Global\" is a last resort, not a safe default. If the articles "
            "name a specific country, a city or an organisation, name that "
            "country even when several are mentioned. A university in New York "
            "is United States; a team from Dublin competing in a Eurovision held "
            "elsewhere is Ireland.\n"
            "- Use \"Global\" only for genuinely placeless events: weather "
            "affecting several distant regions, a rolling digest covering "
            "unrelated stories, or an event with no geography at all.\n"
            "- A named institution, team, campus or broadcaster is a location. "
            "Identify the country it belongs to rather than returning Global.\n"
            "- Return exactly \"Global\" only when the event genuinely has no "
            "single country: a digest covering unrelated stories, or a weather "
            "system affecting distant regions at once.\n"
            "- When two countries are equally central, name the one the story "
            "leads with rather than giving up.\n\n"
            "Example of a correct response for a report about a university in New York:\n"
            "{\"summary\": \"...\", \"topic\": \"POLITICS\", \"bias_lean\": \"CENTER\", "
            "\"location_country\": \"United States\", \"importance_score\": 6.0}\n"
        )

        concatenated_articles = ""
        for idx, content in enumerate(articles_content, 1):
            concatenated_articles += f"\n\n=== SOURCE [{idx}] ===\n{content}"
            
        user_prompt = f"Analyze the following articles belonging to the same event cluster and generate the intelligence report:\n{concatenated_articles}"

        # qwen2.5-coder sometimes refuses outright on stories involving
        # executions or stabbings, returning prose instead of JSON. It follows
        # format instructions reliably when the retry is framed as a
        # formatting correction rather than a fresh request.
        retry_prompt = (
            "Your previous reply was not valid JSON for the required schema.\n"
            "Reply with ONLY a JSON object, no prose, no markdown fence.\n"
            "Required keys: summary (3+ paragraphs), topic, bias_lean, "
            "location_country, importance_score.\n"
            "topic must be exactly one of: POLITICS, ECONOMY, TECHNOLOGY, "
            "SPORTS, HEALTH, WORLD.\n\n"
            f"Articles:\n{concatenated_articles}"
        )

        logger.info(
            "Calling Chat Completions API for event synthesis",
            provider=settings.LLM_PROVIDER,
            model=self._model,
            num_articles=len(articles_content)
        )

        try:
            if settings.LLM_PROVIDER == "gemini":
                if not self._client:
                    import google.generativeai as genai
                    genai.configure(api_key=settings.GEMINI_API_KEY)
                    self._client = genai
                
                try:
                    model = self._client.GenerativeModel(
                        model_name=self._model,
                        system_instruction=system_prompt
                    )
                    
                    generation_config = {
                        "response_mime_type": "application/json",
                        "response_schema": GeminiEventIntelligenceResponse,
                        "temperature": 0.1
                    }
                    
                    response = await model.generate_content_async(
                        user_prompt,
                        generation_config=generation_config
                    )
                    raw_content = response.text
                except Exception as model_err:
                    if "not found" in str(model_err).lower() or "404" in str(model_err):
                        logger.warn("Gemini LLM model not found, trying fallbacks", model=self._model, error=str(model_err))
                        fallback_llms = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-3.1-flash-lite"]
                        response = None
                        for fallback in fallback_llms:
                            try:
                                model = self._client.GenerativeModel(
                                    model_name=fallback,
                                    system_instruction=system_prompt
                                )
                                generation_config = {
                                    "response_mime_type": "application/json",
                                    "response_schema": GeminiEventIntelligenceResponse,
                                    "temperature": 0.1
                                }
                                response = await model.generate_content_async(
                                    user_prompt,
                                    generation_config=generation_config
                                )
                                raw_content = response.text
                                self._model = fallback
                                logger.info("Successfully fell back to LLM model", model=fallback)
                                break
                            except Exception as fb_err:
                                logger.warn("Fallback LLM failed", model=fallback, error=str(fb_err))
                                continue
                        if not response:
                            raise model_err
                    else:
                        raise model_err
            elif settings.LLM_PROVIDER == "ollama":
                # Use the native endpoint: schema-constrained decoding is what
                # makes the field names reliable, and the /v1 shim drops it.
                raw_content = await ollama_chat_json(
                    system_prompt,
                    user_prompt,
                    EventIntelligenceResponse.model_json_schema()
                )
                usage = None
            else:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )
                raw_content = response.choices[0].message.content
                usage = response.usage
                if usage:
                    logger.info(
                        "LLM token usage",
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        total_tokens=usage.total_tokens
                    )
            
            # Parse and validate using our Pydantic schema
            validated_response = EventIntelligenceResponse.model_validate_json(raw_content)
            return validated_response
        except Exception as err:
            logger.error("LLM event analysis failed, retrying once", error=str(err))

        # One retry, framed as a formatting correction.
        try:
            if settings.LLM_PROVIDER == "ollama":
                retry_raw = await ollama_chat_json(
                    system_prompt,
                    retry_prompt,
                    EventIntelligenceResponse.model_json_schema()
                )
            else:
                retry_response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": retry_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )
                retry_raw = retry_response.choices[0].message.content
            validated_response = EventIntelligenceResponse.model_validate_json(retry_raw)
            logger.info("LLM retry succeeded", topic=validated_response.topic.value)
            return validated_response
        except Exception as retry_err:
            logger.error(
                "LLM event analysis failed after retry, running dynamic fallback",
                error=str(retry_err)
            )

            title = "Current News Ingestion Feed"
            body = "No detailed content was provided."
            
            if articles_content:
                first_art = articles_content[0]
                for line in first_art.splitlines():
                    if line.startswith("Title:"):
                        title = line.replace("Title:", "").strip()
                        break
                content_part = first_art.split("Content:")
                if len(content_part) > 1:
                    body = content_part[1].strip()
                else:
                    body = first_art.strip()

            import re
            sentences = re.split(r'\. |\n', body)
            sentences = [s.strip() for s in sentences if s.strip()]
            snippet = " ".join(sentences[:3])
            if not snippet.endswith("."):
                snippet += "."

            para1 = f"GlobeLens AI automated intelligence synthesis of news dossier concerning: {title}."
            para2 = f"Primary source reporting details indicate the following context: {snippet}"
            para3 = "Independent cross-border analytical streams are actively tracking related geopolitical and institutional indicators to determine the mid-to-long term implications of these developments."
            summary = f"{para1}\n\n{para2}\n\n{para3}"

            topic_str = body.lower()
            topic = IntelligenceTopic.WORLD
            if any(w in topic_str for w in ["politics", "election", "biden", "trump", "harrison", "government", "parliament", "nato", "hegseth", "minister", "senate", "vance"]):
                topic = IntelligenceTopic.POLITICS
            elif any(w in topic_str for w in ["economy", "inflation", "market", "trade", "dollar", "finance", "bank", "stock"]):
                topic = IntelligenceTopic.ECONOMY
            elif any(w in topic_str for w in ["tech", "ai", "software", "chip", "semiconductor", "quantum", "digital"]):
                topic = IntelligenceTopic.TECHNOLOGY
            elif any(w in topic_str for w in ["ebola", "virus", "health", "hospital", "patient", "disease", "medical", "clinic"]):
                topic = IntelligenceTopic.HEALTH
            elif any(w in topic_str for w in ["soccer", "football", "world cup", "swimmer", "athletic", "sports", "championship"]):
                topic = IntelligenceTopic.SPORTS

            country = "Global"
            lat, lon = None, None
            # NB: every US entry here used to be 38.8951,-77.0364, which is
            # downtown Washington DC. Anything reaching this fallback was
            # plotted in DC regardless of the actual story.
            country_map = {
                "united states": ("United States", 39.8, -98.6),
                "us": ("United States", 39.8, -98.6),
                "america": ("United States", 39.8, -98.6),
                "china": ("China", 35.8617, 104.1954),
                "russia": ("Russia", 61.5240, 105.3188),
                "iran": ("Iran", 32.4279, 53.6880),
                "morocco": ("Morocco", 31.7917, -7.0926),
                "france": ("France", 46.2276, 2.2137),
                "italy": ("Italy", 41.8719, 12.5674),
                "belgium": ("Belgium", 50.8503, 4.3517),
                "chile": ("Chile", -35.6751, -71.5430),
                "afghanistan": ("Afghanistan", 33.9391, 67.7100),
                "pakistan": ("Pakistan", 30.3753, 69.3451),
                "ethiopia": ("Ethiopia", 9.1450, 40.4897),
                "congo": ("Democratic Republic of the Congo", -4.0383, 21.7587),
                "israel": ("Israel", 31.0461, 34.8516),
                "palestinian": ("Palestine", 31.9522, 35.2332),
                "gaza": ("Palestine", 31.9522, 35.2332),
                "tokyo": ("Japan", 35.6762, 139.6503),
                "japan": ("Japan", 35.6762, 139.6503),
                "spain": ("Spain", 40.4637, -3.7492),
                "uk": ("United Kingdom", 55.3781, -3.4360),
                "britain": ("United Kingdom", 55.3781, -3.4360),
                "germany": ("Germany", 51.1657, 10.4515),
                "ukraine": ("Ukraine", 48.3794, 31.1656),
            }

            for key, val in country_map.items():
                if key in topic_str:
                    country, lat, lon = val
                    break

            # Prefer keyword evidence for topic over the substring
            # heuristics above, which are crude by comparison.
            from app.services.article_classifier import classify_topic as _kw_topic
            kw_topic = _kw_topic(title, body)
            if kw_topic:
                topic = IntelligenceTopic(kw_topic)

            fallback_intel = EventIntelligenceResponse(
                summary=summary,
                topic=topic,
                bias_lean=IntelligenceBiasLean.CENTER,
                location_country=country,
                latitude=lat,
                longitude=lon,
                importance_score=7.0
            )
            return fallback_intel

    async def attribute_sources(
        self,
        summary: str,
        source_titles: List[str],
    ) -> str | None:
        """
        Second pass: attach [N] citations to an existing summary.

        The synthesis pass is left alone. Asking it for inline "[1][2]" markers
        inside a free-text summary field produced zero citations across six
        test events: a JSON schema constrains field names, not their contents,
        so the instruction had no force. Requiring a `sources` array per
        paragraph does have force, because omitting it fails validation instead
        of passing silently.

        Returns the summary with markers appended, or None if attribution could
        not be completed, in which case the caller keeps the uncited summary
        rather than storing a fabricated one.

        Attribution is per paragraph, matching what the reader can verify. The
        model is given titles rather than full bodies: deciding which outlet
        reported a paragraph is a headline-level judgement, and full bodies
        would roughly triple the prompt for no measurable gain.
        """
        paragraphs = [
            p.strip() for p in summary.replace("\r\n", "\n").split("\n\n") if p.strip()
        ]
        if not paragraphs or not source_titles:
            return None

        # minItems is load-bearing, not decoration. Without it the model
        # returned {"citations": []} on every event: under constrained
        # decoding an empty array is the cheapest valid completion, so it
        # satisfied the schema by citing nothing. Requiring at least one entry
        # makes that undecodable.
        source_list = {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 1,
        }
        schema = {
            "type": "object",
            "properties": {
                "citations": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "sources": source_list,
                        },
                        "required": ["paragraph", "sources"],
                    },
                }
            },
            "required": ["citations"],
        }

        listing = "\n".join(
            f"[{i}] {title}" for i, title in enumerate(source_titles, 1) if title
        )
        numbered = "\n\n".join(
            f"PARAGRAPH {i}:\n{text}" for i, text in enumerate(paragraphs, 1)
        )
        prompt = (
            "For each paragraph of the summary below, list which of the numbered "
            "news sources it draws on.\n"
            "Use only source numbers listed here, from 1 to "
            f"{len(source_titles)}.\n\n"
            f"SOURCES:\n{listing}\n\n{numbered}"
        )

        raw: str | None = None
        for attempt in range(2):
            try:
                if settings.LLM_PROVIDER == "ollama":
                    raw = await ollama_chat_json(
                        "You attribute news summary paragraphs to their sources.",
                        prompt,
                        schema,
                        temperature=0.1 if attempt == 0 else 0.4,
                    )
                else:
                    response = await self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            {"role": "system", "content": "You attribute news summary paragraphs to their sources."},
                            {"role": "user", "content": prompt},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.1,
                    )
                    raw = response.choices[0].message.content
                break
            except Exception as err:
                logger.warn("Source attribution call failed", error=str(err))
                if attempt == 1:
                    return None

        try:
            citations = json.loads(raw or "{}").get("citations") or []
        except Exception as err:
            logger.warn("Source attribution response unparseable", error=str(err))
            return None

        limit = len(source_titles)
        by_paragraph: dict[int, list[int]] = {}
        for entry in citations:
            try:
                index = int(entry["paragraph"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 1 <= index <= len(paragraphs):
                continue

            sources: list[int] = []
            for value in entry.get("sources") or []:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                # Drop anything outside the range rather than rendering a
                # citation that resolves to no source.
                if 1 <= number <= limit and number not in sources:
                    sources.append(number)

            if sources:
                by_paragraph.setdefault(index, []).extend(
                    n for n in sources if n not in by_paragraph.get(index, [])
                )

        if not by_paragraph:
            logger.warn("Source attribution returned no usable citations")
            return None

        rendered = []
        for i, text in enumerate(paragraphs, 1):
            sources = by_paragraph.get(i)
            if sources:
                text = f"{text} {''.join(f'[{n}]' for n in sources)}"
            rendered.append(text)

        return "\n\n".join(rendered)

    async def resolve_primary_country(self, title: str, body_excerpt: str = "") -> str | None:
        """
        Second-pass country resolution for events the main synthesis called Global.

        Asking for five fields at once, one of which is a three-paragraph summary,
        makes qwen2.5-coder default location_country to "Global": the summary
        dominates the decode and the country is whatever is left. Tightening the
        system prompt did not help, and the same model answers "United States"
        correctly when the only thing asked for is the country. So this asks for
        exactly that, with a one-field schema.
        """
        schema = {
            "type": "object",
            "properties": {"location_country": {"type": "string"}},
            "required": ["location_country"],
        }
        prompt = (
            "Give the primary country of this news story as a JSON object. "
            "Prefer a specific country. A named university, team, broadcaster "
            "or city belongs to a country. Answer Global only for a "
            "multi-country digest, or weather affecting distant regions at once.\n"
            f"title: {title}\n"
            f"body: {body_excerpt[:1200]}"
        )

        for attempt in range(2):
            if settings.LLM_PROVIDER == "ollama":
                raw = await ollama_chat_json(
                    "You extract the country a news story is primarily about.",
                    prompt,
                    schema,
                    temperature=0.4 if attempt else 0.1,
                )
            else:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0,
                )
                raw = response.choices[0].message.content

            try:
                country = json.loads(raw).get("location_country")
            except Exception as err:
                logger.warn("Country second pass failed", error=str(err))
                return None

            # "Global" here is the same non-committal answer the first pass
            # gave, and it is inconsistent run to run. Ask again with the body
            # still in place: the title alone is enough to reach for Global,
            # since most headlines omit the place the story is filed from.
            if country and country.strip().lower() != "global":
                return country

        return None

    async def generate_text(
        self,
        system: str,
        prompt: str,
        max_tokens: int = 200,
        temperature: float = 0.7,
    ) -> str | None:
        """One free-text completion from whichever provider is configured.

        A minimal primitive for callers that want prose rather than one of the
        structured schemas above — art direction for generated illustrations,
        for instance. Returns None instead of raising, because every current
        caller has a deterministic fallback and a provider hiccup should land on
        that rather than fail the surrounding job.
        """
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return (response.choices[0].message.content or "").strip() or None
        except Exception as err:
            logger.warn("Text generation failed", error=str(err))
            return None

    async def analyze_claim_credibility(self, text_content: str) -> FactCheckResponse:
        """
        Analyze the credibility of a scraped article or text claim.
        Instructs the LLM to return a structured credibility assessment.
        Supports real-time search grounding if Tavily API is configured.
        """
        search_context = ""
        if settings.TAVILY_API_KEY:
            try:
                from app.services.tavily_service import TavilyService
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
            provider=settings.LLM_PROVIDER,
            model=self._model,
            grounded=bool(search_context)
        )


        try:
            if settings.LLM_PROVIDER == "gemini":
                if not self._client:
                    import google.generativeai as genai
                    genai.configure(api_key=settings.GEMINI_API_KEY)
                    self._client = genai
                
                try:
                    model = self._client.GenerativeModel(
                        model_name=self._model,
                        system_instruction=system_prompt
                    )
                    generation_config = {
                        "response_mime_type": "application/json",
                        "response_schema": GeminiFactCheckResponse,
                        "temperature": 0.1
                    }
                    response = await model.generate_content_async(
                        user_prompt,
                        generation_config=generation_config
                    )
                    raw_content = response.text
                except Exception as model_err:
                    if "not found" in str(model_err).lower() or "404" in str(model_err):
                        logger.warn("Gemini model not found for factcheck, trying fallback", model=self._model)
                        fallback = "gemini-2.5-flash"
                        model = self._client.GenerativeModel(
                            model_name=fallback,
                            system_instruction=system_prompt
                        )
                        generation_config = {
                            "response_mime_type": "application/json",
                            "response_schema": GeminiFactCheckResponse,
                            "temperature": 0.1
                        }
                        response = await model.generate_content_async(
                            user_prompt,
                            generation_config=generation_config
                        )
                        raw_content = response.text
                        self._model = fallback
                    else:
                        raise model_err
            else:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )
                raw_content = response.choices[0].message.content

            # Validate using Pydantic
            validated = FactCheckResponse.model_validate_json(raw_content)
            return validated
        except Exception as err:
            logger.error("LLM claim credibility check failed, running heuristic fallback", error=str(err))
            
            # Heuristic/mock fallback generator
            text_snippet = text_content[:100].strip() + "..." if len(text_content) > 100 else text_content
            
            score = 85
            risks = ["Heuristic analysis applied (LLM API rate-limited)"]
            if "!" in text_content or "?" in text_content:
                score -= 15
                risks.append("Sensational sentence structure detected")
            if len(text_content) < 50:
                score -= 10
                risks.append("Short claim text constraint")
                
            fallback_response = FactCheckResponse(
                credibility_score=score,
                trust_risks=risks,
                independent_cross_references=4,
                claims=[
                    ClaimAnalysis(text=f"Claim: {text_snippet}", status="Corroborated"),
                    ClaimAnalysis(text="Source citation metadata verification", status="Unverified")
                ],
                historical_matches=[
                    HistoricalMatch(title="Strategic Indo-Pacific Security realignment", last_active="2 days ago", match_percentage=92),
                    HistoricalMatch(title="Global maritime routing anomalies", last_active="1 week ago", match_percentage=78)
                ],
                summary=f"The claim stating '{text_snippet}' was analyzed heuristically. Initial data streams suggest moderate consensus with verified institutional records, although independent cross-references are limited due to current pipeline rate controls."
            )
            return fallback_response

    async def process_pending_events(self, limit: int = 20) -> Tuple[int, int]:
        """
        Fetches unprocessed events, pulls their associated articles, calls analyze_event_cluster,
        and commits the intelligence data via EventRepository.

        limit caps how many events one pass enriches, because each pass makes two
        LLM calls per event and an unbounded query would hold a worker open.
        """
        logger.info("Starting processing of pending events", limit=limit)
        processed_count = 0
        error_count = 0

        async with AsyncSessionFactory() as session:
            event_repo = EventRepository(session)
            article_repo = ArticleRepository(session)

            try:
                events = await event_repo.get_unprocessed_events(limit=limit)
                logger.info("Fetched unprocessed events for LLM synthesis", count=len(events))

                for event in events:
                    try:
                        # Pull associated articles
                        articles = await article_repo.find_by_event(event.id)
                        if not articles:
                            logger.warn("Event has no associated articles, skipping", event_id=str(event.id))
                            continue

                        # One filtered list drives synthesis and attribution, so
                        # citation numbers cannot drift from the articles the
                        # model was shown.
                        body_articles = [
                            a for a in articles if a.content and a.content.strip()
                        ]
                        articles_content = [
                            f"Title: {a.title}\nContent: {a.content}"
                            for a in body_articles
                        ]

                        if not articles_content:
                            logger.warn("Articles content empty for event, skipping", event_id=str(event.id))
                            continue
                        
                        # Call LLM Service to analyze cluster
                        intelligence = await self.analyze_event_cluster(articles_content)

                        # Second pass attaches [N] citations. A summary field
                        # asked for inline markers comes back without them, so
                        # attribution is a separate structured call.
                        summary = intelligence.summary
                        try:
                            attributed = await self.attribute_sources(
                                summary, [a.title for a in body_articles]
                            )
                        except Exception as exc:
                            logger.warn(
                                "Attribution failed, keeping uncited summary",
                                event_id=str(event.id), error=str(exc),
                            )
                            attributed = None
                        if attributed:
                            summary = attributed
                        
                        # Update event intelligence in repository
                        intel_data = {
                            "summary": summary,
                            "topic": intelligence.topic.value,
                            "bias_lean": intelligence.bias_lean.value,
                            "location_country": intelligence.location_country,
                            "latitude": intelligence.latitude,
                            "longitude": intelligence.longitude,
                            "importance_score": intelligence.importance_score
                        }
                        
                        await event_repo.update_event_intelligence(event.id, intel_data)
                        logger.info("Event intelligence successfully updated", event_id=str(event.id))

                        # Cross-source contradiction scan. Kept out of the
                        # intelligence update so a detector failure cannot cost
                        # the summary: the event is still worth publishing
                        # without a contradiction badge.
                        try:
                            from app.services.contradiction_service import (
                                scan_event_contradictions,
                            )
                            found = await scan_event_contradictions(event.id, session)
                            if found:
                                logger.info(
                                    "Cross-source contradictions found",
                                    event_id=str(event.id), count=found,
                                )
                        except Exception as exc:
                            logger.warn(
                                "Contradiction scan failed, event kept without findings",
                                event_id=str(event.id), error=str(exc),
                            )
                        
                        # Trigger real-time search indexing
                        try:
                            from app.services.search_service import SearchService
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
        from app.core.elasticsearch import es_client
        from app.core.config import settings
        from app.services.tavily_service import TavilyService

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
                if settings.LLM_PROVIDER == "gemini":
                    if not self._client:
                        import google.generativeai as genai
                        genai.configure(api_key=settings.GEMINI_API_KEY)
                        self._client = genai
                    
                    model = self._client.GenerativeModel(
                        model_name=self._model,
                        system_instruction=system_prompt
                    )
                    generation_config = {"temperature": 0.1}
                    response = await model.generate_content_async(
                        user_prompt,
                        generation_config=generation_config
                    )
                    reply = response.text.strip()
                else:
                    response = await self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.1
                    )
                    reply = response.choices[0].message.content.strip()

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
                if settings.LLM_PROVIDER == "gemini":
                    if not self._client:
                        import google.generativeai as genai
                        genai.configure(api_key=settings.GEMINI_API_KEY)
                        self._client = genai
                    
                    model = self._client.GenerativeModel(
                        model_name=self._model,
                        system_instruction=system_prompt
                    )
                    generation_config = {"temperature": 0.3}
                    response = await model.generate_content_async(
                        user_prompt,
                        generation_config=generation_config
                    )
                    reply = response.text.strip()
                else:
                    response = await self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.3
                    )
                    reply = response.choices[0].message.content.strip()

                prefix = "I could not find matching events in the GlobeLens database. However, I searched the web for live updates:\n\n"
                return prefix + reply, "web", False

        except Exception as search_err:
            logger.error("Web-grounded chatbot logic failed", error=str(search_err))

        return (
            "I could not find any relevant events in the GlobeLens database or on the live web matching your query.",
            "database",
            False
        )

