"""
GlobeLens AI — Config-gated AI provider adapters
=================================================
One thin interface for chat and one for embeddings, each with an adapter per
provider. The active adapters are chosen purely from config (LLM_PROVIDER /
EMBEDDING_PROVIDER + their credentials), so a provider can be wired without
touching service code.

Integrity rules shared by every adapter:

  * Nothing is fabricated. A provider that is not configured, not installed, or
    that fails its call raises RuntimeError and returns no text or vector. Callers
    already turn that into an honest 503, and the embedding path keeps its
    explicit refusal to store a mock vector.
  * SDKs are imported lazily inside the adapter, so the app boots even when a
    provider's SDK is not installed. Choosing that provider at call time then
    fails with a clear, actionable message.
  * Unknown provider names are refused, never silently routed to another
    provider. A typo must be visible, not quietly hitting different credentials.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx
import structlog

from src.core.config import Settings

logger = structlog.get_logger()

# Unix epoch as an arbitrary deadline guard for threaded boto3 calls. Network
# timeouts already exist inside boto3; this is purely a local ceiling.
_THREAD_CEILING = 120.0


def _not_configured(provider: str, key: str) -> RuntimeError:
    return RuntimeError(
        f"LLM/embedding adapter '{provider}' is selected but {key} is not set. "
        "Refusing to guess another provider's credentials."
    )


def _placeholder(value: Optional[str]) -> bool:
    return not value or "your_" in value or "super_secret" in value


def _is_truthy(value: Optional[str]) -> bool:
    return bool(value and str(value).strip())


# ── Chat completions ─────────────────────────────────────────────────────

class LLMProvider:
    """Chat adapter contract. Implementations must not fabricate output."""

    name: str
    model: str

    def is_configured(self) -> bool:
        raise NotImplementedError

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        raise NotImplementedError


class OpenAICompatProvider(LLMProvider):
    """
    Any provider that speaks the OpenAI chat completions wire format
    (OpenAI, Grok, Nvidia NIM, local Ollama /v1). Differences are purely
    base_url / api_key / model / whether response_format is accepted.
    """

    def __init__(self, settings: Settings, *, name: str) -> None:
        self.settings = settings
        self.name = name
        if name == "openai":
            self._api_key = settings.OPENAI_API_KEY
            self._base_url = None
            self._model = "gpt-4o-mini"
            self._json_mode_ok = True
        elif name == "grok":
            self._api_key = settings.GROK_API_KEY
            self._base_url = "https://api.x.ai/v1"
            self._model = "grok-beta"
            self._json_mode_ok = True
        elif name == "nvidia":
            self._api_key = settings.NVIDIA_API_KEY
            self._base_url = settings.NVIDIA_API_URL
            self._model = "mistralai/mistral-medium-3.5-128b"
            self._json_mode_ok = True
        elif name == "ollama":
            self._api_key = "ollama"  # fixed wire value; Ollama ignores it
            self._base_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/v1"
            self._model = settings.OLLAMA_CHAT_MODEL
            # Ollama's OpenAI-compat layer does not reliably accept
            # response_format; the prompt already demands JSON and Pydantic
            # validation is the real guard, so the hint is simply omitted.
            self._json_mode_ok = False
        else:  # pragma: no cover - factory never routes an unknown name here
            raise ValueError(f"OpenAICompatProvider cannot serve '{name}'")
        self.model = self._model

    def is_configured(self) -> bool:
        # Ollama needs no credential, only the origin in config (which has a
        # default). Reachability is checked by the call itself.
        if self.name == "ollama":
            return True
        return _is_truthy(self._api_key) and not _placeholder(self._api_key)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        if not self.is_configured():
            raise _not_configured(
                self.name,
                {"openai": "OPENAI_API_KEY", "grok": "GROK_API_KEY",
                 "nvidia": "NVIDIA_API_KEY"}.get(self.name, "its credential"),
            )

        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=60.0,
        )
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if json_mode and self._json_mode_ok:
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        return content or ""


class AnthropicProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.name = "anthropic"
        self.model = settings.ANTHROPIC_MODEL
        self._api_key = settings.ANTHROPIC_API_KEY

    def is_configured(self) -> bool:
        return _is_truthy(self._api_key) and not _placeholder(self._api_key)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        if not self.is_configured():
            raise _not_configured(self.name, "ANTHROPIC_API_KEY")

        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=self._api_key, timeout=60.0)
        message = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=temperature,
        )
        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )


class GeminiProvider(LLMProvider):
    """
    google-generativeai with a 404-driven model fallback chain, matching the
    behaviour that predated the adapter split.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.name = "gemini"
        self.model = "gemini-1.5-flash"
        self._fallback_models = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-3.1-flash-lite"]
        self._api_key = settings.GEMINI_API_KEY
        self._client = None

    def is_configured(self) -> bool:
        return _is_truthy(self._api_key) and not _placeholder(self._api_key)

    def _genai(self):
        if self._client is None:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            self._client = genai
        return self._client

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        if not self.is_configured():
            raise _not_configured(self.name, "GEMINI_API_KEY")

        genai = self._genai()
        candidates = [self.model] + self._fallback_models
        last_error: Optional[Exception] = None

        for model_name in candidates:
            try:
                model = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=system,
                )
                generation_config: Dict[str, Any] = {"temperature": temperature}
                if json_mode:
                    generation_config["response_mime_type"] = "application/json"
                response = await model.generate_content_async(
                    user,
                    generation_config=generation_config,
                )
                self.model = model_name
                return response.text if response.text is not None else ""
            except Exception as model_err:
                err_text = str(model_err).lower()
                if "not found" not in err_text and "404" not in err_text:
                    raise model_err
                last_error = model_err
                logger.warn("Gemini model not found, trying fallback", model=model_name)

        raise RuntimeError(
            f"Gemini chat failed: every model in the fallback chain was unavailable "
            f"({len(candidates)} tried)."
        ) from last_error


