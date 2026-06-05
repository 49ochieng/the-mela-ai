"""
Mela AI - Azure OpenAI Service
"""

import logging
import asyncio
from typing import AsyncGenerator, List, Dict, Any, Optional
from openai import AsyncAzureOpenAI, APIConnectionError, APITimeoutError, RateLimitError, BadRequestError, NotFoundError
import tiktoken
import json

from app.core.config import settings
from app.schemas.chat import StreamChunk

logger = logging.getLogger(__name__)

# Retry config — keep delays short so users don't see noticeable pauses.
# On transient errors we try 2 more times (0.2 s → 0.4 s) then fall back to
# the next model in the chain, rather than waiting several seconds on one model.
_MAX_RETRIES = 2
_RETRY_DELAY = 0.2  # seconds, doubled each attempt (0.2 → 0.4)

# Per-model fallback chains — ordered by preference when a model fails.
# Non-GPT models fall back within their family first, then to GPT backbone.
GPT_FALLBACK_CHAIN = ["gpt-5.2-chat", "gpt-4.1", "gpt-4o", "grok-3-mini"]

_MODEL_FALLBACK_MAP: dict[str, list[str]] = {
    # GPT family: cascade down to smaller/faster models
    "gpt-5.2-chat":   ["gpt-4.1", "gpt-4o", "grok-3-mini"],
    "gpt-4.1":        ["gpt-4o", "grok-3-mini", "gpt-5.2-chat"],
    "gpt-4o":         ["gpt-4.1", "grok-3-mini", "gpt-5.2-chat"],
    # Non-GPT: sibling models first, then reliable GPT backbone
    "kimi-k2.5":      ["mistral-large-3", "grok-3-mini", "gpt-4.1"],
    "mistral-large-3":["grok-3-mini", "kimi-k2.5", "gpt-4.1"],
    "grok-3-mini":    ["gpt-4.1", "gpt-4o", "kimi-k2.5"],
    "llama-4-maverick-17b-128e-instruct-fp8": ["kimi-k2.5", "mistral-large-3", "gpt-4.1"],
    "llama-4-maverick":                        ["kimi-k2.5", "mistral-large-3", "gpt-4.1"],
}


