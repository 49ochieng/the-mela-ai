"""
Mela AI - Unified Model Router

Routes requests across ALL configured AI providers with silent cross-provider
failover. If a provider fails BEFORE producing any content, the router
transparently switches to the next provider. The user never sees a provider
error — they see a response.

Priority chain (configurable per-request):
  1. Requested model's provider (Claude → Anthropic, gemini-* → Gemini, else → Azure OpenAI)
  2. Azure OpenAI backbone with its own internal model fallback chain
  3. Anthropic Claude Haiku (fast, cheap — only if not primary provider)
  4. Google Gemini Flash (only if configured and not primary provider)

Internal chunk:
  StreamChunk(type="router_resolved", data={"provider": str, "model": str})
  Emitted once before the first content chunk. Consumed by chat_service to
  track which provider/model actually answered. Never forwarded to the client.
"""

import logging
from typing import AsyncGenerator, List, Dict, Any, Optional, Tuple

from app.schemas.chat import StreamChunk
from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Model cost tiers (for budget-based auto-downgrade) ──────────────────────
# Tier 2 = expensive, Tier 1 = mid, Tier 0 = cheap/free
MODEL_COST_TIER: Dict[str, int] = {
    "claude-opus-4-6":      3,
    "claude-sonnet-4-6":    2,
    "gpt-5.2-chat":         2,
    "gpt-4.1":              1,
    "gpt-4o":               1,
    "kimi-k2.5":            1,
    "mistral-large-3":      1,
    "grok-3-mini":          0,
    "claude-haiku-4-5":     0,
    "gemini-2.0-flash":     0,
    "gpt-4o-mini":          0,
    "llama-4-maverick":     0,
}

# Downgrade targets per tier, keyed by (current_tier, budget_pct_threshold)
_DOWNGRADE_MAP = [
    # (min_usage_pct, max_tier_allowed, fallback_model)
    (90, 0, "gpt-4o-mini"),   # ≥90% budget → force cheapest
    (70, 1, "gpt-4.1"),       # ≥70% budget → cap at tier-1
]


def budget_downgrade_model(model: str, usage_pct: int) -> str:
    """Return a cheaper model if the user is close to their budget limit.

    At ≥90% usage: downgrade anything above tier-0 to gpt-4o-mini.
    At ≥70% usage: downgrade tier-2+ models to gpt-4.1.
    Otherwise: return the original model unchanged.
    """
    current_tier = MODEL_COST_TIER.get(model, 1)
    for threshold, max_tier, fallback in _DOWNGRADE_MAP:
        if usage_pct >= threshold and current_tier > max_tier:
            return fallback
    return model


def _provider_for_model(model: str) -> str:
    """Return the primary provider string for a model ID."""
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "gemini"
    return "openai"