class BedrockProvider(LLMProvider):
    """
    AWS Bedrock Runtime. boto3 is imported lazily inside the call, so choosing
    this provider without the SDK installed is an honest, actionable error at
    call time rather than a boot failure.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.name = "bedrock"
        self.model = settings.BEDROCK_CHAT_MODEL

    def is_configured(self) -> bool:
        # Credentials may also come from the AWS standard env vars / shared
        # config, so only region + model are strictly ours to check.
        return _is_truthy(self.settings.AWS_REGION)

    def _make_client(self):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "Bedrock adapter selected but boto3 is not installed. "
                "Install it (pip install boto3) to use this provider."
            ) from exc
        kwargs: Dict[str, str] = {"region_name": self.settings.AWS_REGION}
        if self.settings.AWS_ACCESS_KEY_ID:
            kwargs["aws_access_key_id"] = self.settings.AWS_ACCESS_KEY_ID
        if self.settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_secret_access_key"] = self.settings.AWS_SECRET_ACCESS_KEY
        if self.settings.AWS_SESSION_TOKEN:
            kwargs["aws_session_token"] = self.settings.AWS_SESSION_TOKEN
        return boto3.client("bedrock-runtime", **kwargs)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        if not self.is_configured():
            raise _not_configured(self.name, "AWS_REGION")

        client = self._make_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": temperature,
        }
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.invoke_model,
                    modelId=self.model,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(body),
                ),
                timeout=_THREAD_CEILING,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Bedrock invoke_model exceeded the local timeout ceiling.") from exc
        payload = json.loads(response["body"].read())
        return "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )


class CohereProvider(LLMProvider):
    """Cohere Chat v2 over plain httpx — no SDK dependency."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.name = "cohere"
        self.model = settings.COHERE_CHAT_MODEL

    def is_configured(self) -> bool:
        return _is_truthy(self.settings.COHERE_API_KEY) and not _placeholder(
            self.settings.COHERE_API_KEY
        )

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        if not self.is_configured():
            raise _not_configured(self.name, "COHERE_API_KEY")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.cohere.com/v2/chat",
                headers={
                    "Authorization": f"Bearer {self.settings.COHERE_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data.get("message", {}).get("content") or []
        if isinstance(content, str):
            return content
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )


class NoneProvider(LLMProvider):
    """Stand-in for an unknown provider name. Never silently maps to another."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.model = f"<unknown:{name}>"

    def is_configured(self) -> bool:
        return False

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError(
            f"LLM_PROVIDER='{self.name}' is not a known provider. Supported: "
            "anthropic, openai, grok, gemini, nvidia, bedrock, ollama, cohere."
        )


# ── Embeddings ───────────────────────────────────────────────────────────

class EmbeddingProvider:
    """Embedding adapter contract. Implementations must not fabricate vectors."""

    name: str
    model: str

    def is_configured(self) -> bool:
        raise NotImplementedError

    async def embed(self, text: str) -> List[float]:
        raise NotImplementedError


class OpenAICompatEmbeddingProvider(EmbeddingProvider):
    """OpenAI-style /v1/embeddings (OpenAI, Azure, Grok, local Ollama)."""

    def __init__(self, settings: Settings, *, name: str) -> None:
        self.settings = settings
        self.name = name
        self._model = settings.EMBEDDING_MODEL

        if name == "grok":
            self._api_key = settings.GROK_API_KEY
            self._base_url = "https://api.x.ai/v1"
            self._headers = None
            self._query = None
            self._dimensions_ok = False
        elif name == "azure":
            endpoint = (settings.AZURE_EMBEDDING_ENDPOINT or "").rstrip("/")
            for suffix in ("/v1", "/models"):
                if endpoint.endswith(suffix):
                    endpoint = endpoint[: -len(suffix)]
            self._api_key = settings.AZURE_EMBEDDING_API_KEY
            self._base_url = f"{endpoint.rstrip('/')}/models"
            self._headers = {"api-key": settings.AZURE_EMBEDDING_API_KEY}
            self._query = {"api-version": "2024-05-01-preview"}
            self._dimensions_ok = True
        elif name == "ollama":
            self._api_key = "ollama"
            self._base_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/v1"
            self._headers = None
            self._query = None
            self._dimensions_ok = False
            self._model = settings.OLLAMA_EMBEDDING_MODEL
        else:  # openai
            self._api_key = settings.OPENAI_API_KEY
            self._base_url = None
            self._headers = None
            self._query = None
            self._dimensions_ok = "text-embedding-3-" in self._model
        self.model = self._model

    def is_configured(self) -> bool:
        if self.name == "ollama":
            return True
        key = {
            "grok": self._api_key,
            "azure": self._api_key,
            "openai": self._api_key,
        }[self.name]
        return _is_truthy(key) and not _placeholder(key)

    async def embed(self, text: str) -> List[float]:
        if not self.is_configured():
            key = {"openai": "OPENAI_API_KEY", "grok": "GROK_API_KEY",
                   "azure": "AZURE_EMBEDDING_API_KEY"}[self.name]
            raise RuntimeError(
                f"Embedding adapter '{self.name}' is configured but {key} is a "
                "placeholder or unset. Refusing to generate a fake vector."
            )

        from openai import AsyncOpenAI
        kwargs: Dict[str, Any] = {
            "api_key": self._api_key,
            "base_url": self._base_url,
        }
        if self._headers:
            kwargs["default_headers"] = self._headers
        if self._query:
            kwargs["default_query"] = self._query
        client = AsyncOpenAI(**kwargs)

        call_kwargs: Dict[str, Any] = {"input": text, "model": self._model}
        if self._dimensions_ok:
            call_kwargs["dimensions"] = 1536
        response = await client.embeddings.create(**call_kwargs)
        return list(response.data[0].embedding)


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.name = "gemini"
        self.model = "models/text-embedding-004"
        self._fallback_models = ["models/gemini-embedding-2", "models/gemini-embedding-001"]
        self._api_key = settings.GEMINI_API_KEY
        self._client = None

    def is_configured(self) -> bool:
        return _is_truthy(self._api_key) and not _placeholder(self._api_key)

    def _genai(self):
        if self._client is None:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            self._client = genai
        return self._client

    async def embed(self, text: str) -> List[float]:
        if not self.is_configured():
            raise RuntimeError(
                "Embedding adapter 'gemini' is configured but GEMINI_API_KEY is "
                "a placeholder or unset. Refusing to generate a fake vector."
            )

        genai = self._genai()
        candidates = [self.model] + self._fallback_models
        last_error: Optional[Exception] = None
        response = None
        for model_name in candidates:
            try:
                response = await genai.embed_content_async(
                    model=model_name,
                    content=text,
                    output_dimensionality=768,
                )
                self.model = model_name
                break
            except Exception as model_err:
                err_text = str(model_err).lower()
                if "not found" not in err_text and "404" not in err_text:
                    raise model_err
                last_error = model_err
                logger.warn("Gemini embedding model not found, trying fallback", model=model_name)

        if response is None:
            raise RuntimeError(
                f"Gemini embeddings failed: every model in the fallback chain was "
                f"unavailable ({len(candidates)} tried)."
            ) from last_error

        embed_obj = response.get("embedding") if isinstance(response, dict) else getattr(
            response, "embedding", []
        )
        if isinstance(embed_obj, dict) and "values" in embed_obj:
            vector = embed_obj["values"]
        elif hasattr(embed_obj, "values"):
            vector = embed_obj.values
        else:
            vector = embed_obj
        return list(vector)


class CohereEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.name = "cohere"
        self.model = settings.COHERE_EMBEDDING_MODEL

    def is_configured(self) -> bool:
        return _is_truthy(self.settings.COHERE_API_KEY) and not _placeholder(
            self.settings.COHERE_API_KEY
        )

    async def embed(self, text: str) -> List[float]:
        if not self.is_configured():
            raise RuntimeError(
                "Embedding adapter 'cohere' is configured but COHERE_API_KEY is "
                "a placeholder or unset. Refusing to generate a fake vector."
            )

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.cohere.com/v2/embed",
                headers={
                    "Authorization": f"Bearer {self.settings.COHERE_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "texts": [text],
                    "input_type": "search_document",
                    "embedding_types": ["float"],
                },
            )
            response.raise_for_status()
            data = response.json()

        embeddings = data.get("embeddings", {})
        floats = embeddings.get("floats") if isinstance(embeddings, dict) else embeddings
        if isinstance(floats, list) and floats and isinstance(floats[0], list):
            return list(floats[0])
        raise RuntimeError("Cohere embed response did not contain a float embedding.")


class BedrockEmbeddingProvider(EmbeddingProvider):
    """Amazon Titan embeddings via Bedrock Runtime (lazy boto3)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.name = "bedrock"
        self.model = settings.BEDROCK_EMBEDDING_MODEL

    def is_configured(self) -> bool:
        return _is_truthy(self.settings.AWS_REGION)

    async def embed(self, text: str) -> List[float]:
        if not self.is_configured():
            raise RuntimeError(
                "Embedding adapter 'bedrock' is configured but AWS_REGION is not "
                "set. Refusing to generate a fake vector."
            )

        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "Bedrock embedding adapter selected but boto3 is not installed. "
                "Install it (pip install boto3) to use this provider."
            ) from exc

        kwargs: Dict[str, str] = {"region_name": self.settings.AWS_REGION}
        if self.settings.AWS_ACCESS_KEY_ID:
            kwargs["aws_access_key_id"] = self.settings.AWS_ACCESS_KEY_ID
        if self.settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_secret_access_key"] = self.settings.AWS_SECRET_ACCESS_KEY
        if self.settings.AWS_SESSION_TOKEN:
            kwargs["aws_session_token"] = self.settings.AWS_SESSION_TOKEN
        client = boto3.client("bedrock-runtime", **kwargs)

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.invoke_model,
                    modelId=self.model,
                    accept="application/json",
                    contentType="application/json",
                    body=json.dumps({"inputText": text}),
                ),
                timeout=_THREAD_CEILING,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Bedrock embed exceeded the local timeout ceiling.") from exc
        payload = json.loads(response["body"].read())
        vector = payload.get("embedding")
        if not isinstance(vector, list):
            raise RuntimeError("Bedrock embed response did not contain an embedding.")
        return list(vector)


