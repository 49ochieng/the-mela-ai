"""
Mela AI - MCP tool catalogue.

Single source of truth for the tools Mela exposes over its own MCP
server (Phase 6A) and advertises via the public
``/api/v1/orchestration/capabilities`` endpoint (Phase 6C).

Tool names are stable identifiers — never rename.  Argument shapes
follow OpenAI tool-function format so callers can register Mela's
tools alongside built-in tools without translation.
"""

from __future__ import annotations

from typing import Any


# Tool names are also the scope identifiers stored on
# ``mcp_clients.scopes``.  Keep the list as a frozenset for O(1)
# lookups during scope checks.
MELA_TOOL_NAMES: frozenset[str] = frozenset({
    "mela_chat",
    "mela_search_knowledge",
    "mela_get_worker_status",
    "mela_trigger_plan",
    "mela_get_trace_status",
    "mela_ingest_context",
})


# Wildcard scope — equivalent to "every tool registered today AND
# every tool added later".  Use sparingly; prefer explicit lists.
SCOPE_WILDCARD = "*"


MELA_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "mela_chat",
            "description": (
                "Send a message to Mela and get a response.  Mela uses its full "
                "knowledge base, RAG retrieval, and worker orchestration to "
                "answer.  This is the highest-level entry point — use it when "
                "you want Mela to do whatever Mela would normally do for a "
                "user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "conversation_id": {"type": "string"},
                    "profile_mode": {
                        "type": "string",
                        "enum": ["personal", "work"],
                        "default": "personal",
                    },
                    "tenant_id": {"type": "string"},
                    "user_id": {"type": "string"},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mela_search_knowledge",
            "description": (
                "Query Mela's Knowledge Base directly.  Returns short summaries "
                "of past worker results matching the query, scoped to the "
                "tenant.  Use when you want Mela's accumulated cross-worker "
                "knowledge without going through chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "tenant_id": {"type": "string"},
                    "user_id": {"type": "string"},
                    "entry_types": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 5,
                    },
                },
                "required": ["query", "tenant_id", "user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mela_get_worker_status",
            "description": (
                "Return the cross-worker health snapshot — same shape as "
                "/api/v1/orchestration/health.  Use when you want to know "
                "what Mela's workers are doing before issuing a plan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "worker_id": {
                        "type": "string",
                        "description": "Optional filter; omit for all workers.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mela_trigger_plan",
            "description": (
                "Ask Mela to plan and execute a goal across its workers.  In "
                "background mode, returns the trace_id immediately.  In sync "
                "mode, waits up to 30 seconds for completion before returning "
                "partial results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "user_id": {"type": "string"},
                    "tenant_id": {"type": "string"},
                    "execution_mode": {
                        "type": "string",
                        "enum": ["sync", "background"],
                        "default": "background",
                    },
                },
                "required": ["goal", "user_id", "tenant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mela_get_trace_status",
            "description": (
                "Poll the status of an orchestration trace previously created "
                "by mela_trigger_plan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string"},
                },
                "required": ["trace_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mela_ingest_context",
            "description": (
                "Push context into Mela's Knowledge Base.  Use when an "
                "external app (e.g. Meeting Assistant) wants to teach Mela "
                "something without going through the worker ingest API.  "
                "The MCP client's ``client_name`` is recorded as the source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "entry_type": {
                        "type": "string",
                        "enum": [
                            "task_summary",
                            "meeting_summary",
                            "goal_result",
                            "worker_event",
                            "user_context",
                        ],
                    },
                    "tenant_id": {"type": "string"},
                    "user_id": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "data_pointer": {"type": "string"},
                },
                "required": [
                    "title", "summary", "entry_type",
                    "tenant_id", "user_id",
                ],
            },
        },
    },
]


def is_tool_in_scope(tool_name: str, scopes: list[str] | None) -> bool:
    """True if ``tool_name`` is permitted by ``scopes``.

    Empty / missing scopes → no access (the safer default for a
    misconfigured client).  ``["*"]`` → all tools.  Otherwise an
    explicit name match.
    """
    if not scopes:
        return False
    if SCOPE_WILDCARD in scopes:
        return True
    return tool_name in scopes
