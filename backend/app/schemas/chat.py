"""
Mela AI - Chat Schemas
"""

from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ModelOption(str, Enum):
    GPT4O = "gpt-4o"
    GPT4O_MINI = "gpt-4o-mini"
    GPT4_VISION = "gpt-4-vision"


class Attachment(BaseModel):
    """File attachment."""
    id: str
    filename: str
    file_type: str
    file_size: int
    blob_url: Optional[str] = None


class Citation(BaseModel):
    """Document citation."""
    document_id: str
    document_title: str
    chunk_id: str
    content: str
    relevance_score: float
    source_url: Optional[str] = None
    # Rich provenance for user trust
    author: Optional[str] = None
    last_modified: Optional[str] = None
    file_path: Optional[str] = None
    source_type: Optional[str] = None        # "sharepoint" | "onedrive" | "web" | "indexed"
    confidence_level: Optional[str] = None  # "high" | "medium" | "low"
    site_name: Optional[str] = None
    section: Optional[str] = None


class ToolCall(BaseModel):
    """Tool call request."""
    id: str
    name: str
    arguments: Dict[str, Any]


class ToolResult(BaseModel):
    """Tool call result."""
    tool_call_id: str
    name: str
    result: Any
    success: bool
    error: Optional[str] = None


class ChatMessage(BaseModel):
    """Chat message."""
    role: MessageRole
    content: str
    attachments: Optional[List[Attachment]] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_results: Optional[List[ToolResult]] = None
    citations: Optional[List[Citation]] = None
    created_at: Optional[str] = None


class ChatRequest(BaseModel):
    """Chat request from client."""
    message: str
    conversation_id: Optional[str] = None
    model: Optional[str] = "gpt-5.2-chat"
    attachments: Optional[List[str]] = None  # Document IDs (for RAG)
    inline_attachments: Optional[List["InlineAttachment"]] = None  # Real-time file contents
    use_rag: bool = True
    use_web_search: bool = False
    stream: bool = True
    system_prompt: Optional[str] = None
    is_private: bool = False
    project_id: Optional[str] = None
    context_type: str = "personal"  # 'org' | 'personal' — used to tag new conversations


class InlineAttachment(BaseModel):
    """A file attachment whose content is sent inline with the chat request."""
    filename: str
    content_type: str          # e.g. "image/png", "application/pdf", "text/plain"
    # One of the following will be populated depending on file type:
    text_content: Optional[str] = None   # Extracted text for docs / plain-text files
    base64_data: Optional[str] = None    # Base64-encoded data URI for images
    ocr_text: Optional[str] = None       # OCR-extracted text from images if requested
    raw_base64: Optional[str] = None     # Raw base64 bytes for spreadsheets/CSV (code interpreter)


class ChatResponse(BaseModel):
    """Chat response to client."""
    conversation_id: str
    message_id: str
    content: str
    model: str
    citations: Optional[List[Citation]] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_results: Optional[List[ToolResult]] = None
    tokens_used: int
    created_at: datetime


class ErrorCode(str, Enum):
    """Stable, user-actionable error codes for streaming chat errors.

    The frontend maps these to localized, friendly messages. Backend code MUST
    pick the most specific code; never fall back to UNKNOWN unless truly
    uncategorized. New codes require a frontend mapping update.
    """
    # Upstream LLM provider failures
    LLM_TIMEOUT          = "llm_timeout"
    LLM_RATE_LIMITED     = "llm_rate_limited"
    LLM_PROVIDER_DOWN    = "llm_provider_down"
    LLM_CONTENT_FILTERED = "llm_content_filtered"
    # Auth / authorization
    AUTH_EXPIRED         = "auth_expired"
    AUTH_FORBIDDEN       = "auth_forbidden"
    # Tooling
    TOOL_FAILED          = "tool_failed"
    TOOL_TIMEOUT         = "tool_timeout"
    # Search / RAG
    SEARCH_UNAVAILABLE   = "search_unavailable"
    # Storage / DB
    DB_UNAVAILABLE       = "db_unavailable"
    # Quota / budgets
    BUDGET_EXCEEDED      = "budget_exceeded"
    QUOTA_EXCEEDED       = "quota_exceeded"
    # User input issues
    INPUT_TOO_LARGE      = "input_too_large"
    INPUT_INVALID        = "input_invalid"
    # Catch-all (avoid)
    UNKNOWN              = "unknown"


