"""
Mela AI - Chat Endpoints
"""

import logging
import asyncio
from typing import List, Optional
from fastapi import (
    APIRouter, Depends, HTTPException, status,
    Request, UploadFile, File, Form,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import json
import base64
import mimetypes

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.profile_context import (
    get_optional_profile_context, ProfileContext,
)
from app.schemas.auth import UserInfo
from app.schemas.chat import (
    ChatRequest, ConversationCreate, ConversationUpdate,
    ConversationResponse, ConversationDetail, ModelInfo, ModelInsight,
)
from app.services.chat_service import chat_service
from app.services.outcome_orchestrator import outcome_orchestrator
import app.services.budget_service as budget_svc
import app.services.model_access_service as model_access_svc


class GenerateTitleRequest(BaseModel):
    """Request to generate a conversation title from the first message."""
    first_message: str


class GenerateTitleResponse(BaseModel):
    """Response containing the generated title."""
    title: str


logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/completions")
async def create_chat_completion(
    request: ChatRequest,
    http_request: Request,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    """
    Create a chat completion with optional streaming.
    Profile context is read from X-Profile-Mode / X-Tenant-Id headers.
    """
    access_token = getattr(http_request.state, "access_token", None)
    request._profile_context = profile_ctx
    # Locale: capture the user's IANA timezone (sent by the browser) so the
    # LLM gets accurate "now" context. Falls back to America/Chicago (CDT/CST).
    _tz = http_request.headers.get("x-user-timezone") or http_request.headers.get(
        "X-User-Timezone"
    )
    if _tz:
        request._user_timezone = _tz

    # Check budget before processing
    budget_status = await budget_svc.check_budget(
        db,
        user_id=current_user.id,
        tenant_id=getattr(current_user, "tenant_id", None),
    )
    if not budget_status.allowed and budget_status.hard_stop:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                budget_status.message
                or "Budget exceeded. Please contact your administrator."
            ),
        )

    # Check model access — skip for "auto" (orchestrator selects the model)
    if request.model and request.model not in ("auto", ""):
        roles = getattr(current_user, "roles", []) or []
        model_allowed = await model_access_svc.is_model_allowed(
            db, user_id=current_user.id, roles=roles, model_id=request.model
        )
        if not model_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"You do not have access to model '{request.model}'. "
                    "Contact your administrator."
                ),
            )

    if request.stream:
        async def generate():
            # Phase 0.6: SSE keepalive. App Service idles SSE at 230s and
            # iOS Safari closes inactive connections sooner. Race the upstream
            # chunk generator against a 20s timer; emit a ping chunk between
            # real chunks so the connection never goes silent.
            import asyncio as _asyncio
            try:
                upstream = outcome_orchestrator.run(
                    db, current_user, request,
                    access_token=access_token,
                ).__aiter__()
                _next_task: _asyncio.Task | None = None
                while True:
                    if _next_task is None:
                        _next_task = _asyncio.create_task(upstream.__anext__())
                    done, _ = await _asyncio.wait({_next_task}, timeout=20.0)
                    if not done:
                        # 20s elapsed without a chunk — send a keepalive ping.
                        yield 'data: {"type":"ping"}\n\n'
                        continue
                    try:
                        chunk = _next_task.result()
                    except StopAsyncIteration:
                        break
                    finally:
                        _next_task = None
                    yield f"data: {chunk.model_dump_json()}\n\n"
                yield "data: [DONE]\n\n"
            except GeneratorExit:
                # Client disconnected — do not yield, just stop cleanly
                return
            except Exception as exc:
                import traceback
                import uuid as _uuid_mod
                _corr = _uuid_mod.uuid4().hex[:12]
                logger.error(
                    "SSE stream error [corr=%s user=%s]: %s",
                    _corr,
                    getattr(current_user, "id", "unknown"),
                    exc,
                    exc_info=True,
                )
                try:
                    from app.models import ErrorLog
                    db.add(ErrorLog(
                        user_id=getattr(current_user, "id", None),
                        user_email=getattr(current_user, "email", None),
                        tenant_id=getattr(current_user, "tenant_id", None),
                        method="POST",
                        route="/api/v1/chat/completions",
                        status_code=500,
                        error_type=type(exc).__name__,
                        message=str(exc),
                        stack_trace=traceback.format_exc(),
                        severity="error",
                    ))
                    await db.commit()
                except Exception as _log_err:
                    logger.warning("Failed to persist SSE ErrorLog [corr=%s]: %s", _corr, _log_err)
                # Phase 0.1: classify so the frontend can render a friendly message.
                from app.core.error_classifier import classify_chat_error
                _code, _msg = classify_chat_error(exc, _corr)
                error_payload = json.dumps({
                    "type": "error",
                    "content": _msg,
                    "error_code": _code.value,
                    "correlation_id": _corr,
                })
                yield f"data: {error_payload}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        response_content = ""
        conversation_id = None
        message_id = None
        citations = []
        generated_files = []

        async for chunk in outcome_orchestrator.run(
            db, current_user, request, access_token=access_token
        ):
            if chunk.type == "content":
                response_content += chunk.content
            elif chunk.type == "citation":
                citations.append(chunk.data)
            elif chunk.type == "file_generated" and chunk.data:
                generated_files.append(chunk.data)
            elif chunk.type == "done" and chunk.data:
                conversation_id = chunk.data.get("conversation_id")
                message_id = chunk.data.get("message_id")

        result = {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "content": response_content,
            "citations": citations,
        }
        if generated_files:
            result["generated_files"] = generated_files
        return result


