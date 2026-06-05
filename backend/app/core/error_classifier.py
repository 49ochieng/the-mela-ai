"""Phase 0 — classify exceptions into stable ErrorCodes for the chat stream.

Single source of truth so all error sites pick consistent codes.  The frontend
maps these codes to localized, friendly, actionable messages.

Usage:
    from app.core.error_classifier import classify_chat_error
    code, friendly = classify_chat_error(exc, corr_id)
    yield StreamChunk(type="error", error_code=code,
                      content=friendly, correlation_id=corr_id)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Tuple

from app.schemas.chat import ErrorCode

logger = logging.getLogger(__name__)


def _msg(code: ErrorCode, corr_id: str) -> str:
    """Friendly, actionable user-facing message keyed by code.

    Frontend has its own mapping; this server-side default is the fallback
    when the frontend is older or running outside our app shell.
    """
    base = {
        ErrorCode.LLM_TIMEOUT:          "The AI took too long to answer. Try again, or shorten your message.",
        ErrorCode.LLM_RATE_LIMITED:     "We're hitting our AI provider's rate limit. Please wait a few seconds and try again.",
        ErrorCode.LLM_PROVIDER_DOWN:    "Our AI provider is having an outage. We've logged this and will retry automatically.",
        ErrorCode.LLM_CONTENT_FILTERED: "The AI declined to answer due to content safety filters. Try rephrasing.",
        ErrorCode.AUTH_EXPIRED:         "Your session expired. Please sign in again.",
        ErrorCode.AUTH_FORBIDDEN:       "You don't have permission to perform this action.",
        ErrorCode.TOOL_FAILED:          "A tool I tried to use failed. Your message was saved — please try again.",
        ErrorCode.TOOL_TIMEOUT:         "A tool I tried to use timed out. Try again, or ask me to skip that step.",
        ErrorCode.SEARCH_UNAVAILABLE:   "I couldn't reach the document index. Answer may be incomplete — please retry.",
        ErrorCode.DB_UNAVAILABLE:       "Our database is temporarily unavailable. Your message was not saved — please retry.",
        ErrorCode.BUDGET_EXCEEDED:      "You've exceeded the budget for this conversation. Start a new chat or wait for the budget to reset.",
        ErrorCode.QUOTA_EXCEEDED:       "You've hit your daily quota. It resets at midnight UTC.",
        ErrorCode.INPUT_TOO_LARGE:      "Your message (or attachments) is too large. Please shorten it or remove some attachments.",
        ErrorCode.INPUT_INVALID:        "Your request was malformed. Please try again.",
        ErrorCode.UNKNOWN:              "Something went wrong on our side. Your message was saved — please try again.",
    }
    return f"{base[code]} (ref: {corr_id})"


def classify_chat_error(exc: BaseException, corr_id: str) -> Tuple[ErrorCode, str]:
    """Map a raised exception to (ErrorCode, friendly message).

    Heuristic order matters: more specific checks come first.  We avoid
    importing optional providers' exception classes so this stays import-safe
    when those packages aren't installed.
    """
    name = type(exc).__name__
    msg = str(exc)
    msg_l = msg.lower()
    qualname = f"{type(exc).__module__}.{name}"

    # ── Async / network timeouts ──────────────────────────────────────────
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in name.lower():
        # Distinguish tool timeouts from LLM timeouts when the message hints
        if "tool" in msg_l:
            return ErrorCode.TOOL_TIMEOUT, _msg(ErrorCode.TOOL_TIMEOUT, corr_id)
        return ErrorCode.LLM_TIMEOUT, _msg(ErrorCode.LLM_TIMEOUT, corr_id)

    # ── HTTP-shaped errors (httpx, openai, anthropic all expose .status_code
    # or wrap their own classes named *RateLimit*, *NotFound* etc.) ───────
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if "RateLimit" in name or status == 429:
        return ErrorCode.LLM_RATE_LIMITED, _msg(ErrorCode.LLM_RATE_LIMITED, corr_id)
    if "ContentFilter" in name or "content_filter" in msg_l or "responsible ai" in msg_l:
        return ErrorCode.LLM_CONTENT_FILTERED, _msg(ErrorCode.LLM_CONTENT_FILTERED, corr_id)
    if status in (502, 503, 504) or "BadGateway" in name or "ServiceUnavailable" in name:
        return ErrorCode.LLM_PROVIDER_DOWN, _msg(ErrorCode.LLM_PROVIDER_DOWN, corr_id)
    if status == 401 or "Unauthorized" in name or "InvalidAuth" in name:
        return ErrorCode.AUTH_EXPIRED, _msg(ErrorCode.AUTH_EXPIRED, corr_id)
    if status == 403 or "Forbidden" in name:
        return ErrorCode.AUTH_FORBIDDEN, _msg(ErrorCode.AUTH_FORBIDDEN, corr_id)
    if status == 413 or "too large" in msg_l or "context length" in msg_l or "max tokens" in msg_l:
        return ErrorCode.INPUT_TOO_LARGE, _msg(ErrorCode.INPUT_TOO_LARGE, corr_id)

    # ── DB / SQL ──────────────────────────────────────────────────────────
    if any(t in qualname for t in ("sqlalchemy", "asyncpg", "pyodbc", "aiosqlite")):
        return ErrorCode.DB_UNAVAILABLE, _msg(ErrorCode.DB_UNAVAILABLE, corr_id)

    # ── Azure AI Search ───────────────────────────────────────────────────
    if "azure.search" in qualname or "SearchClient" in name or "search index" in msg_l:
        return ErrorCode.SEARCH_UNAVAILABLE, _msg(ErrorCode.SEARCH_UNAVAILABLE, corr_id)

    # ── Budget / quota (raised by our own code) ───────────────────────────
    if "budget" in msg_l and "exceed" in msg_l:
        return ErrorCode.BUDGET_EXCEEDED, _msg(ErrorCode.BUDGET_EXCEEDED, corr_id)
    if "quota" in msg_l:
        return ErrorCode.QUOTA_EXCEEDED, _msg(ErrorCode.QUOTA_EXCEEDED, corr_id)

    # ── Validation ────────────────────────────────────────────────────────
    if "ValidationError" in name or "ValueError" == name:
        return ErrorCode.INPUT_INVALID, _msg(ErrorCode.INPUT_INVALID, corr_id)

    # ── Tool sentinel raised by tool_executor ─────────────────────────────
    if "Tool" in name and ("Failed" in name or "Error" in name):
        return ErrorCode.TOOL_FAILED, _msg(ErrorCode.TOOL_FAILED, corr_id)

    return ErrorCode.UNKNOWN, _msg(ErrorCode.UNKNOWN, corr_id)