class StreamChunk(BaseModel):
    """Streaming response chunk."""
    type: Literal["content", "thinking", "citation", "tool_call", "tool_result", "tool_executing", "image_generated", "file_generated", "model_switched", "router_resolved", "budget_warning", "budget_exceeded", "error", "done", "claude_usage", "claude_limit_reached", "worker_event", "heartbeat", "ping", "injection_detected", "confirmation_required"]
    content: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    # Phase 0: machine-readable error code for the frontend to map to a
    # friendly, actionable message. Only populated when type == "error".
    error_code: Optional[ErrorCode] = None
    # Correlation ID for support — included on error chunks so users can
    # quote a reference and we can trace the failure end-to-end.
    correlation_id: Optional[str] = None


# Phase 5A: enum + payload for the per-user worker-event SSE channel.
# Distinct from StreamChunk because the event-bus stream is its own
# endpoint (not the chat stream); typed so the bus has structured
# payloads instead of opaque dicts.
class WorkerEventType(str, Enum):
    SCAN_COMPLETED      = "scan_completed"
    MEETING_ENDED       = "meeting_ended"
    TASK_UPDATED        = "task_updated"
    WORKER_AVAILABLE    = "worker_available"
    WORKER_UNAVAILABLE  = "worker_unavailable"


class WorkerEventChunk(BaseModel):
    """Server-sent event payload for the orchestration event bus.

    Wire-format note: the SSE generator wraps this in an outer
    ``StreamChunk(type="worker_event", data=<this>)`` so the frontend's
    existing chunk parser handles it uniformly.  Always serialised with
    ``model_dump(mode="json")`` so the timestamp is ISO-8601.
    """
    worker_id: str
    event_type: WorkerEventType
    title: str
    summary: str
    trace_id: Optional[str] = None
    timestamp: datetime = None  # type: ignore[assignment]

    def __init__(self, **data: Any) -> None:
        if data.get("timestamp") is None:
            data["timestamp"] = datetime.utcnow()
        super().__init__(**data)


class ConversationCreate(BaseModel):
    """Create conversation request."""
    title: Optional[str] = "New Conversation"
    model: Optional[str] = "gpt-5.2-chat"
    system_prompt: Optional[str] = None
    is_private: bool = False
    project_id: Optional[str] = None
    context_type: str = "personal"  # legacy alias; 'org' maps to 'work'
    tenant_id: Optional[str] = None  # required for work profile, None for personal
    workspace_id: Optional[str] = None


class ConversationUpdate(BaseModel):
    """Update conversation request."""
    title: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    is_archived: Optional[bool] = None


class ConversationResponse(BaseModel):
    """Conversation response."""
    id: str
    title: str
    model: str
    system_prompt: Optional[str] = None
    is_archived: bool
    is_private: bool = False
    private_expires_at: Optional[datetime] = None
    project_id: Optional[str] = None
    context_type: str = "personal"
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConversationDetail(ConversationResponse):
    """Conversation with messages."""
    messages: List[ChatMessage] = []


class MessageResponse(BaseModel):
    """Message response."""
    id: str
    conversation_id: str
    role: str
    content: str
    tokens_used: int
    model: Optional[str] = None
    citations: Optional[List[Citation]] = None
    attachments: Optional[List[Attachment]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ModelInfo(BaseModel):
    """Model information."""
    id: str
    name: str
    description: str
    max_tokens: int
    supports_vision: bool = False
    supports_tools: bool = True
    is_default: bool = False
    preview: bool = False  # True = rate-limited preview (e.g. Claude)


class ModelInsight(BaseModel):
    """Enriched model card shown on the chat welcome screen."""
    id: str
    name: str
    provider: str                     # azure_openai | azure_ai_foundry | anthropic
    description: str
    cost_per_1k_tokens: float         # USD, from governance table
    performance_label: str            # e.g. "Best for reasoning", "Fast & efficient"
    supports_vision: bool
    supports_tools: bool
    is_default: bool
    preview: bool
    badge: Optional[str] = None       # "Popular" | "Fastest" | "Best Value" | None
    usage_rank: int = 0               # requests in last 7 days (for Popular badge)


# Resolve forward references after all models are defined
ChatRequest.model_rebuild()
