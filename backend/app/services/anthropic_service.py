"""
Mela AI - Anthropic Claude Service

The API key is loaded exclusively from the ANTHROPIC_API_KEY environment
variable — it is never logged, never included in prompts or responses,
and never returned to clients.
"""

import logging
import time
from collections import defaultdict, deque
from typing import AsyncGenerator, Dict, Any, List, Optional

try:
    import anthropic as _anthropic_lib
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _anthropic_lib = None  # type: ignore[assignment]
    _ANTHROPIC_AVAILABLE = False

from app.core.config import settings
from app.schemas.chat import StreamChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model catalogue — preview models with hard token caps to control spend
# ---------------------------------------------------------------------------
ANTHROPIC_MODELS: Dict[str, Dict[str, Any]] = {
    "claude-sonnet-4-6": {
        "model_id": "claude-sonnet-4-6",
        "max_tokens": settings.ANTHROPIC_MAX_TOKENS_SONNET,
        "supports_vision": True,
        "supports_tools": True,  # Enable tool calling
        "is_default": False,
        "description": "Claude Sonnet 4.6 \u2013 advanced reasoning & writing",
    },
    "claude-haiku-4-5": {
        "model_id": "claude-haiku-4-5-20251001",
        "max_tokens": settings.ANTHROPIC_MAX_TOKENS_HAIKU,
        "supports_vision": True,
        "supports_tools": True,  # Enable tool calling
        "is_default": False,
        "description": "Claude Haiku 4.5 \u2013 fast, efficient responses",
    },
}


# ---------------------------------------------------------------------------
# Per-user sliding-window rate limiter
# ---------------------------------------------------------------------------
class _AnthropicRateLimiter:
    """Thread-safe (asyncio) sliding-window rate limiter."""

    def __init__(self, rpm: int) -> None:
        self._rpm = rpm
        self._windows: Dict[str, deque] = defaultdict(deque)

    def check_and_consume(self, user_id: str) -> tuple[bool, int]:
        """Return (allowed, remaining_this_window).

        Consumes one slot if allowed; drops expired entries first.
        """
        now = time.monotonic()
        window = self._windows[user_id]
        # Expire entries older than 60 s
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self._rpm:
            return False, 0
        window.append(now)
        return True, self._rpm - len(window)

    def remaining(self, user_id: str) -> int:
        now = time.monotonic()
        window = self._windows[user_id]
        while window and now - window[0] > 60.0:
            window.popleft()
        return max(0, self._rpm - len(window))


# ---------------------------------------------------------------------------
# Message-format converter  (OpenAI → Anthropic)
# ---------------------------------------------------------------------------
def _convert_messages(
    messages: List[Dict[str, Any]],
) -> tuple[str, List[Dict[str, Any]]]:
    """Split system messages out; convert the rest to Anthropic format.

    OpenAI format: [{role, content}, ...]
    Anthropic format: system str + [{role, content}, ...]

    Handles history that contains OpenAI tool_call turns:
    - assistant messages with tool_calls (content=None) are skipped —
      they have no text content Claude can use.
    - tool role messages are converted to user messages.
    - consecutive same-role messages are merged so Anthropic's strict
      alternating-turn requirement is always satisfied.
    """
    system_parts: List[str] = []
    raw: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            continue

        # Skip assistant messages that were only tool calls (no text content)
        if role == "assistant" and not content and msg.get("tool_calls"):
            continue

        if role == "tool":
            # Convert tool result to user turn so Claude sees the outcome
            raw.append({
                "role": "user",
                "content": f"[Tool result]: {content}",
            })
            continue

        if isinstance(content, list):
            # Vision / multi-part content
            parts: List[Dict[str, Any]] = []
            for part in content:
                if part.get("type") == "text":
                    parts.append({"type": "text", "text": part["text"]})
                elif part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        header, data = url.split(",", 1)
                        media_type = header.split(":")[1].split(";")[0]
                        parts.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": data,
                            },
                        })
                    else:
                        parts.append({
                            "type": "image",
                            "source": {"type": "url", "url": url},
                        })
            raw.append({"role": role, "content": parts})
        else:
            raw.append({"role": role, "content": content or ""})

    # Merge consecutive messages of the same role — Anthropic requires
    # strict user/assistant alternation.
    converted: List[Dict[str, Any]] = []
    for msg in raw:
        if converted and converted[-1]["role"] == msg["role"]:
            prev = converted[-1]
            # Merge: both must be plain strings to concatenate
            if isinstance(prev["content"], str) and isinstance(
                msg["content"], str
            ):
                prev["content"] = prev["content"] + "\n" + msg["content"]
            # If either is a list (vision), keep the last one
            else:
                converted[-1] = msg
        else:
            converted.append(msg)

    # Anthropic requires the last message to be from the user
    if converted and converted[-1]["role"] != "user":
        converted.append({"role": "user", "content": "Please continue."})

    # Must have at least one message
    if not converted:
        converted = [{"role": "user", "content": "Hello"}]

    system_text = "\n\n".join(system_parts)
    return system_text, converted


