"""
Mela AI - Google Gemini Service

Uses the google-genai SDK (free tier, Google AI Studio key).
The API key is loaded exclusively from GOOGLE_AI_API_KEY — it is never
logged, never included in prompts, and never returned to clients.
"""

import logging
from typing import AsyncGenerator, Dict, Any, List, Optional

try:
    from google import genai as _genai_lib
    from google.genai import types as _genai_types
    _GEMINI_AVAILABLE = True
except ImportError:
    _genai_lib = None  # type: ignore[assignment]
    _genai_types = None  # type: ignore[assignment]
    _GEMINI_AVAILABLE = False

from app.core.config import settings
from app.schemas.chat import StreamChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model catalogue — one cheap free-tier model
# ---------------------------------------------------------------------------
GEMINI_MODELS: Dict[str, Dict[str, Any]] = {
    "gemini-2.0-flash": {
        "model_id": "gemini-2.0-flash",
        "max_tokens": settings.GEMINI_MAX_TOKENS,
        "supports_vision": False,
        "supports_tools": False,
        "is_default": False,
        "description": "Gemini 2.0 Flash – fast, free-tier Google AI model",
    },
}


# ---------------------------------------------------------------------------
# Message-format converter  (OpenAI → Google Gemini)
# ---------------------------------------------------------------------------
def _convert_messages(
    messages: List[Dict[str, Any]],
) -> "tuple[str, list]":
    """Extract system instruction; convert the rest to Gemini Content objects.

    OpenAI format: [{role, content}, ...]
    google-genai v1 format: system_instruction str + [types.Content, ...]
    Note: Gemini uses 'model' instead of 'assistant' for AI turns.
    Consecutive same-role messages are merged (strict alternation required).
    """
    # Lazy import so the module-level import guard still protects us
    from google.genai import types as _t

    system_parts: List[str] = []
    # Intermediate plain-text accumulator before building typed objects
    raw: List[Dict[str, str]] = []  # [{role, text}, ...]

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue

        # Skip assistant messages that were only tool calls (no text)
        if role == "assistant" and not content and msg.get("tool_calls"):
            continue

        if role == "tool":
            raw.append({"role": "user", "text": f"[Tool result]: {content}"})
            continue

        gemini_role = "model" if role == "assistant" else "user"

        if isinstance(content, list):
            # Multi-part / vision — extract text only (no vision support)
            text = " ".join(
                p.get("text", "")
                for p in content
                if p.get("type") == "text"
            )
            raw.append({"role": gemini_role, "text": text or "(image)"})
        else:
            raw.append({"role": gemini_role, "text": content or ""})

    # Merge consecutive same-role turns
    merged: List[Dict[str, str]] = []
    for item in raw:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["text"] += "\n" + item["text"]
        else:
            merged.append(dict(item))

    # Gemini requires the last turn to be from the user
    if merged and merged[-1]["role"] != "user":
        merged.append({"role": "user", "text": "Please continue."})

    if not merged:
        merged = [{"role": "user", "text": "Hello"}]

    # Build typed Content objects required by google-genai v1
    contents = [
        _t.Content(role=m["role"], parts=[_t.Part(text=m["text"])])
        for m in merged
    ]

    system_text = "\n\n".join(system_parts)
    return system_text, contents


# ---------------------------------------------------------------------------
# GeminiService
# ---------------------------------------------------------------------------
class GeminiService:
    """Streams Gemini responses using the Google AI free-tier API.

    The API key is loaded from settings.GOOGLE_AI_API_KEY
    (GOOGLE_AI_API_KEY env var). It is never exposed to clients.
    """

    def __init__(self) -> None:
        if not _GEMINI_AVAILABLE:
            raise RuntimeError(
                "google-genai package is not installed. "
                "Run: pip install 'google-genai>=1.0.0'"
            )
        if not settings.GOOGLE_AI_API_KEY:
            raise RuntimeError(
                "GOOGLE_AI_API_KEY is not configured. "
                "Add it to your environment (.env.local)."
            )
        self._client = _genai_lib.Client(
            api_key=settings.GOOGLE_AI_API_KEY,
        )
        logger.info("GeminiService initialised (gemini-2.0-flash)")

    async def stream_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gemini-2.0-flash",
        *,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream a Gemini response as StreamChunk events.

        Converts OpenAI-style messages to Gemini-native Content/Part objects
        before sending.  Yields content chunks, then a done chunk.
        """
        cfg = GEMINI_MODELS.get(model, GEMINI_MODELS["gemini-2.0-flash"])
        model_id: str = cfg["model_id"]
        max_tokens: int = cfg["max_tokens"]

        system_text, contents = _convert_messages(messages)

        logger.debug(
            "Gemini request: model=%s turns=%d sys=%s",
            model_id,
            len(contents),
            bool(system_text),
        )

        try:
            config = _genai_types.GenerateContentConfig(
                system_instruction=system_text or None,
                max_output_tokens=max_tokens,
                temperature=0.7,
            )

            total_chars = 0
            # generate_content_stream returns a coroutine yielding an
            # async iterator in google-genai v1.x — await it first.
            async for chunk in await self._client.aio.models.generate_content_stream(
                model=model_id,
                contents=contents,
                config=config,
            ):
                text = chunk.text or ""
                if text:
                    total_chars += len(text)
                    yield StreamChunk(type="content", content=text)

            approx_tokens = max(total_chars // 4, 1)
            yield StreamChunk(
                type="done",
                data={"model": model, "total_tokens": approx_tokens},
            )

        except Exception as exc:
            exc_str = str(exc)
            # Surface quota/rate-limit errors with a clear user message
            if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str:
                user_msg = (
                    "Gemini free-tier quota exceeded. "
                    "Please try again later or switch to another model."
                )
            else:
                user_msg = f"Gemini error: {type(exc).__name__}: {exc}"
            logger.error(
                "Gemini stream error (model=%s): %s", model_id, exc
            )
            yield StreamChunk(type="error", content=user_msg)


# ---------------------------------------------------------------------------
# Module-level singleton — None if not configured
# ---------------------------------------------------------------------------
gemini_service: Optional[GeminiService] = None

if settings.GEMINI_ENABLED and settings.GOOGLE_AI_API_KEY:
    try:
        gemini_service = GeminiService()
    except Exception as _exc:
        logger.warning("GeminiService not available: %s", _exc)
