"""
Mela AI - Model Settings API

GET  /settings/models          — list all models with their rankings
PUT  /settings/models/rankings — bulk-update model rankings
GET  /settings/claude-usage    — current user's Claude usage today
"""

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.auth import UserInfo
from app.services.claude_usage_service import claude_usage_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class ModelRankingResponse(BaseModel):
    id: str
    model_id: str
    display_name: str
    provider: str
    rank: int
    is_enabled: bool
    is_default: bool
    max_tokens: Optional[int]
    notes: Optional[str]
    cost_multiplier: float
    updated_at: datetime

    class Config:
        from_attributes = True


class ModelRankingUpdate(BaseModel):
    model_id: str
    rank: Optional[int] = None
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    notes: Optional[str] = None
    cost_multiplier: Optional[float] = None


class BulkRankingUpdate(BaseModel):
    rankings: List[ModelRankingUpdate]


# ── Default model list (seeded on first GET if table is empty) ────────────────

_DEFAULT_MODELS = [
    {"model_id": "gpt-5.2-chat",      "display_name": "GPT-5.2",          "provider": "azure_openai",   "rank": 1,  "is_default": True,  "cost_multiplier": 7.5},
    {"model_id": "gpt-4.1",           "display_name": "GPT-4.1",          "provider": "azure_openai",   "rank": 2,  "is_default": False, "cost_multiplier": 3.0},
    {"model_id": "gpt-4o",            "display_name": "GPT-4o",           "provider": "azure_openai",   "rank": 3,  "is_default": False, "cost_multiplier": 3.0},
    {"model_id": "kimi-k2.5",         "display_name": "Kimi K2.5",        "provider": "azure_ai_foundry","rank": 4,  "is_default": False, "cost_multiplier": 2.0},
    {"model_id": "mistral-large-3",   "display_name": "Mistral Large 3",  "provider": "azure_ai_foundry","rank": 5,  "is_default": False, "cost_multiplier": 2.0},
    {"model_id": "grok-3-mini",       "display_name": "Grok-3-mini",      "provider": "azure_ai_foundry","rank": 6,  "is_default": False, "cost_multiplier": 1.0},
    {"model_id": "llama-4-maverick",  "display_name": "Llama 4 Maverick", "provider": "azure_ai_foundry","rank": 7,  "is_default": False, "cost_multiplier": 1.0},
    {"model_id": "gemini-2.0-flash",  "display_name": "Gemini 2.0 Flash", "provider": "google",         "rank": 8,  "is_default": False, "cost_multiplier": 1.0},
    {"model_id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6","provider": "anthropic",      "rank": 9,  "is_default": False, "cost_multiplier": 5.0},
    {"model_id": "claude-haiku-4-5",  "display_name": "Claude Haiku 4.5", "provider": "anthropic",      "rank": 10, "is_default": False, "cost_multiplier": 1.0},
]


async def _seed_defaults(db):
    """Upsert the default model list — adds any missing models, skips existing ones."""
    from app.models.models import ModelRanking
    result = await db.execute(select(ModelRanking))
    existing_ids = {row.model_id for row in result.scalars().all()}
    added = False
    for m in _DEFAULT_MODELS:
        if m["model_id"] not in existing_ids:
            db.add(ModelRanking(
                id=str(uuid.uuid4()),
                model_id=m["model_id"],
                display_name=m["display_name"],
                provider=m["provider"],
                rank=m["rank"],
                is_enabled=True,
                is_default=m["is_default"],
                cost_multiplier=m.get("cost_multiplier", 1.0),
            ))
            added = True
    if added:
        await db.commit()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/models", response_model=List[ModelRankingResponse])