class AzureOpenAIService:
    """Service for interacting with Azure OpenAI / AI Foundry deployments."""

    def __init__(self):
        self._api_version = settings.AZURE_OPENAI_API_VERSION
        self.encoding = tiktoken.get_encoding("cl100k_base")

        # Default client (AI Foundry main endpoint)
        self._default_endpoint = settings.effective_openai_endpoint
        self._default_api_key  = settings.effective_openai_api_key
        self.client = self._make_client(self._default_endpoint, self._default_api_key)

        # Cache for per-endpoint clients: (endpoint, api_key) → AsyncAzureOpenAI
        self._client_cache: Dict[str, AsyncAzureOpenAI] = {}

        # Unified model registry
        # no_temperature=True  → skip temperature param (model only supports default)
        # use_completion_tokens=True  → send max_completion_tokens (newer OpenAI models)
        # use_completion_tokens=False → send max_tokens (legacy / third-party models)
        # endpoint / api_key → override client for this model (different Azure resource)
        self.models: Dict[str, Dict[str, Any]] = {
            # ── GPT-4.1 ──────────────────────────────────────────────────────
            "gpt-4.1": {
                "deployment": settings.DEPLOYMENT_GPT41,
                "max_tokens": 128000,
                "supports_vision": True,
                "supports_tools": True,
                "is_default": False,
                "use_completion_tokens": False,
                "no_temperature": False,
                "description": "GPT-4.1 – latest GPT-4 generation with vision",
            },
            # ── GPT-5.2-chat ─────────────────────────────────────────────────
            # Requires max_completion_tokens (not max_tokens) and no temperature.
            # Uses a newer API version for compatibility with the deployment.
            "gpt-5.2-chat": {
                "deployment": settings.DEPLOYMENT_GPT52_CHAT,
                "max_tokens": 128000,
                "supports_vision": True,
                "supports_tools": True,
                "is_default": True,
                "use_completion_tokens": True,
                "no_temperature": True,
                "api_version": "2025-01-01-preview",
                "description": "GPT-5.2 – next-gen frontier model",
            },
            # ── Kimi-K2.5 ────────────────────────────────────────────────────
            "kimi-k2.5": {
                "deployment": settings.DEPLOYMENT_KIMI_K25,
                "max_tokens": 131072,
                "supports_vision": False,
                "supports_tools": True,
                "is_default": False,
                "use_completion_tokens": False,
                "no_temperature": False,
                "description": "Kimi-K2.5 – long-context reasoning model",
            },
            # ── Mistral-Large-3 ──────────────────────────────────────────────
            "mistral-large-3": {
                "deployment": settings.DEPLOYMENT_MISTRAL_LARGE_3,
                "max_tokens": 131072,
                "supports_vision": False,
                "supports_tools": True,
                "is_default": False,
                "use_completion_tokens": False,
                "no_temperature": False,
                "description": "Mistral Large 3 – high-performance multilingual model",
            },
            # ── Grok-3-mini (xAI via Azure AI Foundry) ───────────────────────
            "grok-3-mini": {
                "deployment": settings.DEPLOYMENT_GROK3_MINI,
                "max_tokens": 131072,
                "supports_vision": False,
                "supports_tools": True,
                "is_default": False,
                "use_completion_tokens": False,
                "no_temperature": False,
                "description": "Grok-3-mini – xAI reasoning model, fast & cost-efficient",
            },
            # ── Llama-4-Maverick ─────────────────────────────────────────────
            "llama-4-maverick": {
                "deployment": settings.DEPLOYMENT_LLAMA4_MAVERICK,
                "max_tokens": 131072,
                "supports_vision": False,
                "supports_tools": True,
                "is_default": False,
                "use_completion_tokens": False,
                "no_temperature": False,
                "description": "Llama 4 Maverick 17B – Meta MoE model",
            },
            # Long-form model ID alias — frontend may send the full deployment name
            "llama-4-maverick-17b-128e-instruct-fp8": {
                "deployment": settings.DEPLOYMENT_LLAMA4_MAVERICK,
                "max_tokens": 131072,
                "supports_vision": False,
                "supports_tools": True,
                "is_default": False,
                "use_completion_tokens": False,
                "no_temperature": False,
                "description": "Llama 4 Maverick 17B – Meta MoE model",
            },
            "gpt-4o-mini": {
                "deployment": settings.AZURE_OPENAI_FAST_DEPLOYMENT,
                "max_tokens": 128000,
                "supports_vision": False,
                "supports_tools": True,
                "is_default": False,
                "use_completion_tokens": False,
                "no_temperature": False,
                "description": "Fast / efficient model",
            },
        }

        # ── GPT-4o (separate Azure OpenAI resource) ─────────────────────────
        if settings.GPT4O_ENDPOINT and settings.GPT4O_API_KEY:
            self.models["gpt-4o"] = {
                "deployment": settings.GPT4O_DEPLOYMENT or "gpt-4o",
                "max_tokens": 128000,
                "supports_vision": True,
                "supports_tools": True,
                "is_default": False,
                "use_completion_tokens": False,
                "no_temperature": False,
                "endpoint": settings.GPT4O_ENDPOINT,
                "api_key": settings.GPT4O_API_KEY,
                "description": "GPT-4o – fast multimodal model with vision",
            }

    def _make_client(self, endpoint: str, api_key: str) -> AsyncAzureOpenAI:
        return AsyncAzureOpenAI(
            api_key=api_key,
            api_version=self._api_version,
            azure_endpoint=endpoint,
            timeout=120.0,
            max_retries=0,
        )

    def _get_client(self, model: str) -> AsyncAzureOpenAI:
        """Return the correct Azure OpenAI client for a given model.

        Supports per-model endpoint, api_key, and api_version overrides.
        GPT-5.2 and other newer models may require a different api_version.
        """
        cfg = self.models.get(model, {})
        endpoint = cfg.get("endpoint", "") or self._default_endpoint
        api_key = cfg.get("api_key", "") or self._default_api_key
        api_version = cfg.get("api_version", "") or self._api_version
        cache_key = f"{endpoint}:{api_key}:{api_version}"
        if cache_key not in self._client_cache:
            self._client_cache[cache_key] = AsyncAzureOpenAI(
                api_key=api_key,
                api_version=api_version,
                azure_endpoint=endpoint,
                timeout=120.0,
                max_retries=0,
            )
        return self._client_cache[cache_key]

    def _model_no_temperature(self, model: str) -> bool:
        """Return True if model does not accept a temperature parameter."""
        return self.models.get(model, {}).get("no_temperature", False)

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        try:
            return len(self.encoding.encode(text))
        except Exception:
            # Rough estimate if encoding fails
            return len(text) // 4

    async def get_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gpt-4.1",
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> Optional[str]:
        """Non-streaming helper — returns just the text content string."""
        try:
            result = await self.create_completion(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return result.get("content", "") if isinstance(result, dict) else None
        except Exception as e:
            logger.warning(f"get_completion failed: {e}")
            return None

    def get_deployment(self, model: str) -> str:
        """Get deployment name for model."""
        return self.models.get(model, {}).get("deployment", settings.AZURE_OPENAI_CHAT_DEPLOYMENT)

    def model_supports_vision(self, model: str) -> bool:
        """Check whether the requested model supports image inputs."""
        return self.models.get(model, {}).get("supports_vision", False)

    def _uses_completion_tokens(self, model: str) -> bool:
        """Return True if the model requires max_completion_tokens instead of max_tokens."""
        return self.models.get(model, {}).get("use_completion_tokens", False)

    def _get_safe_max_tokens(self, model: str, requested: int = 16384) -> int:
        """Return a safe output-token value that won't exceed the model's context."""
        model_max = self.models.get(model, {}).get("max_tokens", 128000)
        # Leave room for the prompt (use at most 50% of context for output)
        safe_max = min(requested, model_max // 2, 32768)
        return max(safe_max, 1024)

    def _build_token_kwarg(self, model: str, requested: int = 16384) -> Dict[str, Any]:
        """Return the correct token-limit kwarg dict for the given model.

        openai SDK 1.50.2 does not expose max_completion_tokens as a first-class
        keyword on AsyncCompletions.create(); pass it via extra_body so it reaches
        the Azure backend without triggering a TypeError.
        """
        safe_max = self._get_safe_max_tokens(model, requested)
        if self._uses_completion_tokens(model):
            return {"extra_body": {"max_completion_tokens": safe_max}}
        return {"max_tokens": safe_max}

    def get_fallback_models(self, current_model: str) -> List[str]:
        """Return fallback models to try after current_model fails.

        Uses per-model chains from _MODEL_FALLBACK_MAP when available,
        otherwise falls back to the legacy GPT_FALLBACK_CHAIN.
        """
        key = current_model.lower()
        if key in _MODEL_FALLBACK_MAP:
            chain = _MODEL_FALLBACK_MAP[key]
        elif current_model in GPT_FALLBACK_CHAIN:
            idx = GPT_FALLBACK_CHAIN.index(current_model)
            chain = GPT_FALLBACK_CHAIN[idx + 1:]
        else:
            chain = GPT_FALLBACK_CHAIN
        # Filter to only models that are registered and not the current one
        return [m for m in chain if m in self.models and m != current_model]

    def _is_retryable_with_fallback(self, exc: Exception) -> bool:
        """Return True if the error should trigger a model fallback."""
        if isinstance(exc, RateLimitError):
            return True
        if isinstance(exc, NotFoundError):
            # Model/deployment not found → try next model in chain
            return True
        if isinstance(exc, BadRequestError):
            msg = str(exc).lower()
            # Deployment capacity, token limit, or content filter → try next model
            if any(kw in msg for kw in ("token", "context_length", "too long", "content_length", "rate",
                                         "deployment", "capacity", "quota", "unavailable")):
                return True
        return False

    async def _with_retry(self, coro_fn, *args, **kwargs):
        """Execute an async callable with exponential back-off for transient errors."""
        delay = _RETRY_DELAY
        for attempt in range(_MAX_RETRIES):
            try:
                return await coro_fn(*args, **kwargs)
            except (APIConnectionError, APITimeoutError) as exc:
                if attempt == _MAX_RETRIES - 1:
                    raise
                logger.warning(f"Transient OpenAI error (attempt {attempt + 1}): {exc}. Retrying in {delay}s…")
                await asyncio.sleep(delay)
                delay *= 2
            except RateLimitError:
                if attempt == _MAX_RETRIES - 1:
                    raise
                wait = delay * (2 ** attempt)
                logger.warning(f"Rate limited (attempt {attempt + 1}). Retrying in {wait}s…")
                await asyncio.sleep(wait)

    async def create_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gpt-5.2-chat",
        max_tokens: int = 16384,
        temperature: float = 0.7,
        tools: Optional[List[Dict]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Create a chat completion (non-streaming) with automatic GPT fallback."""
        models_to_try = [model] + self.get_fallback_models(model)
        last_error: Optional[Exception] = None

        for attempt_model in models_to_try:
            deployment = self.get_deployment(attempt_model)
            client = self._get_client(attempt_model)

            kwargs: Dict[str, Any] = {
                "model": deployment,
                "messages": messages,
                "stream": stream,
                **self._build_token_kwarg(attempt_model, max_tokens),
            }
            if not self._model_no_temperature(attempt_model):
                kwargs["temperature"] = temperature

            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            try:
                if attempt_model != model:
                    logger.info(f"Auto-switching from {model} to {attempt_model} due to: {last_error}")

                response = await self._with_retry(client.chat.completions.create, **kwargs)

                if not stream:
                    result = {
                        "content": response.choices[0].message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "name": tc.function.name,
                                "arguments": json.loads(tc.function.arguments or "{}"),
                            }
                            for tc in (response.choices[0].message.tool_calls or [])
                        ],
                        "usage": {
                            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                            "total_tokens": response.usage.total_tokens if response.usage else 0,
                        },
                        "finish_reason": response.choices[0].finish_reason,
                        "model_used": attempt_model,
                    }
                    return result

                return response

            except (RateLimitError, BadRequestError, NotFoundError) as e:
                if self._is_retryable_with_fallback(e):
                    last_error = e
                    logger.warning(f"Model {attempt_model} failed (retryable), trying fallback: {e}")
                    await asyncio.sleep(1)
                    continue
                raise

            except (APIConnectionError, APITimeoutError) as e:
                last_error = e
                logger.warning(f"Connection/timeout error [{attempt_model}]: {e}")
                await asyncio.sleep(1)
                continue

            except Exception as e:
                # Unknown error — try next model rather than raising immediately
                last_error = e
                logger.warning(f"Unknown completion error [{attempt_model}], trying fallback: {e}")
                await asyncio.sleep(1)
                continue

        # All models exhausted
        raise Exception(f"All AI models are at capacity. Last error: {last_error}")

    async def _stream_single_model(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]],
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream from a single model. Raises on error (caller handles fallback)."""
        deployment = self.get_deployment(model)
        client = self._get_client(model)

        kwargs: Dict[str, Any] = {
            "model": deployment,
            "messages": messages,
            "stream": True,
            **self._build_token_kwarg(model, max_tokens),
        }
        if not self._model_no_temperature(model):
            kwargs["temperature"] = temperature

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await client.chat.completions.create(**kwargs)

        tool_calls_buffer: Dict[int, Dict[str, Any]] = {}

        async for chunk in response:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            if delta.content:
                yield StreamChunk(type="content", content=delta.content)

            # Reasoning models (Kimi-K2.5, DeepSeek-R1, etc.) stream thinking
            # via reasoning_content while content stays None during think phase.
            reasoning = getattr(delta, "reasoning_content", None) or (
                (getattr(delta, "model_extra", None) or {}).get("reasoning_content")
            )
            if reasoning:
                yield StreamChunk(type="thinking", content=reasoning)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if tc.id:
                        tool_calls_buffer[idx] = {
                            "id": tc.id,
                            "name": tc.function.name if tc.function else "",
                            "arguments": "",
                        }
                    elif idx in tool_calls_buffer and tc.function and tc.function.name:
                        tool_calls_buffer[idx]["name"] = tc.function.name
                    if tc.function and tc.function.arguments and idx in tool_calls_buffer:
                        tool_calls_buffer[idx]["arguments"] += tc.function.arguments

            finish = chunk.choices[0].finish_reason
            if finish:
                if finish == "tool_calls":
                    for tc in tool_calls_buffer.values():
                        try:
                            tc["arguments"] = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError:
                            tc["arguments"] = {}
                        yield StreamChunk(type="tool_call", data=tc)

                yield StreamChunk(type="done", data={"finish_reason": finish})

    async def stream_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gpt-5.2-chat",
        max_tokens: int = 16384,
        temperature: float = 0.7,
        tools: Optional[List[Dict]] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream a chat completion with automatic GPT model fallback on rate/token errors."""
        models_to_try = [model] + self.get_fallback_models(model)
        last_error: Optional[Exception] = None

        for attempt_model in models_to_try:
            try:
                if attempt_model != model:
                    logger.info(f"Auto-switching from {model} to {attempt_model} due to: {last_error}")
                    yield StreamChunk(
                        type="model_switched",
                        content=f"Switched to {attempt_model} due to capacity limits on {model}.",
                        data={"from_model": model, "to_model": attempt_model},
                    )

                async for chunk in self._stream_single_model(
                    messages, attempt_model, max_tokens, temperature, tools
                ):
                    yield chunk
                return  # success — exit

            except (RateLimitError, BadRequestError, NotFoundError) as e:
                if self._is_retryable_with_fallback(e):
                    last_error = e
                    logger.warning(
                        f"Model {attempt_model} failed (retryable), trying fallback: {e}"
                    )
                    await asyncio.sleep(1)
                    continue
                # Non-retryable (e.g. content policy violation) — log details, show safe msg
                logger.error(f"Non-retryable error [{attempt_model}]: {e}")
                yield StreamChunk(
                    type="error",
                    content=(
                        "I'm unable to respond to that request. "
                        "Please rephrase your message and try again."
                    ),
                )
                return

            except (APIConnectionError, APITimeoutError) as e:
                last_error = e
                logger.warning(f"Connection/timeout error [{attempt_model}]: {e}")
                await asyncio.sleep(1)
                continue

            except Exception as e:
                # Unknown error — try next model rather than bailing immediately
                last_error = e
                logger.warning(f"Unknown streaming error [{attempt_model}], trying fallback: {e}")
                await asyncio.sleep(1)
                continue

        # All models exhausted
        logger.error(f"All fallback models exhausted. Last error: {last_error}")
        yield StreamChunk(
            type="error",
            content="All AI models are currently at capacity. Please try again in a moment.",
        )

    async def get_embedding(self, text: str) -> Optional[List[float]]:
        """Compute a 1536-dim embedding for *text* using ``text-embedding-3-small``.

        Phase 4 helper for the orchestration brain's KB.  Returns ``None``
        on any failure — callers (knowledge.py ingest) MUST treat this as
        "no embedding, persist to SQL only" rather than aborting the
        write.  Never raises.
        """
        if not text or not text.strip():
            return None
        try:
            response = await self._with_retry(
                self.client.embeddings.create,
                model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
                input=text,
            )
            return list(response.data[0].embedding)
        except Exception as exc:  # noqa: BLE001 — best-effort, never raise
            logger.warning("get_embedding failed: %s", exc)
            return None

    async def create_embedding(self, text: str) -> List[float]:
        """Create embedding for text."""
        try:
            response = await self._with_retry(
                self.client.embeddings.create,
                model=settings.DEPLOYMENT_EMBEDDING,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            raise

    async def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create embeddings for multiple texts."""
        try:
            response = await self._with_retry(
                self.client.embeddings.create,
                model=settings.DEPLOYMENT_EMBEDDING,
                input=texts,
            )
            return [d.embedding for d in response.data]
        except Exception as e:
            logger.error(f"Batch embedding error: {e}")
            raise


# Singleton instance
try:
    openai_service = AzureOpenAIService()
except Exception as e:
    logger.warning(f"Failed to initialize AzureOpenAIService: {e}")
    openai_service = None