# ── Phase 3a (CR-3): user-confirmation token mint ───────────────────────────


class ToolConfirmRequest(BaseModel):
    """Body for ``POST /chat/tool-confirm``.

    Frontend calls this when the user clicks "Approve" on a
    ``confirmation_required`` SSE chunk. The arguments MUST be the exact
    payload the user reviewed — the gate hashes them and refuses any
    mismatch.
    """
    tool_call_id: str
    tool_name: str
    arguments: dict


class ToolConfirmResponse(BaseModel):
    token: str
    expires_in: int


@router.post("/tool-confirm", response_model=ToolConfirmResponse)
async def issue_tool_confirmation_token(
    body: ToolConfirmRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Mint a one-shot confirmation token for a dangerous tool call.

    Security model:
      * Token is bound to ``(user_id, tool_name, sha256(args))`` — cannot
        be replayed for a different payload.
      * Token is single-use and expires in 60 seconds.
      * Endpoint requires authenticated user (Entra/JWT).
      * Only the listed dangerous tools are accepted; other tools never
        need a token so requesting one is rejected to keep the surface
        minimal.
    """
    from app.agents.confirmation import DANGEROUS_TOOLS, issue_token, _TOKEN_TTL_SECONDS

    if body.tool_name not in DANGEROUS_TOOLS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tool does not require confirmation",
        )

    token = issue_token(
        user_id=current_user.id,
        tool_name=body.tool_name,
        arguments=body.arguments,
    )
    logger.info(
        "[security] Confirmation token issued user=%s tool=%s",
        current_user.id, body.tool_name,
    )
    return ToolConfirmResponse(token=token, expires_in=_TOKEN_TTL_SECONDS)


@router.get(
    "/conversations/shared-with-me",
    response_model=List[ConversationResponse],
)
async def list_shared_with_me(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    """Conversations explicitly shared with the current user."""
    return await chat_service.list_shared_with_me(db, current_user.id, profile_ctx=profile_ctx)


@router.get(
    "/conversations/shared-by-me",
    response_model=List[ConversationResponse],
)
async def list_shared_by_me(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    """Conversations owned by the current user that have been shared."""
    return await chat_service.list_shared_by_me(db, current_user.id, profile_ctx=profile_ctx)


@router.get("/conversations", response_model=List[ConversationResponse])
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    archived: bool = False,
    context_type: Optional[str] = None,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    """List user conversations scoped to the active profile namespace."""
    return await chat_service.list_conversations(
        db, current_user.id, limit, offset, archived,
        context_type=context_type,
        profile_context=profile_ctx,
    )


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    data: ConversationCreate,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    """Create a new conversation bound to the active profile namespace."""
    data.context_type = profile_ctx.profile_mode
    data.tenant_id = profile_ctx.db_tenant_id
    conversation = await chat_service.get_or_create_conversation(
        db, current_user, create_data=data
    )
    await db.commit()

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        model=conversation.model,
        system_prompt=conversation.system_prompt,
        is_archived=conversation.is_archived,
        is_private=getattr(conversation, "is_private", False),
        context_type=getattr(conversation, "profile_mode", "personal"),
        project_id=conversation.project_id,
        message_count=0,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def get_conversation(
    conversation_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    """Get a conversation with its messages."""
    result = await chat_service.get_conversation_detail(
        db, conversation_id, current_user.id
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    conversation, messages = result

    # Enforce profile namespace boundary — prevent personal-mode requests
    # from reading work conversations (and vice versa).
    if profile_ctx:
        profile_ctx.validate_record(conversation)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        model=conversation.model,
        system_prompt=conversation.system_prompt,
        is_archived=conversation.is_archived,
        message_count=len(messages),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            {
                "role": m.role,
                "content": m.content,
                "citations": m.citations,
                "attachments": m.attachments,
                "created_at": (
                    m.created_at.isoformat() if m.created_at else None
                ),
            }
            for m in messages
        ],
    )


@router.put(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
async def update_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a conversation."""
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    conversation = await chat_service.update_conversation_data(
        db, conversation_id, current_user.id, updates
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    messages = await chat_service.get_conversation_messages(
        db, conversation_id
    )
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        model=conversation.model,
        system_prompt=conversation.system_prompt,
        is_archived=conversation.is_archived,
        message_count=len(messages),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation."""
    success = await chat_service.delete_conversation(
        db, conversation_id, current_user.id
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return {"message": "Conversation deleted"}


@router.post(
    "/conversations/generate-title",
    response_model=GenerateTitleResponse
)
async def generate_title(
    request: GenerateTitleRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Generate a chat title from the first message using Claude Haiku."""
    from app.services.title_service import generate_chat_title

    title = await generate_chat_title(request.first_message)
    return GenerateTitleResponse(title=title)


_MODEL_DISPLAY_NAMES = {
    "gpt-5.2-chat":      "GPT-5.2",
    "gpt-4.1":           "GPT-4.1",
    "gpt-4o":            "GPT-4o",
    "kimi-k2.5":         "Kimi-K2.5",
    "mistral-large-3":   "Mistral Large 3",
    "grok-3-mini":       "Grok-3-mini",
    "llama-4-maverick":  "Llama 4 Maverick",
    "gemini-2.0-flash":  "Gemini 2.0 Flash",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-haiku-4-5":  "Claude Haiku 4.5",
}

# Static performance labels shown in the Model Insights panel
_MODEL_PERFORMANCE_LABELS: dict[str, str] = {
    "gpt-5.2-chat":      "Best for reasoning",
    "gpt-4.1":           "Balanced",
    "gpt-4o":            "Fast multimodal",
    "kimi-k2.5":         "Long context",
    "mistral-large-3":   "Multilingual",
    "grok-3-mini":       "Fast reasoning",
    "llama-4-maverick":  "Fast & efficient",
    "gemini-2.0-flash":  "Free tier · Google AI",
    "claude-opus-4-6":   "Premium reasoning",
    "claude-sonnet-4-6": "Best for writing",
    "claude-haiku-4-5":  "Lightning fast",
    "dall-e-3":          "Best for images",
}

_HIDDEN_MODELS = {"gpt-4o-mini"}


@router.get("/models", response_model=List[ModelInfo])
async def list_models(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List available AI models / deployments, filtered by user access."""
    from app.services.openai_service import openai_service
    try:
        from app.services.anthropic_service import ANTHROPIC_MODELS
    except Exception:
        ANTHROPIC_MODELS = {}
    try:
        from app.services.gemini_service import GEMINI_MODELS
    except Exception:
        GEMINI_MODELS = {}

    result: List[ModelInfo] = []

    if openai_service:
        result.extend(
            ModelInfo(
                id=model_id,
                name=_MODEL_DISPLAY_NAMES.get(model_id, model_id),
                description=info.get("description", ""),
                max_tokens=info.get("max_tokens", 128000),
                supports_vision=info.get("supports_vision", False),
                supports_tools=info.get("supports_tools", True),
                is_default=info.get("is_default", False),
                preview=False,
            )
            for model_id, info in openai_service.models.items()
            if model_id not in _HIDDEN_MODELS
        )

    if ANTHROPIC_MODELS:
        result.extend(
            ModelInfo(
                id=model_id,
                name=_MODEL_DISPLAY_NAMES.get(model_id, model_id),
                description=info.get("description", ""),
                max_tokens=info.get("max_tokens", 4096),
                supports_vision=info.get("supports_vision", False),
                supports_tools=info.get("supports_tools", False),
                is_default=False,
                preview=True,
            )
            for model_id, info in ANTHROPIC_MODELS.items()
        )

    if GEMINI_MODELS:
        result.extend(
            ModelInfo(
                id=model_id,
                name=_MODEL_DISPLAY_NAMES.get(model_id, model_id),
                description=info.get("description", ""),
                max_tokens=info.get("max_tokens", 4096),
                supports_vision=info.get("supports_vision", False),
                supports_tools=info.get("supports_tools", False),
                is_default=False,
                preview=False,
            )
            for model_id, info in GEMINI_MODELS.items()
        )

    # Filter models by user access rules
    roles = getattr(current_user, "roles", []) or []
    allowed_models = await model_access_svc.get_allowed_models(
        db, user_id=current_user.id, roles=roles
    )
    # If get_allowed_models returns models, filter to only allowed ones
    # If no rules exist, it returns all enabled models, so filter by that list
    if allowed_models:
        allowed_ids = {m.model_id for m in allowed_models}
        result = [m for m in result if m.id in allowed_ids]

    if result:
        return result

    return [
        ModelInfo(
            id="gpt-5.2-chat", name="GPT-5.2",
            description="GPT-5.2 – next-gen frontier model",
            max_tokens=128000, supports_vision=True, is_default=True,
        ),
        ModelInfo(
            id="gpt-4.1", name="GPT-4.1",
            description="GPT-4.1 – latest with vision",
            max_tokens=128000, supports_vision=True,
        ),
        ModelInfo(
            id="kimi-k2.5", name="Kimi-K2.5",
            description="Kimi-K2.5 – long-context reasoning",
            max_tokens=131072,
        ),
    ]


@router.get("/models/insights", response_model=List[ModelInsight])
async def list_model_insights(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return enriched model cards for the chat welcome screen.
    Merges governance data (cost, enabled flag) with the model registry.
    Computes live usage rank from ModelUsage so the 'Popular' badge reflects
    real 7-day activity.  Only enabled models are returned.
    """
    import datetime
    from sqlalchemy import select, func
    from app.models.models import ModelQuotaPolicy, ModelUsage
    from app.services.billing_service import (
        get_model_policies,
        DEFAULT_COST_RATES,
        DEFAULT_PROVIDERS,
    )
    from app.services.openai_service import openai_service

    try:
        from app.services.anthropic_service import ANTHROPIC_MODELS
    except Exception:
        ANTHROPIC_MODELS = {}
    try:
        from app.services.gemini_service import GEMINI_MODELS
    except Exception:
        GEMINI_MODELS = {}

    # ── 1. Governance policies (cost + enabled flag) ──────────────────────
    policies = await get_model_policies(db)
    policy_map: dict[str, ModelQuotaPolicy] = {
        p.model_id: p for p in policies
    }

    # ── 2. Build registry (OpenAI + Anthropic + Gemini) ──────────────────
    registry: dict[str, dict] = {}
    if openai_service:
        for mid, info in openai_service.models.items():
            if mid not in _HIDDEN_MODELS:
                registry[mid] = info
    for mid, info in (ANTHROPIC_MODELS or {}).items():
        registry[mid] = info
    for mid, info in (GEMINI_MODELS or {}).items():
        registry[mid] = info

    # ── 3. 7-day usage counts per model ─────────────────────────────────────
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    usage_result = await db.execute(
        select(ModelUsage.model, func.count(ModelUsage.id).label("cnt"))
        .where(ModelUsage.created_at >= cutoff)
        .group_by(ModelUsage.model)
    )
    rows = usage_result.fetchall()
    usage_counts: dict[str, int] = {row.model: row.cnt for row in rows}

    # ── 4. Determine which model gets each badge ──────────────────────────
    # "Popular"    → most used in last 7 days (among enabled models)
    # "Fastest"    → lowest cost_rate (ties broken alphabetically)
    # "Best Value" → vision + tools at lowest cost

    enabled_ids = {
        mid for mid, info in registry.items()
        if policy_map.get(mid) is None or policy_map[mid].is_enabled
    }

    popular_id = max(
        (mid for mid in usage_counts if mid in enabled_ids),
        key=lambda m: usage_counts[m],
        default=None,
    )

    def _cost(mid: str) -> float:
        p = policy_map.get(mid)
        return (
            p.cost_rate_per_1k_tokens if p
            else DEFAULT_COST_RATES.get(mid, 0.002)
        )

    # "Fastest" = cheapest tool-capable enabled model (excludes DALL-E)
    tool_models = [
        m for m in enabled_ids
        if registry.get(m, {}).get("supports_tools", True)
        and m != "dall-e-3"
    ]
    fastest_id = min(tool_models, key=_cost, default=None)

    # "Best Value" = vision+tools at lowest cost (skip already-badged)
    vision_tool_models = [
        m for m in enabled_ids
        if registry.get(m, {}).get("supports_vision")
        and registry.get(m, {}).get("supports_tools", True)
        and m not in {popular_id, fastest_id}
    ]
    best_value_id = min(vision_tool_models, key=_cost, default=None)

    # ── 5. Assemble response ─────────────────────────────────────────────────
    insights: list[ModelInsight] = []
    for mid, info in registry.items():
        policy = policy_map.get(mid)
        is_enabled = policy.is_enabled if policy else True
        if not is_enabled:
            continue

        provider = (
            policy.provider if policy
            else DEFAULT_PROVIDERS.get(mid, "azure_openai")
        )
        cost = _cost(mid)

        badge: Optional[str] = None
        if mid == popular_id:
            badge = "Popular"
        elif mid == fastest_id:
            badge = "Fastest"
        elif mid == best_value_id:
            badge = "Best Value"

        _third_party = mid in (ANTHROPIC_MODELS or {}) or mid in (
            GEMINI_MODELS or {}
        )
        insights.append(ModelInsight(
            id=mid,
            name=_MODEL_DISPLAY_NAMES.get(mid, mid),
            provider=provider,
            description=info.get("description", ""),
            cost_per_1k_tokens=cost,
            performance_label=_MODEL_PERFORMANCE_LABELS.get(
                mid, "General purpose"
            ),
            supports_vision=info.get("supports_vision", False),
            supports_tools=info.get(
                "supports_tools", not _third_party
            ),
            is_default=info.get("is_default", False),
            preview=mid in (ANTHROPIC_MODELS or {}),
            badge=badge,
            usage_rank=usage_counts.get(mid, 0),
        ))

    # Sort: default first, then by usage rank desc
    insights.sort(key=lambda m: (not m.is_default, -m.usage_rank))
    return insights


# ── MIME types that get raw-base64 passthrough for the code interpreter ─────
_BINARY_PASSTHROUGH_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "application/csv",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument"
    ".presentationml.presentation",
}
_BINARY_PASSTHROUGH_EXTS = {
    ".xlsx", ".xls", ".csv", ".tsv",
    ".pdf", ".docx", ".doc", ".pptx",
}

# Rich-doc MIME types that benefit from Document Intelligence
_DI_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument"
    ".spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument"
    ".presentationml.presentation",
}


@router.post("/process-attachment")
async def process_chat_attachment(
    file: UploadFile = File(...),
    extract_text: bool = Form(default=True),
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Process an uploaded file for use in a chat message.

    Pipeline:
      Images  → base64 data URI + optional OCR
      Audio   → speech transcription
      Docs    → text extraction (Document Intelligence → local fallback)

    Security checks (before any parsing):
      1. File size ≤ 25 MB
      2. Magic-byte check — blocks executables / scripts
      3. MIME-type spoofing detection
      4. ZIP bomb detection
      5. Prompt-injection scan on extracted text
    """
    from app.services.file_security import (
        scan_file, scan_text, wrap_file_content,
    )

    try:
        file_data = await file.read()
        content_type = (
            file.content_type
            or mimetypes.guess_type(file.filename or "")[0]
            or "application/octet-stream"
        )
        filename = file.filename or "attachment"

        # ── 0. Per-user daily upload quota (M-4) ───────────────────────────
        from app.services.upload_quota import check_and_consume_upload_quota
        allowed, used_bytes, limit_bytes = await check_and_consume_upload_quota(
            current_user.id, len(file_data)
        )
        if not allowed:
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "daily_upload_quota_exceeded",
                    "message": (
                        f"Daily upload limit of "
                        f"{limit_bytes // (1024 * 1024)} MB reached. "
                        "Try again after midnight UTC."
                    ),
                    "limit_mb": limit_bytes // (1024 * 1024),
                    "used_mb": used_bytes // (1024 * 1024),
                },
            )

        # ── 1. Security scan ───────────────────────────────────────────────
        scan = scan_file(file_data, filename, content_type)
        if scan.blocked:
            logger.warning(
                "[security] Blocked upload user=%s file=%r: %s",
                getattr(current_user, "id", "?"),
                filename,
                scan.warnings,
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "file_rejected",
                    "message": (
                        scan.warnings[0] if scan.warnings
                        else "This file type is not permitted."
                    ),
                    "warnings": scan.warnings,
                },
            )
        if scan.warnings:
            logger.warning(
                "[security] risk=%s user=%s file=%r: %s",
                scan.risk_level,
                getattr(current_user, "id", "?"),
                filename,
                scan.warnings,
            )

        # ── 1b. Antivirus scan (Phase 6 / M-5) ────────────────────────────
        from app.services.av_scan_service import (
            scan_bytes as av_scan_bytes,
            should_fail_closed_on_unknown,
        )
        av = await av_scan_bytes(file_data, filename)
        if av.is_malicious:
            logger.warning(
                "[av] Quarantined chat attachment user=%s file=%r engine=%s sig=%s",
                getattr(current_user, "id", "?"),
                filename, av.engine, av.signature,
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "file_quarantined",
                    "message": "File rejected by antivirus scan.",
                    "engine": av.engine,
                    "signature": av.signature,
                },
            )
        if av.verdict in ("unknown", "error") and should_fail_closed_on_unknown():
            logger.warning(
                "[av] Rejecting chat attachment (fail-closed) user=%s file=%r verdict=%s",
                getattr(current_user, "id", "?"),
                filename, av.verdict,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "av_scan_unavailable",
                    "message": "Antivirus scanner unavailable; upload rejected.",
                },
            )

        # ── 2. Images ──────────────────────────────────────────────────────
        if content_type.startswith("image/"):
            b64 = base64.b64encode(file_data).decode("utf-8")
            data_uri = f"data:{content_type};base64,{b64}"
            result: dict = {
                "filename": filename,
                "content_type": content_type,
                "type": "image",
                "base64_data": data_uri,
                "size": len(file_data),
            }
            if extract_text:
                try:
                    from app.services.document_intelligence_service import (
                        document_intelligence_service as _di,
                        DocumentModel,
                    )
                    if _di and _di.is_configured:
                        ocr = await _di.analyze_document(
                            document=file_data,
                            model_id=DocumentModel.PREBUILT_READ,
                        )
                        result["ocr_text"] = ocr.text
                except Exception as ocr_exc:
                    logger.warning("OCR failed %r: %s", filename, ocr_exc)
            return result

        # ── 3. Audio → transcribe ──────────────────────────────────────────
        if content_type.startswith("audio/"):
            try:
                from app.services.speech_service import speech_service
                if speech_service:
                    tr = await speech_service.transcribe(
                        audio_data=file_data,
                        content_type=content_type,
                    )
                    return {
                        "filename": filename,
                        "content_type": content_type,
                        "type": "audio",
                        "text_content": tr.text,
                        "size": len(file_data),
                    }
            except Exception as sp_exc:
                logger.warning(
                    "Audio transcription failed %r: %s", filename, sp_exc
                )
            return {
                "filename": filename,
                "content_type": content_type,
                "type": "audio",
                "text_content": "",
            }

        # ── 4. Documents / text files ──────────────────────────────────────
        text_content = ""

        # Try Document Intelligence first for PDFs and rich Office docs
        if content_type in _DI_MIMES:
            try:
                from app.services.document_intelligence_service import (
                    document_intelligence_service as _di,
                    DocumentModel,
                )
                if _di and _di.is_configured:
                    # Bound DI to 45s so a slow/hanging service can't stall
                    # the whole upload — local extraction will take over.
                    analysis = await asyncio.wait_for(
                        _di.analyze_document(
                            document=file_data,
                            model_id=DocumentModel.PREBUILT_LAYOUT,
                        ),
                        timeout=45.0,
                    )
                    text_content = analysis.text
            except asyncio.TimeoutError:
                logger.warning(
                    "Document Intelligence timed out for %r — falling back to local extraction",
                    filename,
                )
            except Exception as di_exc:
                logger.warning(
                    "Document Intelligence failed %r: %s", filename, di_exc
                )

        # Local extraction fallback (PyMuPDF, python-docx, openpyxl, …)
        if not text_content:
            try:
                from app.services.document_service import (
                    get_document_processor,
                )
                text_content, _ = get_document_processor().extract_text(
                    file_data, content_type, filename
                )
            except Exception as ex:
                logger.warning(
                    "Local extraction failed %r: %s", filename, ex
                )

        # Last resort for plain-text content types
        if not text_content and content_type.startswith("text/"):
            try:
                text_content = file_data.decode("utf-8", errors="replace")
            except Exception:
                text_content = ""

        # ── 5. Prompt-injection scan on extracted text ─────────────────────
        if text_content:
            txt_scan = scan_text(text_content, filename)
            if txt_scan.injection_detected:
                logger.warning(
                    "[security] Injection patterns in %r (user=%s): %s",
                    filename,
                    getattr(current_user, "id", "?"),
                    txt_scan.matched_snippets,
                )
                text_content = wrap_file_content(
                    text_content, filename, injection_detected=True
                )
            else:
                text_content = wrap_file_content(
                    text_content, filename, injection_detected=False
                )

        # Binary passthrough for code-interpreter consumption
        is_passthrough = (
            content_type in _BINARY_PASSTHROUGH_MIMES
            or any(
                filename.lower().endswith(ext)
                for ext in _BINARY_PASSTHROUGH_EXTS
            )
        )

        result = {
            "filename": filename,
            "content_type": content_type,
            "type": "document",
            "text_content": text_content[:50_000],  # token cap
            "size": len(file_data),
        }
        if is_passthrough:
            result["raw_base64"] = base64.b64encode(
                file_data
            ).decode("utf-8")
        return result

    except HTTPException:
        raise  # re-raise security rejections as-is
    except Exception as e:
        logger.error("process_attachment error: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process attachment: {e}",
        )