async def list_model_rankings(
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return all models ordered by rank."""
    from app.models.models import ModelRanking
    await _seed_defaults(db)
    result = await db.execute(
        select(ModelRanking).order_by(ModelRanking.rank)
    )
    return result.scalars().all()


@router.put("/models/rankings", response_model=List[ModelRankingResponse])
async def update_model_rankings(
    body: BulkRankingUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    """Bulk-update model rankings / enabled flags."""
    from app.models.models import ModelRanking
    for upd in body.rankings:
        result = await db.execute(
            select(ModelRanking).where(ModelRanking.model_id == upd.model_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model '{upd.model_id}' not found",
            )
        if upd.rank is not None:
            row.rank = upd.rank
        if upd.is_enabled is not None:
            row.is_enabled = upd.is_enabled
        if upd.is_default is not None:
            row.is_default = upd.is_default
        if upd.notes is not None:
            row.notes = upd.notes
        if upd.cost_multiplier is not None:
            if upd.cost_multiplier < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="cost_multiplier must be \u2265 0",
                )
            row.cost_multiplier = upd.cost_multiplier
        row.updated_at = datetime.utcnow()
        row.updated_by = current_user.id

    await db.commit()

    result = await db.execute(
        select(ModelRanking).order_by(ModelRanking.rank)
    )
    return result.scalars().all()


@router.get("/claude-usage")
async def get_claude_usage(
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return today's Claude usage for the current user."""
    return await claude_usage_service.get_usage(db, current_user.id)


# ── Instructions CRUD ─────────────────────────────────────────────────────────

class InstructionCreate(BaseModel):
    name: str
    content: str
    scope: str = "user"
    priority: int = 100


class InstructionUpdate(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    priority: Optional[int] = None
    is_enabled: Optional[bool] = None


class InstructionResponse(BaseModel):
    id: str
    name: str
    content: str
    scope: str
    priority: int
    is_enabled: bool
    created_by: str

    class Config:
        from_attributes = True


@router.get("/instructions", response_model=List[InstructionResponse])
async def list_instructions(
    scope: Optional[str] = None,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    from app.services.instruction_service import instruction_service
    await instruction_service.seed_builtins(db)
    is_admin = getattr(current_user, "role", "user") == "admin"
    rows = await instruction_service.list_instructions(db, current_user.id, scope=scope, admin=is_admin)
    return rows


@router.post("/instructions", response_model=InstructionResponse, status_code=201)
async def create_instruction(
    body: InstructionCreate,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    from app.services.instruction_service import instruction_service
    is_admin = getattr(current_user, "role", "user") == "admin"
    # Only admins can create global/org scope instructions
    if body.scope in ("global", "org") and not is_admin:
        raise HTTPException(status_code=403, detail="Admin role required for global/org scope instructions")
    return await instruction_service.create_instruction(
        db, current_user.id,
        name=body.name,
        content=body.content,
        scope=body.scope,
        priority=body.priority,
    )


@router.put("/instructions/{instruction_id}", response_model=InstructionResponse)
async def update_instruction(
    instruction_id: str,
    body: InstructionUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    from app.services.instruction_service import instruction_service
    is_admin = getattr(current_user, "role", "user") == "admin"
    result = await instruction_service.update_instruction(
        db, instruction_id, current_user.id,
        name=body.name, content=body.content,
        priority=body.priority, is_enabled=body.is_enabled,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Instruction not found or permission denied")
    return result


@router.delete("/instructions/{instruction_id}", status_code=204)
async def delete_instruction(
    instruction_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    from app.services.instruction_service import instruction_service
    is_admin = getattr(current_user, "role", "user") == "admin"
    ok = await instruction_service.delete_instruction(db, instruction_id, current_user.id, admin=is_admin)
    if not ok:
        raise HTTPException(status_code=404, detail="Instruction not found or permission denied")


# ── Skills CRUD ───────────────────────────────────────────────────────────────

class SkillCreate(BaseModel):
    name: str
    description: Optional[str] = None
    category: str = "general"
    trigger_keywords: Optional[List[str]] = None
    instruction_block: str
    model_preference: Optional[str] = None
    rank: int = 100
    is_enabled: bool = True
    visibility: str = "user"


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    trigger_keywords: Optional[List[str]] = None
    instruction_block: Optional[str] = None
    model_preference: Optional[str] = None
    rank: Optional[int] = None
    is_enabled: Optional[bool] = None


class SkillResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    category: str
    trigger_keywords: Optional[str]  # JSON string
    instruction_block: str
    model_preference: Optional[str]
    is_enabled: bool
    is_builtin: bool
    rank: int
    visibility: str
    created_by: str

    class Config:
        from_attributes = True


@router.get("/skills", response_model=List[SkillResponse])
async def list_skills(
    category: Optional[str] = None,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    from app.services.skill_service import skill_service
    await skill_service.seed_builtins(db)
    is_admin = getattr(current_user, "role", "user") == "admin"
    rows = await skill_service.list_skills(db, current_user.id, category=category, admin=is_admin)
    return rows


@router.post("/skills", response_model=SkillResponse, status_code=201)
async def create_skill(
    body: SkillCreate,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    from app.services.skill_service import skill_service
    is_admin = getattr(current_user, "role", "user") == "admin"
    if body.visibility in ("global", "org") and not is_admin:
        raise HTTPException(status_code=403, detail="Admin role required for global/org visibility skills")
    return await skill_service.create_skill(
        db, current_user.id,
        name=body.name, description=body.description,
        category=body.category, trigger_keywords=body.trigger_keywords,
        instruction_block=body.instruction_block,
        model_preference=body.model_preference,
        rank=body.rank, is_enabled=body.is_enabled,
        visibility=body.visibility,
    )


@router.put("/skills/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    body: SkillUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    from app.services.skill_service import skill_service
    is_admin = getattr(current_user, "role", "user") == "admin"
    result = await skill_service.update_skill(
        db, skill_id, current_user.id, admin=is_admin,
        name=body.name, description=body.description,
        category=body.category, trigger_keywords=body.trigger_keywords,
        instruction_block=body.instruction_block,
        model_preference=body.model_preference,
        rank=body.rank, is_enabled=body.is_enabled,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Skill not found or permission denied")
    return result


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    from app.services.skill_service import skill_service
    is_admin = getattr(current_user, "role", "user") == "admin"
    ok = await skill_service.delete_skill(db, skill_id, current_user.id, admin=is_admin)
    if not ok:
        raise HTTPException(status_code=404, detail="Skill not found or permission denied")


# ── Model Access Governance (admin only) ────────────────────────────────────

class ModelAccessRule(BaseModel):
    model_id: str
    is_allowed: bool
    user_id: Optional[str] = None   # if set: per-user rule
    role: Optional[str] = None      # if set: per-role rule


class ModelAccessRuleResponse(BaseModel):
    id: str
    model_id: str
    user_id: Optional[str]
    role: Optional[str]
    is_allowed: bool
    created_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/model-access", response_model=List[ModelAccessRuleResponse])
async def list_model_access_rules(
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    """List all model access rules (admin only)."""
    if not any(r.lower() == "admin" for r in (getattr(current_user, "roles", []) or [])):
        raise HTTPException(status_code=403, detail="Admin role required")
    from app.models.models import UserModelAccess
    from sqlalchemy import select
    result = await db.execute(select(UserModelAccess).order_by(UserModelAccess.created_at.desc()))
    return result.scalars().all()


@router.post("/model-access", response_model=ModelAccessRuleResponse, status_code=201)
async def set_model_access_rule(
    body: ModelAccessRule,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create or update a model access rule (admin only).

    Supply either user_id (per-user) or role (per-role), not both.
    """
    if not any(r.lower() == "admin" for r in (getattr(current_user, "roles", []) or [])):
        raise HTTPException(status_code=403, detail="Admin role required")
    if not body.user_id and not body.role:
        raise HTTPException(status_code=422, detail="Supply user_id or role")
    if body.user_id and body.role:
        raise HTTPException(status_code=422, detail="Supply user_id OR role, not both")

    import app.services.model_access_service as mac_svc
    rule = await mac_svc.set_model_access(
        db,
        model_id=body.model_id,
        is_allowed=body.is_allowed,
        user_id=body.user_id,
        role=body.role,
        set_by=current_user.id,
    )
    await db.commit()
    return rule


@router.delete("/model-access/{rule_id}", status_code=204)
async def delete_model_access_rule(
    rule_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    """Delete a model access rule (admin only)."""
    if not any(r.lower() == "admin" for r in (getattr(current_user, "roles", []) or [])):
        raise HTTPException(status_code=403, detail="Admin role required")
    from app.models.models import UserModelAccess
    from sqlalchemy import select, delete as sa_delete
    result = await db.execute(
        sa_delete(UserModelAccess).where(UserModelAccess.id == rule_id)
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Rule not found")


@router.get("/model-access/user/{user_id}")
async def get_user_model_access(
    user_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get effective allowed models for a user (admin or self)."""
    if user_id != current_user.id and not any(r.lower() == "admin" for r in (getattr(current_user, "roles", []) or [])):
        raise HTTPException(status_code=403, detail="Admin role required")
    import app.services.model_access_service as mac_svc
    roles = getattr(current_user, "roles", []) or [] if user_id == current_user.id else []
    allowed = await mac_svc.get_allowed_models(db, user_id, roles)
    return [{"model_id": m.model_id, "display_name": m.display_name, "rank": m.rank} for m in allowed]


# ── Provider status ──────────────────────────────────────────────────────────

@router.get("/providers/status")
async def get_provider_status(
    current_user: UserInfo = Depends(get_current_user),
):
    """Return configured status for each LLM/service provider."""
    from app.core.config import settings
    from app.services.anthropic_service import anthropic_service

    providers = {
        "azure_openai": {
            "name": "Azure OpenAI / AI Foundry",
            "configured": bool(settings.AZURE_OPENAI_API_KEY or settings.AI_FOUNDRY_API_KEY),
            "endpoint": bool(settings.AZURE_OPENAI_ENDPOINT or settings.AI_FOUNDRY_ENDPOINT),
            "status": "ok" if (settings.AZURE_OPENAI_API_KEY or settings.AI_FOUNDRY_API_KEY) else "missing_key",
        },
        "anthropic": {
            "name": "Anthropic Claude",
            "configured": bool(settings.ANTHROPIC_API_KEY) and settings.ANTHROPIC_ENABLED,
            "service_ready": anthropic_service is not None,
            "rpm_limit": settings.ANTHROPIC_RPM_LIMIT,
            "daily_limit": settings.CLAUDE_DAILY_QUESTION_LIMIT,
            "status": "ok" if anthropic_service else ("disabled" if not settings.ANTHROPIC_ENABLED else "missing_key"),
        },
        "image_generation": {
            "name": "Image Generation",
            "flux_configured": bool(settings.FLUX_API_KEY and settings.FLUX_ENDPOINT),
            "dalle_configured": bool(getattr(settings, "AZURE_DALLE_API_KEY", "")),
            "provider_order": settings.IMAGE_PROVIDER_ORDER,
            "status": "ok" if (settings.FLUX_API_KEY or getattr(settings, "AZURE_DALLE_API_KEY", "")) else "missing_key",
        },
        "speech": {
            "name": "Azure Speech",
            "configured": bool(settings.AZURE_SPEECH_KEY),
            "region": settings.AZURE_SPEECH_REGION or "not set",
            "status": "ok" if settings.AZURE_SPEECH_KEY else "missing_key",
        },
        "document_intelligence": {
            "name": "Azure Document Intelligence",
            "configured": bool(settings.AZURE_DOCUMENT_INTELLIGENCE_KEY),
            "status": "ok" if settings.AZURE_DOCUMENT_INTELLIGENCE_KEY else "missing_key",
        },
        "search": {
            "name": "Azure AI Search",
            "configured": bool(getattr(settings, "AZURE_SEARCH_ADMIN_KEY", "")),
            "status": "ok" if getattr(settings, "AZURE_SEARCH_ADMIN_KEY", "") else "missing_key",
        },
    }
    return providers


@router.get("/feature-flags")
async def get_feature_flags(
    current_user: UserInfo = Depends(get_current_user),
):
    """Return all feature flags and their current values."""
    from app.core.config import settings

    flags = {
        "voice": getattr(settings, "ENABLE_VOICE", True),
        "file_upload": getattr(settings, "ENABLE_FILE_UPLOAD", True),
        "agents": getattr(settings, "ENABLE_AGENTS", True),
        "rag": getattr(settings, "ENABLE_RAG", True),
        "image_generation": getattr(settings, "ENABLE_IMAGE_GENERATION", True),
        "translation": getattr(settings, "ENABLE_TRANSLATION", True),
        "document_intelligence": getattr(settings, "ENABLE_DOCUMENT_INTELLIGENCE", True),
        "code_interpreter": getattr(settings, "ENABLE_AGENTS", True),
        "claude_models": getattr(settings, "ANTHROPIC_ENABLED", False),
        "web_search": getattr(settings, "ENABLE_WEB_SEARCH", False),
    }
    return flags