class NoneEmbeddingProvider(EmbeddingProvider):
    def __init__(self, name: str) -> None:
        self.name = name
        self.model = f"<unknown:{name}>"

    def is_configured(self) -> bool:
        return False

    async def embed(self, text: str) -> List[float]:
        raise RuntimeError(
            f"EMBEDDING_PROVIDER='{self.name}' is not a known provider. Supported: "
            "openai, gemini, grok, azure, bedrock, ollama, cohere."
        )


# ── Factories ────────────────────────────────────────────────────────────

_CHAT_FACTORIES: Dict[str, Any] = {
    "openai": lambda s: OpenAICompatProvider(s, name="openai"),
    "grok": lambda s: OpenAICompatProvider(s, name="grok"),
    "nvidia": lambda s: OpenAICompatProvider(s, name="nvidia"),
    "ollama": lambda s: OpenAICompatProvider(s, name="ollama"),
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "bedrock": BedrockProvider,
    "cohere": CohereProvider,
}

_EMBED_FACTORIES: Dict[str, Any] = {
    "openai": lambda s: OpenAICompatEmbeddingProvider(s, name="openai"),
    "grok": lambda s: OpenAICompatEmbeddingProvider(s, name="grok"),
    "azure": lambda s: OpenAICompatEmbeddingProvider(s, name="azure"),
    "ollama": lambda s: OpenAICompatEmbeddingProvider(s, name="ollama"),
    "gemini": GeminiEmbeddingProvider,
    "bedrock": BedrockEmbeddingProvider,
    "cohere": CohereEmbeddingProvider,
}


def build_llm_provider(settings: Optional[Settings] = None) -> LLMProvider:
    """Return the chat adapter for settings.LLM_PROVIDER, never another one."""
    cfg = settings if settings is not None else _import_global_settings()
    factory = _CHAT_FACTORIES.get(cfg.LLM_PROVIDER)
    if factory is None:
        return NoneProvider(cfg.LLM_PROVIDER)
    return factory(cfg)


def build_embedding_provider(settings: Optional[Settings] = None) -> EmbeddingProvider:
    """Return the embedding adapter for settings.EMBEDDING_PROVIDER, never another one."""
    cfg = settings if settings is not None else _import_global_settings()
    factory = _EMBED_FACTORIES.get(cfg.EMBEDDING_PROVIDER)
    if factory is None:
        return NoneEmbeddingProvider(cfg.EMBEDDING_PROVIDER)
    return factory(cfg)


def _import_global_settings() -> Settings:
    from src.core.config import settings as global_settings
    return global_settings