class ModelRouter:
    """
    Unified cross-provider router with silent failover.

    Call ``stream()`` in place of a specific service's ``stream_completion()``.
    It tries providers in priority order and never propagates a provider error
    to the caller — it silently falls back until one succeeds.
    """

    # ── Provider sequence builder ────────────────────────────────────────────

    def _build_sequence(self, model: str) -> List[Tuple[str, str]]:
        """Return ordered (provider, model) pairs to try for this request."""
        primary = _provider_for_model(model)
        sequence: List[Tuple[str, str]] = [(primary, model)]

        # Azure OpenAI backbone — its own stream_completion handles internal
        # GPT fallback chain, so we only need one entry here.
        if primary != "openai":
            sequence.append(("openai", "gpt-4.1"))

        # Anthropic as secondary cross-provider fallback
        if primary != "anthropic" and getattr(settings, "ANTHROPIC_ENABLED", False):
            sequence.append(("anthropic", "claude-haiku-4-5"))

        # Gemini as tertiary fallback
        if primary != "gemini" and getattr(settings, "GOOGLE_AI_API_KEY", None):
            sequence.append(("gemini", "gemini-2.0-flash"))

        return sequence

    # ── Per-provider delegation ──────────────────────────────────────────────

    async def _call_provider(
        self,
        provider: str,
        model: str,
        messages: List[Dict[str, Any]],
        user_id: str,
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Delegate to the correct service and yield its StreamChunks."""

        if provider == "openai":
            from app.services.openai_service import openai_service
            if not openai_service:
                logger.warning("[router] OpenAI service not configured — skipping")
                yield StreamChunk(type="error", content="_provider_unavailable")
                return
            async for chunk in openai_service.stream_completion(
                messages, model=model, tools=tools, temperature=temperature
            ):
                yield chunk

        elif provider == "anthropic":
            from app.services.anthropic_service import anthropic_service
            if not anthropic_service:
                logger.warning("[router] Anthropic service not configured — skipping")
                yield StreamChunk(type="error", content="_provider_unavailable")
                return
            async for chunk in anthropic_service.stream_completion(
                messages, model=model, user_id=user_id,
                tools=tools, temperature=temperature,
            ):
                yield chunk

        elif provider == "gemini":
            try:
                from app.services.gemini_service import gemini_service
            except ImportError:
                logger.warning("[router] Gemini package not installed — skipping")
                yield StreamChunk(type="error", content="_provider_unavailable")
                return
            if not gemini_service:
                logger.warning("[router] Gemini service not configured — skipping")
                yield StreamChunk(type="error", content="_provider_unavailable")
                return
            async for chunk in gemini_service.stream_completion(
                messages, model=model, user_id=user_id
            ):
                yield chunk

        else:
            logger.warning("[router] Unknown provider: %s", provider)
            yield StreamChunk(type="error", content="_provider_unavailable")

    # ── Main entry point ─────────────────────────────────────────────────────

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        user_id: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream a response with silent cross-provider failover.

        Yields a ``router_resolved`` chunk FIRST (before any content) so the
        caller can track which provider/model won. This chunk must be consumed
        internally — never forwarded to the client.

        If a provider errors BEFORE producing any content, the router silently
        moves to the next (provider, model) in the sequence. If a provider
        errors AFTER content has started streaming, the error is surfaced (the
        stream is already committed at that point).

        If every provider fails, the router yields a user-friendly message
        rather than an error chunk.
        """
        sequence = self._build_sequence(model)
        last_error: Optional[str] = None

        for provider, try_model in sequence:
            content_started = False
            pre_content_buffer: List[StreamChunk] = []
            resolved = False
            failed = False

            try:
                async for chunk in self._call_provider(
                    provider, try_model, messages, user_id, tools, temperature
                ):
                    if chunk.type == "error":
                        if not content_started:
                            # Error before any content — try next provider silently
                            last_error = chunk.content
                            logger.warning(
                                "[router] %s/%s error before content, will try fallback: %.120s",
                                provider, try_model, chunk.content,
                            )
                            failed = True
                            break
                        else:
                            # Error after content started — can't cleanly recover mid-stream.
                            # Surface a user-safe message; never expose raw provider errors.
                            _raw = chunk.content or ""
                            if _raw.startswith("_provider_unavailable") or not _raw:
                                pass  # swallow — no content reached user yet
                            else:
                                yield StreamChunk(
                                    type="content",
                                    content=(
                                        "\n\nI encountered a temporary issue completing "
                                        "this response. Please try again."
                                    ),
                                )
                            return

                    # Track when real content starts
                    if chunk.type == "content" and chunk.content:
                        content_started = True

                    if not content_started:
                        # Hold pre-content chunks (model_switched, thinking, etc.)
                        # until we're confident this provider will deliver content.
                        pre_content_buffer.append(chunk)
                    else:
                        # First content token — flush the held chunks
                        if pre_content_buffer:
                            yield StreamChunk(
                                type="router_resolved",
                                data={"provider": provider, "model": try_model},
                            )
                            for buffered in pre_content_buffer:
                                yield buffered
                            pre_content_buffer.clear()
                        yield chunk

                    if chunk.type == "done":
                        resolved = True
                        break

            except Exception as exc:
                if not content_started:
                    last_error = str(exc)
                    logger.warning(
                        "[router] %s/%s exception before content, trying fallback: %s",
                        provider, try_model, exc,
                    )
                    failed = True
                else:
                    logger.error(
                        "[router] %s/%s mid-stream exception (cannot recover): %s",
                        provider, try_model, exc,
                    )
                    return

            if failed:
                # Error before content — try next provider
                continue

            # Provider finished (possibly with only pre-content chunks — e.g. tool calls)
            if pre_content_buffer:
                yield StreamChunk(
                    type="router_resolved",
                    data={"provider": provider, "model": try_model},
                )
                for buffered in pre_content_buffer:
                    yield buffered

            if resolved or pre_content_buffer:
                return

        # ── All providers exhausted ──────────────────────────────────────────
        logger.error(
            "[router] All providers exhausted for model=%s. Last error: %s",
            model, last_error,
        )
        yield StreamChunk(
            type="router_resolved",
            data={"provider": "fallback", "model": model},
        )
        yield StreamChunk(
            type="content",
            content=(
                "I'm currently experiencing high demand. "
                "Your request will be ready in a moment — please try again."
            ),
        )
        yield StreamChunk(type="done", data={"finish_reason": "all_providers_failed"})


# Singleton
model_router = ModelRouter()