# ---------------------------------------------------------------------------
# Tool format converter  (OpenAI → Anthropic)
# ---------------------------------------------------------------------------
def _convert_tools_to_anthropic(
    tools: Optional[List[Dict[str, Any]]]
) -> Optional[List[Dict[str, Any]]]:
    """Convert OpenAI function-calling tool format to Anthropic tool format.

    OpenAI format:
    {
        "type": "function",
        "function": {
            "name": "...",
            "description": "...",
            "parameters": {...}  # JSON schema
        }
    }

    Anthropic format:
    {
        "name": "...",
        "description": "...",
        "input_schema": {...}  # JSON schema
    }
    """
    if not tools:
        return None

    converted = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func = tool.get("function", {})
        anthropic_tool = {
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
        }
        converted.append(anthropic_tool)

    return converted if converted else None


# ---------------------------------------------------------------------------
# AnthropicService
# ---------------------------------------------------------------------------
class AnthropicService:
    """Streams Claude responses with per-user rate limiting.

    The Anthropic API key is loaded from settings.ANTHROPIC_API_KEY
    (ANTHROPIC_API_KEY env var). It is stored only in this object's memory
    and is never logged, echoed, or returned to any client.
    """

    def __init__(self) -> None:
        if not _ANTHROPIC_AVAILABLE:
            raise RuntimeError(
                "anthropic package is not installed. "
                "Run: pip install 'anthropic>=0.85.0'"
            )
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to your env/.env.local (dev) or Key Vault (prod)."
            )
        # Key lives only here — never serialised or logged
        self._client = _anthropic_lib.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
        )
        self._rate_limiter = _AnthropicRateLimiter(
            rpm=settings.ANTHROPIC_RPM_LIMIT,
        )
        logger.info(
            "AnthropicService initialised (rpm_limit=%d, "
            "sonnet_max=%d, haiku_max=%d)",
            settings.ANTHROPIC_RPM_LIMIT,
            settings.ANTHROPIC_MAX_TOKENS_SONNET,
            settings.ANTHROPIC_MAX_TOKENS_HAIKU,
        )

    def get_remaining(self, user_id: str) -> int:
        """Return how many Claude requests this user has left in the window."""
        return self._rate_limiter.remaining(user_id)

    async def stream_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        user_id: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream a Claude response with optional tool calling.

        Yields StreamChunk objects compatible with the rest of the chat
        pipeline. Rate-limits per user; emits a preview notice before the
        first content token.

        If tools are provided and the model uses them, yields tool_call chunks
        in the same format as the OpenAI service.
        """
        if model not in ANTHROPIC_MODELS:
            yield StreamChunk(
                type="error",
                content=f"Unknown Claude model: {model}. "
                        f"Available: {', '.join(ANTHROPIC_MODELS)}",
            )
            return

        allowed, remaining = self._rate_limiter.check_and_consume(user_id)
        if not allowed:
            yield StreamChunk(
                type="error",
                content=(
                    "**Rate limit reached:** Claude models allow "
                    f"{settings.ANTHROPIC_RPM_LIMIT} requests per minute. "
                    "Please wait ~60 seconds or switch to a standard model."
                ),
            )
            return

        cfg = ANTHROPIC_MODELS[model]
        cap = cfg["max_tokens"]
        safe_max = min(max_tokens or cap, cap)

        system_text, converted_msgs = _convert_messages(messages)
        anthropic_tools = _convert_tools_to_anthropic(tools) if cfg.get("supports_tools") else None

        try:
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

            # Build API call kwargs
            api_kwargs = {
                "model": cfg["model_id"],
                "max_tokens": safe_max,
                "system": system_text or _anthropic_lib.NOT_GIVEN,
                "messages": converted_msgs,
                "temperature": temperature,
                "stream": True,
            }
            if anthropic_tools:
                api_kwargs["tools"] = anthropic_tools

            raw_stream = await self._client.messages.create(**api_kwargs)

            # Track tool use blocks for streaming
            current_tool_use: Optional[Dict[str, Any]] = None
            tool_use_json_buffer = ""

            async for event in raw_stream:
                event_type = getattr(event, "type", None)

                if event_type == "content_block_start":
                    # Check if this is a tool_use block
                    content_block = getattr(event, "content_block", None)
                    if content_block:
                        block_type = getattr(content_block, "type", None)
                        if block_type == "tool_use":
                            # Starting a new tool call
                            current_tool_use = {
                                "id": getattr(content_block, "id", ""),
                                "name": getattr(content_block, "name", ""),
                                "arguments": {},
                            }
                            tool_use_json_buffer = ""

                elif event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta:
                        delta_type = getattr(delta, "type", None)
                        if delta_type == "text_delta":
                            yield StreamChunk(
                                type="content", content=delta.text
                            )
                        elif delta_type == "input_json_delta":
                            # Tool call arguments are streamed as JSON
                            partial_json = getattr(delta, "partial_json", "")
                            if partial_json:
                                tool_use_json_buffer += partial_json

                elif event_type == "content_block_stop":
                    # If we were building a tool call, emit it now
                    if current_tool_use and tool_use_json_buffer:
                        import json as _json
                        try:
                            current_tool_use["arguments"] = _json.loads(tool_use_json_buffer)
                        except _json.JSONDecodeError:
                            current_tool_use["arguments"] = {"raw": tool_use_json_buffer}

                        yield StreamChunk(
                            type="tool_call",
                            data=current_tool_use,
                        )
                        current_tool_use = None
                        tool_use_json_buffer = ""

                elif event_type == "message_delta":
                    # Capture token usage
                    delta_usage = getattr(event, "usage", None)
                    if delta_usage:
                        out = getattr(delta_usage, "output_tokens", 0) or 0
                        usage["completion_tokens"] = out
                        usage["total_tokens"] = (
                            usage["prompt_tokens"] + out
                        )
                elif event_type == "message_start":
                    msg = getattr(event, "message", None)
                    if msg:
                        msg_usage = getattr(msg, "usage", None)
                        if msg_usage:
                            inp = getattr(msg_usage, "input_tokens", 0) or 0
                            usage["prompt_tokens"] = inp
                            usage["total_tokens"] = (
                                inp + usage["completion_tokens"]
                            )

        except _anthropic_lib.RateLimitError as exc:
            logger.warning("Anthropic API rate limit [%s]: %s", model, exc)
            yield StreamChunk(
                type="error",
                content=(
                    "Claude API rate limit reached. "
                    "Please try again in a moment."
                ),
            )
            return
        except _anthropic_lib.AuthenticationError as exc:
            logger.error(
                "Anthropic authentication failed [%s]: %s — "
                "check ANTHROPIC_API_KEY",
                model, exc,
            )
            yield StreamChunk(
                type="error",
                content="Claude authentication failed. "
                        "Check ANTHROPIC_API_KEY configuration.",
            )
            return
        except _anthropic_lib.BadRequestError as exc:
            logger.error(
                "Anthropic bad request [%s]: %s", model, exc, exc_info=True
            )
            yield StreamChunk(
                type="error",
                content=(
                    "Claude rejected the request (invalid message format). "
                    "Please start a new conversation."
                ),
            )
            return
        except Exception as exc:
            logger.error(
                "Anthropic stream error [%s]: %s",
                model, exc, exc_info=True,
            )
            yield StreamChunk(
                type="error",
                content=(
                    f"Claude encountered an error: {type(exc).__name__}. "
                    "Please try again."
                ),
            )
            return

        yield StreamChunk(
            type="done",
            data={"finish_reason": "stop", **usage},
        )


# ---------------------------------------------------------------------------
# Singleton — initialised lazily so missing key only raises on first use
# ---------------------------------------------------------------------------
try:
    anthropic_service: Optional[AnthropicService] = (
        AnthropicService() if settings.ANTHROPIC_ENABLED else None
    )
except RuntimeError as _e:
    logger.warning("AnthropicService not available: %s", _e)
    anthropic_service = None
