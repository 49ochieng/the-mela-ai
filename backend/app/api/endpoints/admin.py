"""
Mela AI - Admin Endpoints
"""

import logging
from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.core.database import get_db
from app.core.security import get_current_admin_user, get_current_user
from app.models import (
    User, Conversation, Message, Document,
    AuditLog, ModelUsage, EnabledTool, SystemSettings,
)
from app.schemas.auth import UserInfo, UserResponse, UserUpdate
from app.schemas.admin import (
    UsageStats, AnalyticsResponse, DailyUsage, ModelUsageStats,
    UserUsageStats, AuditLogResponse,
    ToolConfigResponse, ToolConfigUpdate,
    SystemSettingResponse, SystemSettingUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_VALID_ALERT_SEVERITIES = {"critical", "error", "warning", "info"}


class AlertTestRequest(BaseModel):
    """Request payload for manually triggering an ops alert."""

    severity: str = "critical"
    error_type: str = "ManualTestAlert"
    message: str = "Manual ops alert test"
    route: str = "POST /api/v1/admin/alerts/test"
    run_url: str = ""


def _scoped_tenant_id(current_user: UserInfo) -> Optional[str]:
    """Return tenant scope for admin endpoints when present on the caller."""
    tenant_id = (current_user.tenant_id or "").strip()
    return tenant_id or None


def _tenant_user_ids_subquery(tenant_id: str):
    """Distinct user IDs that have conversation activity in the tenant."""
    return (
        select(Conversation.user_id.label("user_id"))
        .where(Conversation.tenant_id == tenant_id)
        .distinct()
        .subquery()
    )


def _scoped_user_query(user_id: str, tenant_id: Optional[str]):
    """Return user query constrained to tenant-visible users when scoped."""
    query = select(User).where(User.id == user_id)
    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        query = query.where(User.id.in_(select(tenant_user_ids.c.user_id)))
    return query


def _require_global_admin(current_user: UserInfo) -> None:
    """Reject tenant-scoped admins from global control-plane operations."""
    if _scoped_tenant_id(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global admin access required",
        )


@router.get("/me")
async def get_admin_status(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns whether the authenticated user has admin privileges.
    Safe for any authenticated user to call — never returns 403.

    Also triggers bootstrap elevation on-the-fly if the user's email is in
    BOOTSTRAP_ADMIN_EMAILS, so admin access is granted even when the user
    hasn't gone through POST /auth/login yet.
    """
    import uuid as _uuid
    from app.models.models import User as UserModel, UserRole
    from app.core.config import settings as _settings

    token_is_admin = (
        "Admin" in current_user.roles or "admin" in current_user.roles
    )
    db_is_admin = False

    try:
        result = await db.execute(
            select(UserModel).where(UserModel.azure_id == current_user.id)
        )
        db_user = result.scalar_one_or_none()

        # If OID lookup missed, try by email (case-insensitive) — handles the case
        # where the same person previously logged in via dev token (fake azure_id)
        # or where email case differs between token and DB.
        # Migrating the azure_id to the real OID unifies the account.
        if db_user is None and current_user.email:
            email_result = await db.execute(
                select(UserModel).where(
                    func.lower(UserModel.email) == current_user.email.lower()
                )
            )
            email_user = email_result.scalar_one_or_none()
            if email_user is not None:
                email_user.azure_id = current_user.id
                db_user = email_user
                await db.flush()
                logger.info(
                    "Migrated azure_id for %s to real OID %s",
                    current_user.email, current_user.id,
                )

        # ── Bootstrap elevation ────────────────────────────────────────────
        # Elevate if email matches BOOTSTRAP_ADMIN_EMAILS OR oid matches
        # BOOTSTRAP_ADMIN_OIDS (critical for accounts where Entra token has
        # no email claim — OID is always present and stable).
        bootstrap_list = _settings.bootstrap_admin_email_list
        bootstrap_oids = _settings.bootstrap_admin_oid_list
        user_email_lower = (current_user.email or "").lower()
        user_oid_lower = (current_user.id or "").lower()

        _is_bootstrap = (
            (bootstrap_list and user_email_lower and user_email_lower in bootstrap_list)
            or (bootstrap_oids and user_oid_lower and user_oid_lower in bootstrap_oids)
        )

        if _is_bootstrap:
            if db_user is None:
                # First-ever call for this account — create a minimal DB row.
                db_user = UserModel(
                    id=str(_uuid.uuid4()),
                    azure_id=current_user.id,
                    email=current_user.email,
                    name=current_user.name or user_email_lower or user_oid_lower,
                    role=UserRole.ADMIN,
                    bootstrap_elevated_at=datetime.utcnow(),
                )
                db.add(db_user)
                try:
                    await db.flush()
                except Exception as _ie:
                    # UNIQUE constraint — a row with this email already exists
                    # (race condition or case-sensitivity miss). Rollback and re-fetch.
                    if "unique" in str(_ie).lower() or "integrity" in type(_ie).__name__.lower():
                        await db.rollback()
                        _retry = await db.execute(
                            select(UserModel).where(
                                func.lower(UserModel.email) == (current_user.email or "").lower()
                            )
                        )
                        existing = _retry.scalar_one_or_none()
                        if existing is not None:
                            existing.azure_id = current_user.id
                            db_user = existing
                        else:
                            raise
                    else:
                        raise
                db.add(AuditLog(
                    user_id=db_user.id,
                    action="bootstrap_admin_elevation",
                    resource_type="user",
                    resource_id=db_user.id,
                    details={"email": current_user.email, "oid": current_user.id, "source": "GET /admin/me (new user)"},
                    success=True,
                ))
                await db.commit()
                logger.warning(
                    "Bootstrap admin (new user) created for oid=%s email=%s",
                    current_user.id, current_user.email,
                )
            elif db_user.role != UserRole.ADMIN:
                # Bootstrap identities are ALWAYS admin — no bootstrap_elevated_at guard.
                db_user.role = UserRole.ADMIN
                db_user.bootstrap_elevated_at = db_user.bootstrap_elevated_at or datetime.utcnow()
                if not db_user.email and current_user.email:
                    db_user.email = current_user.email
                db.add(AuditLog(
                    user_id=db_user.id,
                    action="bootstrap_admin_elevation",
                    resource_type="user",
                    resource_id=db_user.id,
                    details={"email": current_user.email, "oid": current_user.id, "source": "GET /admin/me (forced)"},
                    success=True,
                ))
                await db.commit()
                logger.warning(
                    "Bootstrap admin elevation (forced) granted to oid=%s email=%s",
                    current_user.id, current_user.email,
                )
            else:
                # ── H-8: bootstrap-listed user is ALREADY admin ─────────────
                # Original gap: this branch silently set db_is_admin=True with
                # no audit trail, so we could not prove the bootstrap list was
                # matched on a given request. Emit a lightweight audit row,
                # throttled to once per user per 24h to avoid spam from the
                # frontend polling /admin/me on every page load.
                _cutoff = datetime.utcnow() - timedelta(hours=24)
                _recent = await db.execute(
                    select(AuditLog.id).where(
                        and_(
                            AuditLog.user_id == db_user.id,
                            AuditLog.action == "bootstrap_admin_check",
                            AuditLog.created_at >= _cutoff,
                        )
                    ).limit(1)
                )
                if _recent.scalar_one_or_none() is None:
                    from app.core.logging import log_security_event
                    await log_security_event(
                        db,
                        user_id=db_user.id,
                        action="bootstrap_admin_check",
                        event_type="bootstrap",
                        resource_type="user",
                        resource_id=db_user.id,
                        details={
                            "email": current_user.email,
                            "oid": current_user.id,
                            "already_admin": True,
                            "source": "GET /admin/me",
                        },
                        success=True,
                    )
                    await db.commit()

            # Ensure db_is_admin reflects the current state
            db_is_admin = True

        if db_user is not None and not db_is_admin:
            db_is_admin = db_user.role == UserRole.ADMIN
    except Exception:
        logger.exception("Error in get_admin_status bootstrap check")

    # Newly promoted: admin role, promoted within last 7 days, banner not yet shown.
    newly_promoted = False
    if (db_is_admin or token_is_admin) and db_user is not None:
        from datetime import timedelta as _td
        if (
            db_user.promoted_at is not None
            and not db_user.promotion_banner_shown
            and (datetime.utcnow() - db_user.promoted_at) < _td(days=7)
        ):
            newly_promoted = True

    return {"is_admin": db_is_admin or token_is_admin, "newly_promoted": newly_promoted}


@router.post("/me/ack-promotion")
async def ack_promotion(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark the admin-promotion banner as shown for the current user.
    Safe to call multiple times; idempotent.
    """
    from app.models.models import User as UserModel
    result = await db.execute(
        select(UserModel).where(UserModel.azure_id == current_user.id)
    )
    db_user = result.scalar_one_or_none()
    if db_user and db_user.promoted_at is not None:
        db_user.promotion_banner_shown = True
        await db.commit()
    return {"ok": True}


@router.get("/stats", response_model=UsageStats)
async def get_usage_stats(
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get overall usage statistics."""
    today = datetime.utcnow().date()
    tenant_id = _scoped_tenant_id(current_user)
    tenant_user_ids = (
        _tenant_user_ids_subquery(tenant_id) if tenant_id else None
    )
    tenant_user_ids_select = (
        select(tenant_user_ids.c.user_id) if tenant_user_ids is not None else None
    )

    # Total users
    if tenant_user_ids is not None:
        total_users = await db.scalar(
            select(func.count()).select_from(tenant_user_ids)
        )
    else:
        total_users = await db.scalar(select(func.count(User.id)))

    # Active users today
    active_today_query = (
        select(func.count(func.distinct(Message.conversation_id)))
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(func.date(Message.created_at) == today)
    )
    if tenant_id:
        active_today_query = active_today_query.where(
            Conversation.tenant_id == tenant_id
        )
    active_today = await db.scalar(active_today_query)

    # Total conversations (exclude private)
    total_conversations_query = select(func.count(Conversation.id)).where(
        Conversation.is_private == False
    )
    if tenant_id:
        total_conversations_query = total_conversations_query.where(
            Conversation.tenant_id == tenant_id
        )
    total_conversations = await db.scalar(total_conversations_query)

    # Total messages
    if tenant_id:
        total_messages = await db.scalar(
            select(func.count(Message.id))
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.tenant_id == tenant_id)
        )
    else:
        total_messages = await db.scalar(select(func.count(Message.id)))

    # Total tokens
    if tenant_id:
        total_tokens = await db.scalar(
            select(func.sum(ModelUsage.total_tokens))
            .select_from(ModelUsage)
            .join(Conversation, ModelUsage.conversation_id == Conversation.id)
            .where(Conversation.tenant_id == tenant_id)
        ) or 0
    else:
        total_tokens = await db.scalar(
            select(func.sum(ModelUsage.total_tokens))
        ) or 0

    # Documents
    total_docs_query = select(func.count(Document.id)).where(
        Document.is_active == True
    )
    indexed_docs_query = select(func.count(Document.id)).where(
        and_(Document.is_active == True, Document.is_indexed == True)
    )
    if tenant_user_ids_select is not None:
        total_docs_query = total_docs_query.where(
            Document.uploaded_by.in_(tenant_user_ids_select)
        )
        indexed_docs_query = indexed_docs_query.where(
            Document.uploaded_by.in_(tenant_user_ids_select)
        )
    total_docs = await db.scalar(total_docs_query)
    indexed_docs = await db.scalar(indexed_docs_query)

    return UsageStats(
        total_users=total_users or 0,
        active_users_today=active_today or 0,
        total_conversations=total_conversations or 0,
        total_messages=total_messages or 0,
        total_tokens_used=total_tokens,
        total_documents=total_docs or 0,
        indexed_documents=indexed_docs or 0,
    )


@router.get("/analytics", response_model=AnalyticsResponse)
async def get_analytics(
    days: int = Query(default=30, ge=1, le=365),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed analytics."""
    tenant_id = _scoped_tenant_id(current_user)

    # Get overview
    overview = await get_usage_stats(current_user, db)

    # Daily usage for last N days
    start_date = datetime.utcnow() - timedelta(days=days)

    daily_usage = []
    for i in range(days):
        date = (start_date + timedelta(days=i)).date()

        # Users active on this day
        users_query = select(func.count(func.distinct(Conversation.user_id))).where(
            func.date(Conversation.updated_at) == date
        )
        if tenant_id:
            users_query = users_query.where(Conversation.tenant_id == tenant_id)
        users = await db.scalar(users_query) or 0

        # Conversations on this day
        convs_query = select(func.count(Conversation.id)).where(
            func.date(Conversation.created_at) == date
        )
        if tenant_id:
            convs_query = convs_query.where(Conversation.tenant_id == tenant_id)
        convs = await db.scalar(convs_query) or 0

        # Messages on this day
        if tenant_id:
            msgs = await db.scalar(
                select(func.count(Message.id))
                .select_from(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    func.date(Message.created_at) == date,
                    Conversation.tenant_id == tenant_id,
                )
            ) or 0
        else:
            msgs = await db.scalar(
                select(func.count(Message.id))
                .where(func.date(Message.created_at) == date)
            ) or 0

        # Tokens on this day
        if tenant_id:
            tokens = await db.scalar(
                select(func.sum(ModelUsage.total_tokens))
                .select_from(ModelUsage)
                .join(Conversation, ModelUsage.conversation_id == Conversation.id)
                .where(
                    func.date(ModelUsage.created_at) == date,
                    Conversation.tenant_id == tenant_id,
                )
            ) or 0
        else:
            tokens = await db.scalar(
                select(func.sum(ModelUsage.total_tokens))
                .where(func.date(ModelUsage.created_at) == date)
            ) or 0

        daily_usage.append(DailyUsage(
            date=date,
            users=users,
            conversations=convs,
            messages=msgs,
            tokens=tokens,
        ))

    # Model usage
    model_query = (
        select(
            ModelUsage.model,
            func.count(ModelUsage.id).label("count"),
            func.sum(ModelUsage.total_tokens).label("total"),
            func.sum(ModelUsage.prompt_tokens).label("prompt"),
            func.sum(ModelUsage.completion_tokens).label("completion"),
        )
        .where(ModelUsage.created_at >= start_date)
        .group_by(ModelUsage.model)
    )
    if tenant_id:
        model_query = (
            select(
                ModelUsage.model,
                func.count(ModelUsage.id).label("count"),
                func.sum(ModelUsage.total_tokens).label("total"),
                func.sum(ModelUsage.prompt_tokens).label("prompt"),
                func.sum(ModelUsage.completion_tokens).label("completion"),
            )
            .select_from(ModelUsage)
            .join(Conversation, ModelUsage.conversation_id == Conversation.id)
            .where(
                ModelUsage.created_at >= start_date,
                Conversation.tenant_id == tenant_id,
            )
            .group_by(ModelUsage.model)
        )
    model_result = await db.execute(model_query)

    model_usage = [
        ModelUsageStats(
            model=row.model,
            request_count=row.count,
            total_tokens=row.total or 0,
            prompt_tokens=row.prompt or 0,
            completion_tokens=row.completion or 0,
        )
        for row in model_result.all()
    ]

    # Top users
    top_users_query = (
        select(
            User.id,
            User.name,
            User.email,
            func.count(Conversation.id).label("conv_count"),
            func.sum(Message.tokens_used).label("token_sum"),
        )
        .join(Conversation, Conversation.user_id == User.id)
        .join(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.created_at >= start_date)
        .group_by(User.id, User.name, User.email)
        .order_by(func.sum(Message.tokens_used).desc())
        .limit(10)
    )
    if tenant_id:
        top_users_query = top_users_query.where(
            Conversation.tenant_id == tenant_id
        )
    user_result = await db.execute(top_users_query)

    top_users = [
        UserUsageStats(
            user_id=row.id,
            user_name=row.name,
            user_email=row.email,
            total_conversations=row.conv_count,
            total_messages=0,  # Would need subquery
            total_tokens=row.token_sum or 0,
        )
        for row in user_result.all()
    ]

    return AnalyticsResponse(
        overview=overview,
        daily_usage=daily_usage,
        model_usage=model_usage,
        top_users=top_users,
    )


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    limit: int = 50,
    offset: int = 0,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users."""
    tenant_id = _scoped_tenant_id(current_user)
    query = select(User)
    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        query = query.where(User.id.in_(select(tenant_user_ids.c.user_id)))

    result = await db.execute(
        query.order_by(User.created_at.desc()).limit(limit).offset(offset)
    )
    users = result.scalars().all()
    return [UserResponse.model_validate(u) for u in users]


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user details."""
    tenant_id = _scoped_tenant_id(current_user)
    result = await db.execute(_scoped_user_query(user_id, tenant_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse.model_validate(user)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    data: UserUpdate,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user settings."""
    tenant_id = _scoped_tenant_id(current_user)
    result = await db.execute(_scoped_user_query(user_id, tenant_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if data.role is not None:
        from app.models.models import UserRole as _UserRole
        old_role = user.role
        user.role = data.role
        # Phase 2 (H-7): audit EVERY role change — promotions and demotions.
        if old_role != data.role:
            db.add(AuditLog(
                user_id=user.id,
                action="user_role_changed",
                event_type="role_change",
                resource_type="user",
                resource_id=user.id,
                details={
                    "changed_by": current_user.email,
                    "from_role": str(old_role),
                    "to_role": str(data.role),
                },
                success=True,
            ))
        # When promoting to admin for the first time, stamp promoted_at so the
        # user sees the "You've been promoted" banner on their next page load.
        if data.role == _UserRole.ADMIN and old_role != _UserRole.ADMIN:
            user.promoted_at = datetime.utcnow()
            user.promotion_banner_shown = False
            db.add(AuditLog(
                user_id=user.id,
                action="admin_promoted",
                resource_type="user",
                resource_id=user.id,
                details={"promoted_by": current_user.email, "from_role": str(old_role)},
                success=True,
            ))
    if data.daily_token_limit is not None:
        user.daily_token_limit = data.daily_token_limit
    if data.is_active is not None:
        prev_active = user.is_active
        user.is_active = data.is_active
        # When disabling, revoke ALL of the user's active sessions.
        if prev_active and not data.is_active:
            try:
                from app.core.sessions import revoke_all_user_sessions
                revoked = await revoke_all_user_sessions(db, user.id)
                db.add(AuditLog(
                    user_id=user.id,
                    action="user_disabled_revoke_all",
                    event_type="auth",
                    resource_type="user",
                    resource_id=user.id,
                    details={
                        "revoked_by": current_user.email,
                        "sessions_revoked": int(revoked),
                    },
                    success=True,
                ))
            except Exception as e:
                logger.warning("admin disable: revoke-all failed: %s", e)

    user.updated_at = datetime.utcnow()
    await db.commit()

    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/revoke-sessions")
async def admin_revoke_user_sessions(
    user_id: str,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke all active sessions for a user (forces re-authentication)."""
    tenant_id = _scoped_tenant_id(current_user)
    scoped_user = await db.scalar(_scoped_user_query(user_id, tenant_id))
    if not scoped_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    from app.core.sessions import revoke_all_user_sessions

    revoked = await revoke_all_user_sessions(db, user_id)
    db.add(AuditLog(
        user_id=user_id,
        action="admin_revoke_sessions",
        event_type="auth",
        resource_type="user",
        resource_id=user_id,
        details={
            "revoked_by": current_user.email,
            "sessions_revoked": int(revoked),
        },
        success=True,
    ))
    await db.commit()
    return {"revoked": int(revoked)}


@router.get("/audit-logs", response_model=List[AuditLogResponse])
async def get_audit_logs(
    limit: int = 100,
    offset: int = 0,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get audit logs with filters."""
    tenant_id = _scoped_tenant_id(current_user)
    query = select(AuditLog).join(User, AuditLog.user_id == User.id)

    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        query = query.where(
            AuditLog.user_id.in_(select(tenant_user_ids.c.user_id))
        )

    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if action:
        query = query.where(AuditLog.action == action)
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)

    query = query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    logs = result.scalars().all()

    return [AuditLogResponse.model_validate(log) for log in logs]


@router.get("/tools", response_model=List[ToolConfigResponse])
async def list_tools(
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List tool configurations."""
    _require_global_admin(current_user)
    result = await db.execute(select(EnabledTool))
    tools = result.scalars().all()
    return [ToolConfigResponse.model_validate(t) for t in tools]


@router.put("/tools/{tool_name}", response_model=ToolConfigResponse)
async def update_tool(
    tool_name: str,
    data: ToolConfigUpdate,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update tool configuration."""
    _require_global_admin(current_user)
    result = await db.execute(
        select(EnabledTool).where(EnabledTool.tool_name == tool_name)
    )
    tool = result.scalar_one_or_none()

    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found",
        )

    if data.is_enabled is not None:
        tool.is_enabled = data.is_enabled
    if data.requires_confirmation is not None:
        tool.requires_confirmation = data.requires_confirmation
    if data.allowed_roles is not None:
        tool.allowed_roles = data.allowed_roles
    if data.configuration is not None:
        tool.configuration = data.configuration

    tool.updated_at = datetime.utcnow()
    await db.commit()

    return ToolConfigResponse.model_validate(tool)


@router.get("/settings", response_model=List[SystemSettingResponse])
async def list_settings(
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List system settings."""
    _require_global_admin(current_user)
    result = await db.execute(select(SystemSettings))
    settings = result.scalars().all()
    return [SystemSettingResponse.model_validate(s) for s in settings]


@router.put("/settings/{key}", response_model=SystemSettingResponse)
async def update_setting(
    key: str,
    data: SystemSettingUpdate,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a system setting."""
    _require_global_admin(current_user)
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == key)
    )
    setting = result.scalar_one_or_none()

    if not setting:
        # Create new setting
        setting = SystemSettings(
            key=key,
            value=data.value,
            description=data.description,
            updated_by=current_user.id,
        )
        db.add(setting)
    else:
        setting.value = data.value
        if data.description:
            setting.description = data.description
        setting.updated_by = current_user.id
        setting.updated_at = datetime.utcnow()

    await db.commit()

    return SystemSettingResponse.model_validate(setting)


# ─────────────────────────────────────────────────────────────────────────────
# Private Chat Admin Endpoints
# ─────────────────────────────────────────────────────────────────────────────

import json as _json
from app.schemas.settings import OrgSettings
from app.schemas.chat import ConversationResponse

_ORG_SETTINGS_KEY = "org_settings"


@router.get("/private-conversations", response_model=list[ConversationResponse])
async def list_private_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: Optional[str] = Query(default=None),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all private conversations (admin only). Optionally filter by user_id."""
    tenant_id = _scoped_tenant_id(current_user)
    from sqlalchemy import func as _func
    msg_count_subq = (
        select(Message.conversation_id, _func.count(Message.id).label("msg_count"))
        .group_by(Message.conversation_id)
        .subquery()
    )

    where = [Conversation.is_private == True]  # noqa: E712
    if user_id:
        where.append(Conversation.user_id == user_id)
    if tenant_id:
        where.append(Conversation.tenant_id == tenant_id)

    result = await db.execute(
        select(Conversation, msg_count_subq.c.msg_count)
        .outerjoin(msg_count_subq, Conversation.id == msg_count_subq.c.conversation_id)
        .where(*where)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.all()
    return [
        ConversationResponse(
            id=conv.id,
            title=conv.title,
            model=conv.model,
            system_prompt=conv.system_prompt,
            is_archived=conv.is_archived,
            is_private=conv.is_private,
            private_expires_at=conv.private_expires_at,
            message_count=int(msg_count or 0),
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )
        for conv, msg_count in rows
    ]


@router.get("/private-conversations/{conversation_id}")
async def get_private_conversation(
    conversation_id: str,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full detail of a private conversation including messages (admin only)."""
    tenant_id = _scoped_tenant_id(current_user)
    from app.services.chat_service import ChatService
    from app.schemas.chat import ConversationDetail

    svc = ChatService()
    detail = await svc.get_conversation_detail(
        db, conversation_id, user_id="", admin_override=True
    )
    if not detail:
        raise HTTPException(status_code=404, detail="Private conversation not found")

    conv, messages = detail
    if not conv.is_private:
        raise HTTPException(status_code=403, detail="Conversation is not private")
    if tenant_id and conv.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Private conversation not found")

    from app.schemas.chat import ChatMessage, MessageRole
    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        model=conv.model,
        system_prompt=conv.system_prompt,
        is_archived=conv.is_archived,
        is_private=conv.is_private,
        private_expires_at=conv.private_expires_at,
        message_count=len(messages),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[
            ChatMessage(
                role=MessageRole(m.role),
                content=m.content,
                created_at=m.created_at.isoformat() if m.created_at else None,
            )
            for m in messages
        ],
    )


@router.delete("/private-conversations/{conversation_id}")
async def delete_private_conversation(
    conversation_id: str,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Force-delete a private conversation before its 20-day expiry (admin only)."""
    tenant_id = _scoped_tenant_id(current_user)
    where = [
        Conversation.id == conversation_id,
        Conversation.is_private == True,  # noqa: E712
    ]
    if tenant_id:
        where.append(Conversation.tenant_id == tenant_id)
    result = await db.execute(
        select(Conversation).where(*where)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Private conversation not found")
    await db.delete(conv)
    await db.commit()
    return {"message": "Private conversation deleted"}


@router.get("/bootstrap-list")
async def get_bootstrap_list(
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the current BOOTSTRAP_ADMIN_EMAILS config list alongside the DB
    state of each listed user (role, whether elevation has already fired).
    Read-only — the list itself is controlled via the env var.
    """
    _require_global_admin(current_user)
    from app.core.config import settings as _s
    from app.models.models import UserRole

    rows = []
    for email in _s.bootstrap_admin_email_list:
        result = await db.execute(
            select(User).where(User.email == email)
        )
        u = result.scalar_one_or_none()
        rows.append({
            "email": email,
            "in_db": u is not None,
            "user_id": u.id if u else None,
            "current_role": u.role if u else None,
            "is_admin": (u.role == UserRole.ADMIN) if u else False,
            "bootstrap_elevated_at": (
                u.bootstrap_elevated_at.isoformat() if u and u.bootstrap_elevated_at else None
            ),
        })
    return {"bootstrap_admins": rows}


@router.get("/token-usage")
async def get_token_usage(
    days: int = Query(default=7, ge=1, le=90),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Per-user token consumption for the last N days, sorted by total tokens desc.
    Includes today's live counter from users.tokens_used_today.
    """
    start = datetime.utcnow() - timedelta(days=days)
    tenant_id = _scoped_tenant_id(current_user)

    join_conditions = [
        ModelUsage.user_id == User.id,
        ModelUsage.created_at >= start,
    ]

    if tenant_id:
        tenant_conversation_ids = select(Conversation.id).where(
            Conversation.tenant_id == tenant_id
        )
        join_conditions.append(
            ModelUsage.conversation_id.in_(tenant_conversation_ids)
        )

    usage_query = (
        select(
            User.id,
            User.name,
            User.email,
            User.role,
            User.daily_token_limit,
            User.tokens_used_today,
            func.coalesce(func.sum(ModelUsage.total_tokens), 0).label("period_tokens"),
            func.coalesce(func.sum(ModelUsage.prompt_tokens), 0).label("period_prompt"),
            func.coalesce(func.sum(ModelUsage.completion_tokens), 0).label("period_completion"),
            func.count(ModelUsage.id).label("period_requests"),
        )
        .outerjoin(ModelUsage, and_(*join_conditions))
        .where(User.is_active == True)  # noqa: E712
        .group_by(
            User.id,
            User.name,
            User.email,
            User.role,
            User.daily_token_limit,
            User.tokens_used_today,
        )
        .order_by(func.coalesce(func.sum(ModelUsage.total_tokens), 0).desc())
    )
    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        usage_query = usage_query.where(
            User.id.in_(select(tenant_user_ids.c.user_id))
        )

    usage_result = await db.execute(usage_query)

    rows = []
    for r in usage_result.all():
        rows.append({
            "user_id": r.id,
            "name": r.name,
            "email": r.email,
            "role": r.role,
            "daily_token_limit": r.daily_token_limit,
            "tokens_used_today": r.tokens_used_today,
            "period_tokens": r.period_tokens,
            "period_prompt_tokens": r.period_prompt,
            "period_completion_tokens": r.period_completion,
            "period_requests": r.period_requests,
            "pct_daily_limit_used": (
                round(r.tokens_used_today / r.daily_token_limit * 100, 1)
                if r.daily_token_limit else 0
            ),
        })
    return {"days": days, "users": rows}


@router.get("/audit-logs/bootstrap", response_model=List[AuditLogResponse])
async def get_bootstrap_audit_logs(
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all bootstrap_admin_elevation audit log entries."""
    tenant_id = _scoped_tenant_id(current_user)
    query = select(AuditLog).where(AuditLog.action == "bootstrap_admin_elevation")
    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        query = query.where(
            AuditLog.user_id.in_(select(tenant_user_ids.c.user_id))
        )
    result = await db.execute(query.order_by(AuditLog.created_at.desc()))
    return [AuditLogResponse.model_validate(r) for r in result.scalars().all()]


@router.get("/org-settings", response_model=OrgSettings)
async def get_org_settings(
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get organization-level settings (admin only)."""
    _require_global_admin(current_user)
    defaults = OrgSettings()
    try:
        row = await db.scalar(
            select(SystemSettings).where(SystemSettings.key == _ORG_SETTINGS_KEY)
        )
        if row:
            merged = {**defaults.model_dump(), **_json.loads(row.value)}
            return OrgSettings(**merged)
    except Exception:
        pass
    return defaults


@router.get("/monitoring")
async def get_monitoring_data(
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Real-time monitoring snapshot: active sessions, token burn, error rate, model health."""
    import sys
    now = datetime.utcnow()
    last_hour = now - timedelta(hours=1)
    last_24h = now - timedelta(hours=24)
    tenant_id = _scoped_tenant_id(current_user)
    tenant_user_ids = (
        _tenant_user_ids_subquery(tenant_id) if tenant_id else None
    )
    tenant_user_ids_select = (
        select(tenant_user_ids.c.user_id) if tenant_user_ids is not None else None
    )
    tenant_conversation_ids = (
        select(Conversation.id).where(Conversation.tenant_id == tenant_id)
        if tenant_id
        else None
    )

    active_1h_query = (
        select(func.count(func.distinct(Message.conversation_id)))
        .select_from(Message)
        .where(Message.created_at >= last_hour)
    )
    if tenant_conversation_ids is not None:
        active_1h_query = active_1h_query.where(
            Message.conversation_id.in_(tenant_conversation_ids)
        )
    active_1h = await db.scalar(active_1h_query) or 0

    messages_1h_query = select(func.count(Message.id)).where(
        Message.created_at >= last_hour
    )
    messages_24h_query = select(func.count(Message.id)).where(
        Message.created_at >= last_24h
    )
    if tenant_conversation_ids is not None:
        messages_1h_query = messages_1h_query.where(
            Message.conversation_id.in_(tenant_conversation_ids)
        )
        messages_24h_query = messages_24h_query.where(
            Message.conversation_id.in_(tenant_conversation_ids)
        )
    messages_1h = await db.scalar(messages_1h_query) or 0
    messages_24h = await db.scalar(messages_24h_query) or 0

    tokens_1h_query = select(func.sum(ModelUsage.total_tokens)).where(
        ModelUsage.created_at >= last_hour
    )
    tokens_24h_query = select(func.sum(ModelUsage.total_tokens)).where(
        ModelUsage.created_at >= last_24h
    )
    if tenant_conversation_ids is not None:
        tokens_1h_query = tokens_1h_query.where(
            ModelUsage.conversation_id.in_(tenant_conversation_ids)
        )
        tokens_24h_query = tokens_24h_query.where(
            ModelUsage.conversation_id.in_(tenant_conversation_ids)
        )
    tokens_1h = await db.scalar(tokens_1h_query) or 0
    tokens_24h = await db.scalar(tokens_24h_query) or 0

    errors_24h_query = select(func.count(AuditLog.id)).where(
        AuditLog.created_at >= last_24h,
        AuditLog.success == False,  # noqa: E712
    )
    total_audit_24h_query = select(func.count(AuditLog.id)).where(
        AuditLog.created_at >= last_24h
    )
    if tenant_user_ids_select is not None:
        errors_24h_query = errors_24h_query.where(
            AuditLog.user_id.in_(tenant_user_ids_select)
        )
        total_audit_24h_query = total_audit_24h_query.where(
            AuditLog.user_id.in_(tenant_user_ids_select)
        )
    errors_24h = await db.scalar(errors_24h_query) or 0
    total_audit_24h = await db.scalar(total_audit_24h_query) or 0
    error_rate = round(errors_24h / total_audit_24h * 100, 1) if total_audit_24h else 0.0

    model_query = (
        select(
            ModelUsage.model,
            func.count(ModelUsage.id).label("req_count"),
            func.sum(ModelUsage.total_tokens).label("tokens"),
        )
        .where(ModelUsage.created_at >= last_24h)
        .group_by(ModelUsage.model)
        .order_by(func.count(ModelUsage.id).desc())
    )
    if tenant_conversation_ids is not None:
        model_query = model_query.where(
            ModelUsage.conversation_id.in_(tenant_conversation_ids)
        )
    model_result = await db.execute(model_query)
    model_health = [
        {"model": r.model, "requests_24h": r.req_count, "tokens_24h": r.tokens or 0}
        for r in model_result.all()
    ]

    recent_errors_query = (
        select(AuditLog)
        .where(AuditLog.success == False, AuditLog.created_at >= last_24h)  # noqa: E712
        .order_by(AuditLog.created_at.desc())
        .limit(10)
    )
    if tenant_user_ids_select is not None:
        recent_errors_query = recent_errors_query.where(
            AuditLog.user_id.in_(tenant_user_ids_select)
        )
    recent_errors_result = await db.execute(recent_errors_query)
    recent_errors = [
        {"id": e.id, "user_id": e.user_id, "action": e.action,
         "resource_type": e.resource_type, "created_at": e.created_at.isoformat()}
        for e in recent_errors_result.scalars().all()
    ]

    if tenant_user_ids is not None:
        total_users = await db.scalar(
            select(func.count()).select_from(tenant_user_ids)
        ) or 0
        active_users = await db.scalar(
            select(func.count(User.id)).where(
                User.id.in_(tenant_user_ids_select),
                User.is_active == True,  # noqa: E712
            )
        ) or 0
    else:
        total_users = await db.scalar(select(func.count(User.id))) or 0
        active_users = await db.scalar(
            select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
        ) or 0

    return {
        "timestamp": now.isoformat(),
        "db_status": "ok",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "users": {"total": total_users, "active": active_users},
        "activity": {
            "active_sessions_1h": active_1h,
            "messages_1h": messages_1h,
            "messages_24h": messages_24h,
            "tokens_1h": tokens_1h,
            "tokens_24h": tokens_24h,
        },
        "quality": {
            "error_rate_pct": error_rate,
            "errors_24h": errors_24h,
            "total_audit_24h": total_audit_24h,
        },
        "model_health": model_health,
        "recent_errors": recent_errors,
    }


@router.get("/alerts/config")
async def get_alert_config(
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """Show effective ops alert routing configuration (global admin only)."""
    _require_global_admin(current_user)
    from app.core.config import settings

    recipients = list(settings.ALERT_RECIPIENTS)
    if "edgar.mcochieng@armely.com" not in recipients:
        recipients.append("edgar.mcochieng@armely.com")

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "recipients": recipients,
        "channels": list(settings.ALERT_CHANNELS),
        "acs_configured": bool(settings.ACS_CONNECTION_STRING),
        "acs_sender": settings.ACS_SENDER_ADDRESS,
        "teams_configured": bool(settings.TEAMS_WEBHOOK_URL),
        "cooldown_seconds": settings.ALERT_COOLDOWN_SECONDS,
        "max_retries": settings.ALERT_MAX_RETRIES,
        "ai_confidence_threshold": settings.ALERT_CONFIDENCE_THRESHOLD,
    }


@router.get("/alerts/status")
async def get_alert_status(
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """Active alert cooldowns from Redis (global admin only)."""
    _require_global_admin(current_user)
    cooldowns: list[dict] = []
    try:
        from app.core.redis_client import get_redis
        redis = await get_redis()
        if redis is not None:
            async for key in redis.scan_iter("alert:cooldown:*"):
                k = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
                ttl = await redis.ttl(k)
                cooldowns.append({"key": k, "ttl_seconds": ttl})
    except Exception as exc:
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "redis_available": False,
            "error": str(exc),
            "active_cooldowns": [],
        }
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "redis_available": True,
        "active_cooldowns": cooldowns,
    }


@router.get("/alerts/recent")
async def get_recent_alert_deliveries(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent alert dispatch records from the alert_events table."""
    _require_global_admin(current_user)
    from sqlalchemy import select, desc
    from app.models.models import AlertEvent

    result = await db.execute(
        select(AlertEvent).order_by(desc(AlertEvent.created_at)).limit(limit)
    )
    rows = result.scalars().all()
    alerts = [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "incident_id": r.incident_id,
            "severity": r.severity,
            "code": r.code,
            "title": r.title,
            "route": r.route,
            "tenant_id": r.tenant_id,
            "channels_attempted": r.channels_attempted,
            "ai_triage_confidence": r.ai_triage_confidence,
        }
        for r in rows
    ]
    return {"count": len(alerts), "alerts": alerts}


@router.post("/alerts/test")
async def trigger_test_alert(
    payload: AlertTestRequest,
    request: Request,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a synthetic alert to verify ACS Email + Teams delivery paths."""
    _require_global_admin(current_user)

    severity = (payload.severity or "critical").strip().lower()
    if severity not in _VALID_ALERT_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail="severity must be one of: critical, error, warning, info",
        )

    from app.services.alert_service import send_alert, AlertIncident
    incident = AlertIncident(
        title=(payload.error_type or "ManualTestAlert")[:120],
        severity=severity,
        code="MANUAL_TEST",
        route=(payload.route or "POST /api/v1/admin/alerts/test")[:300],
        error_message=(payload.message or "Manual ops alert test")[:1000],
        stack_trace="manual_test_alert",
    )
    await send_alert(incident)

    from app.core.logging import log_security_event
    await log_security_event(
        db,
        user_id=current_user.id,
        action="ops_alert_test_triggered",
        event_type="security",
        resource_type="system",
        resource_id=current_user.id,
        details={
            "severity": severity,
            "incident_id": incident.id,
            "code": incident.code,
        },
        success=True,
        request=request,
    )
    await db.commit()

    return {
        "status": "ok",
        "incident_id": incident.id,
        "severity": severity,
        "note": "fire-and-forget — check /admin/alerts/recent or mailbox for delivery confirmation",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Enterprise Control-Plane Endpoints
# ─────────────────────────────────────────────────────────────────────────────

from app.models.models import ErrorLog, ModelQuotaPolicy
from app.services.billing_service import (
    get_model_policies,
    get_monthly_cost_summary,
    list_tenant_summaries,
    seed_default_rates,
)


@router.get("/tenants")
async def list_tenants(
    year: int = Query(default=0),
    month: int = Query(default=0),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-tenant usage + cost rollup for a billing month."""
    now = datetime.utcnow()
    y = year or now.year
    m = month or now.month
    tenant_id = _scoped_tenant_id(current_user)
    summaries = await list_tenant_summaries(db, y, m)
    if tenant_id:
        summaries = [s for s in summaries if s.get("tenant_id") == tenant_id]
    return {"year": y, "month": m, "tenants": summaries}


@router.get("/tenants/{tenant_id}")
async def get_tenant_detail(
    tenant_id: str,
    year: int = Query(default=0),
    month: int = Query(default=0),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Detailed cost breakdown for a single tenant."""
    scoped_tenant = _scoped_tenant_id(current_user)
    if scoped_tenant and tenant_id != scoped_tenant:
        raise HTTPException(status_code=403, detail="Cross-tenant access is not allowed")

    now = datetime.utcnow()
    y = year or now.year
    m = month or now.month
    summary = await get_monthly_cost_summary(db, y, m, tenant_id=tenant_id)

    # Count users in this tenant
    user_count = await db.scalar(
        select(func.count(func.distinct(Conversation.user_id))).where(
            Conversation.tenant_id == tenant_id
        )
    ) or 0
    conversation_count = await db.scalar(
        select(func.count(Conversation.id)).where(
            Conversation.tenant_id == tenant_id
        )
    ) or 0

    return {
        **summary,
        "user_count": user_count,
        "conversation_count": conversation_count,
    }


@router.get("/invoices/{tenant_id}/{year}/{month}")
async def get_invoice(
    tenant_id: str,
    year: int,
    month: int,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Downloadable invoice data for a tenant-month."""
    scoped_tenant = _scoped_tenant_id(current_user)
    if scoped_tenant and tenant_id != scoped_tenant:
        raise HTTPException(status_code=403, detail="Cross-tenant access is not allowed")

    summary = await get_monthly_cost_summary(db, year, month, tenant_id=tenant_id)
    return {
        "invoice": {
            "tenant_id": tenant_id,
            "period": f"{year}-{month:02d}",
            "generated_at": datetime.utcnow().isoformat(),
            **summary,
        }
    }


@router.get("/models/governance")
async def list_model_governance(
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all model quota policies (enable/disable, cost rates, limits)."""
    _require_global_admin(current_user)
    policies = await get_model_policies(db)
    return {
        "models": [
            {
                "id": p.id,
                "model_id": p.model_id,
                "display_name": p.display_name,
                "provider": p.provider,
                "is_enabled": p.is_enabled,
                "cost_rate_per_1k_tokens": p.cost_rate_per_1k_tokens,
                "daily_token_limit": p.daily_token_limit,
                "daily_request_limit": p.daily_request_limit,
                "updated_by": p.updated_by,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in policies
        ]
    }


@router.get("/models/health")
async def probe_model_health(
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """
    Probe each configured AI model with a minimal completion request.
    Returns per-model latency and status so the admin can see which deployments
    are live.  Non-blocking — failures are captured as error detail, not 500s.
    """
    _require_global_admin(current_user)
    import asyncio
    import time
    from app.services.openai_service import openai_service

    if openai_service is None:
        return {"checked": 0, "healthy": 0, "models": [], "error": "OpenAI service not configured"}

    async def _probe(model_id: str, cfg: dict) -> dict:
        start = time.monotonic()
        try:
            client = openai_service._get_client(model_id)
            kwargs: dict = {
                "model": cfg["deployment"],
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
            }
            if cfg.get("use_completion_tokens") or cfg.get("no_temperature"):
                # Older SDK versions don't accept max_completion_tokens as a
                # first-class kwarg — pass via extra_body to reach the backend.
                # Use 50 tokens minimum; some models reject values below ~10.
                kwargs["extra_body"] = {"max_completion_tokens": 50}
            else:
                kwargs["max_tokens"] = 1
                kwargs["temperature"] = 0
            await client.chat.completions.create(**kwargs)
            return {"model": model_id, "status": "ok", "latency_ms": round((time.monotonic() - start) * 1000)}
        except Exception as exc:
            return {
                "model": model_id,
                "status": "error",
                "latency_ms": round((time.monotonic() - start) * 1000),
                "error": f"{type(exc).__name__}: {exc}",
            }

    probes = [_probe(mid, cfg) for mid, cfg in openai_service.models.items()]
    results = await asyncio.gather(*probes, return_exceptions=False)
    ok = sum(1 for r in results if r["status"] == "ok")
    return {"checked": len(results), "healthy": ok, "models": results}


@router.put("/models/{model_id}/quota")
async def update_model_quota(
    model_id: str,
    data: dict,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update cost rate, enable/disable, and quota limits for a model."""
    _require_global_admin(current_user)
    await seed_default_rates(db)
    result = await db.execute(
        select(ModelQuotaPolicy).where(ModelQuotaPolicy.model_id == model_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Model policy not found")

    if "is_enabled" in data:
        policy.is_enabled = bool(data["is_enabled"])
    if "cost_rate_per_1k_tokens" in data:
        policy.cost_rate_per_1k_tokens = float(data["cost_rate_per_1k_tokens"])
    if "daily_token_limit" in data:
        policy.daily_token_limit = (
            int(data["daily_token_limit"]) if data["daily_token_limit"] else None
        )
    if "daily_request_limit" in data:
        policy.daily_request_limit = (
            int(data["daily_request_limit"])
            if data["daily_request_limit"]
            else None
        )
    policy.updated_by = current_user.id
    policy.updated_at = datetime.utcnow()
    await db.commit()

    # Audit
    db.add(AuditLog(
        user_id=current_user.id,
        action="model_quota_updated",
        resource_type="model_quota_policy",
        resource_id=policy.id,
        details=_json.dumps({"model_id": model_id, "changes": data}),
        success=True,
    ))
    await db.commit()

    return {
        "model_id": policy.model_id,
        "is_enabled": policy.is_enabled,
        "cost_rate_per_1k_tokens": policy.cost_rate_per_1k_tokens,
        "daily_token_limit": policy.daily_token_limit,
        "daily_request_limit": policy.daily_request_limit,
    }


@router.get("/errors")
async def list_error_logs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    severity: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    route: Optional[str] = Query(default=None),
    start_date: Optional[datetime] = Query(default=None),
    end_date: Optional[datetime] = Query(default=None),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List application error logs with optional filters."""
    scoped_tenant = _scoped_tenant_id(current_user)
    q = select(ErrorLog)
    if severity:
        q = q.where(ErrorLog.severity == severity)
    if user_id:
        q = q.where(ErrorLog.user_id == user_id)
    if scoped_tenant and tenant_id and tenant_id != scoped_tenant:
        raise HTTPException(status_code=403, detail="Cross-tenant access is not allowed")
    if scoped_tenant:
        q = q.where(ErrorLog.tenant_id == scoped_tenant)
    elif tenant_id:
        q = q.where(ErrorLog.tenant_id == tenant_id)
    if route:
        q = q.where(ErrorLog.route.contains(route))
    if start_date:
        q = q.where(ErrorLog.created_at >= start_date)
    if end_date:
        q = q.where(ErrorLog.created_at <= end_date)

    total = await db.scalar(
        select(func.count()).select_from(q.subquery())
    ) or 0
    q = q.order_by(ErrorLog.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    errors = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "errors": [
            {
                "id": e.id,
                "user_id": e.user_id,
                "user_email": e.user_email,
                "tenant_id": e.tenant_id,
                "method": e.method,
                "route": e.route,
                "status_code": e.status_code,
                "error_type": e.error_type,
                "message": e.message,
                "severity": e.severity,
                "request_id": e.request_id,
                "created_at": e.created_at.isoformat(),
            }
            for e in errors
        ],
    }


@router.get("/errors/{error_id}")
async def get_error_detail(
    error_id: str,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full error log entry including stack trace."""
    scoped_tenant = _scoped_tenant_id(current_user)
    q = select(ErrorLog).where(ErrorLog.id == error_id)
    if scoped_tenant:
        q = q.where(ErrorLog.tenant_id == scoped_tenant)
    e = await db.scalar(q)
    if not e:
        raise HTTPException(status_code=404, detail="Error log not found")
    return {
        "id": e.id,
        "user_id": e.user_id,
        "user_email": e.user_email,
        "tenant_id": e.tenant_id,
        "method": e.method,
        "route": e.route,
        "status_code": e.status_code,
        "error_type": e.error_type,
        "message": e.message,
        "stack_trace": e.stack_trace,
        "severity": e.severity,
        "request_id": e.request_id,
        "created_at": e.created_at.isoformat(),
    }


@router.get("/users/{user_id}/detail")
async def get_user_detail(
    user_id: str,
    days: int = Query(default=30, ge=1, le=365),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Extended user profile: usage stats, recent conversations, audit trail."""
    scoped_tenant = _scoped_tenant_id(current_user)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if scoped_tenant:
        in_scope = await db.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.user_id == user_id,
                Conversation.tenant_id == scoped_tenant,
            )
        )
        if not in_scope:
            raise HTTPException(status_code=404, detail="User not found")

    start = datetime.utcnow() - timedelta(days=days)

    token_query = select(
        func.coalesce(func.sum(ModelUsage.total_tokens), 0),
        func.count(ModelUsage.id),
    ).where(
        ModelUsage.user_id == user_id,
        ModelUsage.created_at >= start,
    )
    if scoped_tenant:
        token_query = token_query.where(
            ModelUsage.conversation_id.in_(
                select(Conversation.id).where(Conversation.tenant_id == scoped_tenant)
            )
        )

    token_result = await db.execute(token_query)
    total_tokens, total_requests = token_result.one()

    conv_count_query = select(func.count(Conversation.id)).where(
        Conversation.user_id == user_id,
        Conversation.created_at >= start,
    )
    if scoped_tenant:
        conv_count_query = conv_count_query.where(
            Conversation.tenant_id == scoped_tenant
        )
    conv_count = await db.scalar(conv_count_query) or 0

    recent_convs_query = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(5)
    )
    if scoped_tenant:
        recent_convs_query = recent_convs_query.where(
            Conversation.tenant_id == scoped_tenant
        )
    recent_convs_result = await db.execute(recent_convs_query)
    recent_convs = [
        {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}
        for c in recent_convs_result.scalars().all()
    ]

    audit_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.user_id == user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
    )
    recent_audit = [
        {
            "action": a.action,
            "resource_type": a.resource_type,
            "success": a.success,
            "created_at": a.created_at.isoformat(),
        }
        for a in audit_result.scalars().all()
    ]

    from app.services.billing_service import get_cost_rates, calculate_cost
    rates = await get_cost_rates(db)
    estimated_cost = calculate_cost(total_tokens, "gpt-4.1", rates)

    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active,
            "daily_token_limit": user.daily_token_limit,
            "tokens_used_today": user.tokens_used_today,
            "created_at": user.created_at.isoformat(),
        },
        "period_days": days,
        "usage": {
            "total_tokens": total_tokens,
            "total_requests": total_requests,
            "conversations": conv_count,
            "estimated_cost_usd": round(estimated_cost, 4),
        },
        "recent_conversations": recent_convs,
        "recent_audit": recent_audit,
    }


# ── Index Health & Vector Backfill ────────────────────────────────────────────

@router.get("/index/health")
async def index_vector_health(
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """Report how many indexed chunks are missing their content_vector.

    A non-zero count means hybrid search will silently degrade to keyword-only
    for those documents.  Use POST /admin/index/reindex to backfill.
    """
    _require_global_admin(current_user)
    try:
        from app.services.search.index_manager import index_manager
        from app.core.config import settings as _settings

        if index_manager is None:
            raise HTTPException(status_code=503, detail="Azure AI Search not configured")

        idx = _settings.AZURE_SEARCH_INDEX_NAME
        stats = index_manager.get_index_stats(idx)

        # Scan for docs that have an empty content_vector.
        # Azure AI Search supports OData filter on collection fields — an empty
        # collection satisfies "not content_vector/any()".
        missing_rows = index_manager.search(
            index_name=idx,
            query="*",
            vector=None,
            filter_expr="not content_vector/any()",
            top=1000,
            select=["id", "source_type", "title"],
        )
        by_source: dict = {}
        for row in missing_rows:
            st = row.get("source_type", "unknown")
            by_source[st] = by_source.get(st, 0) + 1

        return {
            "index_name": idx,
            "total_documents": stats.get("document_count"),
            "missing_vectors": len(missing_rows),
            "missing_by_source_type": by_source,
            "status": "ok" if len(missing_rows) == 0 else "degraded",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("index/health error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/index/reindex")
async def reindex_missing_vectors(
    batch_size: int = Query(default=16, ge=1, le=64),
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """Backfill missing content_vector for all indexed chunks.

    Pages through documents with empty vectors in batches, calls the embedding
    service, and upserts only the vector field back into the index.
    Non-blocking: runs in the request context and streams a summary on completion.
    For very large indexes, call this endpoint repeatedly until missing_vectors == 0.
    """
    _require_global_admin(current_user)
    try:
        from app.services.search.index_manager import index_manager
        from app.services.openai_service import openai_service
        from app.core.config import settings as _settings

        if index_manager is None:
            raise HTTPException(status_code=503, detail="Azure AI Search not configured")
        if openai_service is None:
            raise HTTPException(status_code=503, detail="OpenAI service not configured")

        idx = _settings.AZURE_SEARCH_INDEX_NAME

        # Fetch up to 1000 missing-vector chunks
        missing_rows = index_manager.search(
            index_name=idx,
            query="*",
            vector=None,
            filter_expr="not content_vector/any()",
            top=min(batch_size * 10, 1000),
            select=["id", "content", "source_type"],
        )

        if not missing_rows:
            return {"status": "ok", "message": "No missing vectors found", "backfilled": 0}

        total_backfilled = 0
        total_failed = 0
        # Process in batches for efficiency
        for i in range(0, len(missing_rows), batch_size):
            batch = missing_rows[i : i + batch_size]
            texts = [r.get("content", "") for r in batch]
            try:
                vectors = await openai_service.create_embeddings(texts)
            except Exception as emb_exc:
                logger.error("Embedding batch %d failed during reindex: %s", i, emb_exc)
                total_failed += len(batch)
                continue

            upsert_docs = []
            for row, vector in zip(batch, vectors):
                if vector:
                    upsert_docs.append({"id": row["id"], "content_vector": vector})
                else:
                    total_failed += 1
                    logger.warning("Reindex: empty vector returned for chunk %s", row["id"])

            if upsert_docs:
                count = index_manager.upsert_documents(idx, upsert_docs)
                total_backfilled += count
                logger.info(
                    "Reindex batch %d/%d: backfilled %d chunks",
                    i // batch_size + 1, (len(missing_rows) + batch_size - 1) // batch_size, count,
                )

        return {
            "status": "ok" if total_failed == 0 else "partial",
            "scanned": len(missing_rows),
            "backfilled": total_backfilled,
            "failed": total_failed,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("index/reindex error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/connectors/acl-status")
async def get_acl_status(
    current_user: UserInfo = Depends(get_current_admin_user),
    stale_hours: int = 24,
):
    """Return ACL freshness statistics per source type.

    Documents whose acl_last_refreshed is older than `stale_hours` (default 24h)
    or is missing entirely are considered stale.  Use this to audit whether the
    ACL refresh job has run recently.
    """
    _require_global_admin(current_user)
    try:
        from app.services.search.index_manager import index_manager
        from datetime import datetime, timezone, timedelta

        idx = index_manager.index_name
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=stale_hours)).isoformat()

        # Docs with no acl_last_refreshed field
        never_refreshed = index_manager.search(
            idx, "*",
            vector=None,
            filter_expr="not acl_last_refreshed/any()",
            top=1000,
            select=["id", "source_type"],
        )
        # Docs where acl_last_refreshed < cutoff
        stale = index_manager.search(
            idx, "*",
            vector=None,
            filter_expr=f"acl_last_refreshed lt '{cutoff}'",
            top=1000,
            select=["id", "source_type"],
        )

        never_by_type: dict = {}
        for r in (never_refreshed or []):
            st = r.get("source_type", "unknown")
            never_by_type[st] = never_by_type.get(st, 0) + 1

        stale_by_type: dict = {}
        for r in (stale or []):
            st = r.get("source_type", "unknown")
            stale_by_type[st] = stale_by_type.get(st, 0) + 1

        return {
            "stale_threshold_hours": stale_hours,
            "cutoff_utc": cutoff,
            "never_refreshed_count": len(never_refreshed or []),
            "never_refreshed_by_source_type": never_by_type,
            "stale_count": len(stale or []),
            "stale_by_source_type": stale_by_type,
            "status": "ok" if not (never_refreshed or stale) else "stale_acls_found",
        }
    except Exception as exc:
        logger.error("acl-status error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/org-settings", response_model=OrgSettings)
async def update_org_settings(
    data: OrgSettings,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update organization-level settings (admin only)."""
    _require_global_admin(current_user)
    row = await db.scalar(
        select(SystemSettings).where(SystemSettings.key == _ORG_SETTINGS_KEY)
    )
    value = data.model_dump_json()
    if row:
        row.value = value
        row.updated_by = current_user.id
        row.updated_at = datetime.utcnow()
    else:
        db.add(SystemSettings(
            key=_ORG_SETTINGS_KEY,
            value=value,
            description="Organization-level feature settings",
            updated_by=current_user.id,
        ))
    await db.commit()


# ── Admin access request ──────────────────────────────────────────────────────

@router.post("/request-access")
async def request_admin_access(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Let a non-admin user request admin access.
    Sends an email notification to all bootstrap admin addresses.
    Always returns 200 so the caller can't enumerate whether the email was delivered.
    """
    from app.core.config import settings as _settings
    admin_emails = _settings.bootstrap_admin_email_list
    sender_email = getattr(_settings, "GRAPH_SENDER_EMAIL", None) or (
        admin_emails[0] if admin_emails else None
    )

    subject = f"[Mela AI] Admin access requested by {current_user.email}"
    body = (
        f"<h3>Admin Access Request</h3>"
        f"<p><strong>{current_user.name or current_user.email}</strong> "
        f"(<a href='mailto:{current_user.email}'>{current_user.email}</a>) "
        f"has requested administrator privileges in Mela AI.</p>"
        f"<p>To grant access, log into the "
        f"<a href='/admin'>Admin Console</a> → Users → find the user → "
        f"Edit → set Role to <em>Admin</em>.</p>"
        f"<p style='color:#888;font-size:12px;'>Tenant: {current_user.tenant_id or 'n/a'} · "
        f"User ID: {current_user.id}</p>"
    )

    if admin_emails and sender_email:
        try:
            from app.services.graph_service import GraphAPIService
            gs = GraphAPIService()
            await gs.send_email_app_only(
                sender_email=sender_email,
                to=admin_emails,
                subject=subject,
                body=body,
                is_html=True,
            )
            logger.info(
                "Admin access request email sent for %s → %s",
                current_user.email,
                admin_emails,
            )
        except Exception:
            logger.exception(
                "Failed to send admin access request email for %s", current_user.email
            )

    # Audit log — look up DB user to get the correct UUID primary key.
    # current_user.id is the Entra oid; AuditLog.user_id FKs to users.id (UUID4).
    try:
        from app.models.models import User as _UserModel
        _result = await db.execute(
            select(_UserModel).where(_UserModel.azure_id == current_user.id)
        )
        _db_user = _result.scalar_one_or_none()
        if _db_user:
            db.add(AuditLog(
                user_id=_db_user.id,
                action="admin_access_requested",
                resource_type="user",
                resource_id=_db_user.id,
                details={"email": current_user.email, "name": current_user.name},
                success=True,
            ))
            await db.commit()
    except Exception:
        logger.exception("Failed to write audit log for admin access request by %s", current_user.email)

    return {"requested": True}


@router.get("/access-requests")
async def list_access_requests(
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return all users who have requested admin access and are NOT yet admins.
    Used by the admin panel to show a pending-promotions badge and quick-approve list.
    """
    from app.models.models import User as UserModel, UserRole
    scoped_tenant = _scoped_tenant_id(current_user)

    # Latest request timestamp per user
    subq = (
        select(
            AuditLog.user_id,
            func.max(AuditLog.created_at).label("requested_at"),
        )
        .where(AuditLog.action == "admin_access_requested")
        .group_by(AuditLog.user_id)
        .subquery()
    )

    query = (
        select(UserModel, subq.c.requested_at)
        .join(subq, UserModel.id == subq.c.user_id)
        .where(UserModel.role != UserRole.ADMIN)
        .order_by(subq.c.requested_at.desc())
    )
    if scoped_tenant:
        tenant_user_ids = _tenant_user_ids_subquery(scoped_tenant)
        query = query.where(UserModel.id.in_(select(tenant_user_ids.c.user_id)))

    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "user_id": u.id,
            "email": u.email,
            "name": u.name,
            "requested_at": ra.isoformat() + "Z" if ra else None,
        }
        for u, ra in rows
    ]


# ── Employee onboarding ───────────────────────────────────────────────────────

class OnboardRequest(BaseModel):
    new_user_email: str
    new_user_name: str
    department: Optional[str] = None
    manager_email: Optional[str] = None
    send_welcome_email: bool = True
    schedule_orientation: bool = True
    create_tasks: bool = True


@router.post("/onboard")
async def trigger_onboarding(
    body: OnboardRequest,
    http_request: Request,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin endpoint: trigger the full automated onboarding workflow for a new hire.
    Sends welcome email, schedules orientation, creates Planner tasks.
    All steps are best-effort — partial success is returned, not a 500.
    """
    from app.services.onboarding_service import run_onboarding
    access_token = getattr(http_request.state, "access_token", None)

    # Translate legacy flat fields to the new payload-dict signature
    payload = {
        "first_name": body.new_user_name.split()[0] if body.new_user_name else body.new_user_name,
        "last_name": " ".join(body.new_user_name.split()[1:]) if body.new_user_name and len(body.new_user_name.split()) > 1 else "",
        "display_name": body.new_user_name,
        "upn": body.new_user_email,
        "mail_nickname": body.new_user_email.split("@")[0] if "@" in body.new_user_email else body.new_user_email,
        "work_email": body.new_user_email,
        "department": body.department,
        "manager_email": body.manager_email,
        "send_welcome_email": body.send_welcome_email,
        "schedule_orientation": body.schedule_orientation,
        "create_tasks": body.create_tasks,
    }
    result = await run_onboarding(
        db,
        payload=payload,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        access_token=access_token,
    )
    return result


@router.get("/onboarding-logs")
async def list_onboarding_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent onboarding workflow runs."""
    from app.models import OnboardingLog
    from sqlalchemy import select, func

    tenant_id = _scoped_tenant_id(current_user)
    query = select(OnboardingLog)
    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        query = query.where(
            OnboardingLog.initiated_by.in_(select(tenant_user_ids.c.user_id))
        )

    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    result = await db.execute(
        query
        .order_by(OnboardingLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    logs = result.scalars().all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "logs": [
            {
                "id": row.id,
                "new_user_email": row.new_user_email,
                "new_user_name": row.new_user_name,
                "department": row.department,
                "manager_email": row.manager_email,
                "initiated_by_email": row.initiated_by_email,
                "status": row.status,
                "steps_completed": _json.loads(row.steps_completed),
                "steps_failed": _json.loads(row.steps_failed),
                "created_at": row.created_at.isoformat(),
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }
            for row in logs
        ],
    }


# ── HR Workflow: Onboarding (structured, admin-only) ──────────────────────────

class OnboardingPayload(BaseModel):
    # Identity fields
    first_name: str
    last_name: str
    display_name: str
    upn: str                              # user principal name e.g. jsmith@armely.com
    mail_nickname: str
    work_email: Optional[str] = None
    # Profile
    department: Optional[str] = None
    job_title: Optional[str] = None
    manager_email: Optional[str] = None
    usage_location: str = "US"
    # Access
    group_ids: Optional[List[str]] = None
    sku_ids: Optional[List[str]] = None   # license SKU IDs
    # Actions
    schedule_orientation: bool = True
    orientation_datetime: Optional[str] = None
    send_welcome_email: bool = True
    welcome_recipients: Optional[List[str]] = None
    create_tasks: bool = True
    # Meta
    notes: Optional[str] = None
    approval_reference: Optional[str] = None


@router.post("/onboarding/preview")
async def onboarding_preview(
    body: OnboardingPayload,
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """Validate the onboarding payload and return a preview summary without executing."""
    from app.services.onboarding_service import build_onboarding_preview
    return await build_onboarding_preview(body.model_dump())


@router.post("/onboarding/execute")
async def onboarding_execute(
    body: OnboardingPayload,
    http_request: Request,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute the full onboarding workflow. Returns structured step-by-step result."""
    from app.services.onboarding_service import run_onboarding
    access_token = getattr(http_request.state, "access_token", None)
    return await run_onboarding(
        db,
        payload=body.model_dump(),
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        access_token=access_token,
    )


@router.get("/onboarding/runs")
async def list_onboarding_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List onboarding workflow runs."""
    from app.models import HRWorkflowRun
    from sqlalchemy import func, select

    tenant_id = _scoped_tenant_id(current_user)
    query = select(HRWorkflowRun).where(HRWorkflowRun.workflow_type == "onboarding")
    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        query = query.where(
            HRWorkflowRun.actor_user_id.in_(select(tenant_user_ids.c.user_id))
        )

    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    result = await db.execute(
        query
        .order_by(HRWorkflowRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    runs = result.scalars().all()
    return {
        "total": total,
        "runs": [_serialize_hr_run(r) for r in runs],
    }


@router.get("/onboarding/runs/{run_id}")
async def get_onboarding_run(
    run_id: str,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full detail for a specific onboarding run."""
    from app.models import HRWorkflowRun
    from sqlalchemy import select

    tenant_id = _scoped_tenant_id(current_user)
    query = select(HRWorkflowRun).where(HRWorkflowRun.id == run_id)
    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        query = query.where(
            HRWorkflowRun.actor_user_id.in_(select(tenant_user_ids.c.user_id))
        )

    result = await db.execute(query)
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    return _serialize_hr_run(run, full=True)


@router.get("/onboarding/groups")
async def list_onboarding_groups(
    search: Optional[str] = Query(default=None),
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """List available groups from Entra for the onboarding group picker."""
    from app.services.graph_service import GraphAPIService
    gs = GraphAPIService()
    groups = await gs.list_groups_app(search=search)
    return {"groups": [{"id": g.get("id"), "displayName": g.get("displayName", "?"),
                        "description": g.get("description", ""), "mail": g.get("mail")} for g in groups]}


@router.get("/onboarding/licenses")
async def list_onboarding_licenses(
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """List available license SKUs from Entra for the onboarding license picker."""
    from app.services.graph_service import GraphAPIService
    gs = GraphAPIService()
    skus = await gs.list_subscribed_skus()
    return {"licenses": [{"skuId": s.get("skuId"), "skuPartNumber": s.get("skuPartNumber", "?"),
                          "consumedUnits": s.get("consumedUnits", 0),
                          "prepaidUnits": s.get("prepaidUnits", {}).get("enabled", 0)} for s in skus]}


# ── HR Workflow: Offboarding (structured, admin-only) ─────────────────────────

class OffboardingPayload(BaseModel):
    target_email: str
    reason: Optional[str] = None
    effective_date: Optional[str] = None
    disable_sign_in: bool = True
    revoke_sessions: bool = True
    remove_licenses: bool = True
    remove_groups: bool = True
    send_notifications: bool = False
    notification_recipients: Optional[List[str]] = None
    delete_account: bool = False
    confirm_delete: bool = False
    confirm_delete_second: bool = False
    approval_reference: Optional[str] = None


@router.post("/offboarding/preview")
async def offboarding_preview(
    body: OffboardingPayload,
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """Resolve target user and return a dry-run summary without executing."""
    from app.services.offboarding_service import build_offboarding_preview
    return await build_offboarding_preview(body.model_dump())


@router.post("/offboarding/execute")
async def offboarding_execute(
    body: OffboardingPayload,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute the offboarding workflow. Delete requires double confirmation."""
    if body.delete_account and not (body.confirm_delete and body.confirm_delete_second):
        raise HTTPException(400, "Account deletion requires confirm_delete=true AND confirm_delete_second=true")
    from app.services.offboarding_service import run_offboarding
    return await run_offboarding(
        db,
        payload=body.model_dump(),
        actor_user_id=current_user.id,
        actor_email=current_user.email,
    )


@router.get("/offboarding/runs")
async def list_offboarding_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List offboarding workflow runs."""
    from app.models import HRWorkflowRun
    from sqlalchemy import func, select

    tenant_id = _scoped_tenant_id(current_user)
    query = select(HRWorkflowRun).where(HRWorkflowRun.workflow_type == "offboarding")
    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        query = query.where(
            HRWorkflowRun.actor_user_id.in_(select(tenant_user_ids.c.user_id))
        )

    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    result = await db.execute(
        query
        .order_by(HRWorkflowRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    runs = result.scalars().all()
    return {
        "total": total,
        "runs": [_serialize_hr_run(r) for r in runs],
    }


@router.get("/offboarding/runs/{run_id}")
async def get_offboarding_run(
    run_id: str,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full detail for a specific offboarding run."""
    from app.models import HRWorkflowRun
    from sqlalchemy import select

    tenant_id = _scoped_tenant_id(current_user)
    query = select(HRWorkflowRun).where(HRWorkflowRun.id == run_id)
    if tenant_id:
        tenant_user_ids = _tenant_user_ids_subquery(tenant_id)
        query = query.where(
            HRWorkflowRun.actor_user_id.in_(select(tenant_user_ids.c.user_id))
        )

    result = await db.execute(query)
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    return _serialize_hr_run(run, full=True)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _serialize_hr_run(run, full: bool = False) -> dict:
    base = {
        "id": run.id,
        "workflow_type": run.workflow_type,
        "actor_email": run.actor_email,
        "target_email": run.target_email,
        "target_upn": run.target_upn,
        "target_display_name": run.target_display_name,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "approval_reference": run.approval_reference,
        "error_summary": run.error_summary,
    }
    if full:
        base["step_results"] = _json.loads(run.step_results_json or "[]")
        base["payload"] = _json.loads(run.payload_json or "{}")
    return base


@router.get("/search/diagnostic")
async def search_diagnostic(
    current_user: UserInfo = Depends(get_current_admin_user),
):
    """
    Live diagnostic for enterprise search — tests AI Search and Graph connectivity.
    Returns index stats, query latency, and token status for ops debugging.
    """
    _require_global_admin(current_user)
    import time
    from app.core.config import settings as _s
    from app.services.obo_service import _cached_token, get_graph_token_app_only

    report: dict = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "search": {},
        "graph": {},
    }

    # --- Azure AI Search probe ---
    search_endpoint = _s.AZURE_SEARCH_ENDPOINT
    search_key = _s.AZURE_SEARCH_ADMIN_KEY
    report["search"]["endpoint"] = (search_endpoint[:40] + "...") if search_endpoint else None
    report["search"]["configured"] = bool(search_endpoint and search_key)

    if search_endpoint and search_key:
        try:
            from azure.search.documents import SearchClient
            from azure.core.credentials import AzureKeyCredential
            _t0 = time.monotonic()
            _sc = SearchClient(
                endpoint=search_endpoint,
                index_name=_s.AZURE_SEARCH_INDEX_NAME or "fileshare-documents",
                credential=AzureKeyCredential(search_key),
            )
            _results = list(_sc.search("*", top=1, include_total_count=True))
            _latency_ms = round((time.monotonic() - _t0) * 1000)
            report["search"]["status"] = "ok"
            report["search"]["latency_ms"] = _latency_ms
            report["search"]["doc_count"] = _sc.get_document_count() if hasattr(_sc, "get_document_count") else "n/a"
        except Exception as _e:
            report["search"]["status"] = f"error: {type(_e).__name__}: {str(_e)[:200]}"
    else:
        report["search"]["status"] = "not_configured"

    # --- Graph probe ---
    report["graph"]["token_cached"] = bool(_cached_token)
    report["graph"]["configured"] = bool(_s.effective_client_id and _s.effective_client_secret)

    if _s.effective_client_id and _s.effective_client_secret:
        try:
            import asyncio as _asyncio
            import httpx as _httpx
            _t0 = time.monotonic()
            _token = await _asyncio.wait_for(get_graph_token_app_only(), timeout=30)
            _latency_ms = round((time.monotonic() - _t0) * 1000)
            if _token:
                report["graph"]["token_status"] = "acquired"
                report["graph"]["token_latency_ms"] = _latency_ms
                # Quick search probe (30s timeout — first post-restart call can be slow)
                _search_body = {
                    "requests": [{
                        "entityTypes": ["driveItem"],
                        "query": {"queryString": "test"},
                        "region": "US",
                        "from": 0, "size": 1,
                    }]
                }
                _t1 = time.monotonic()
                async with _httpx.AsyncClient(timeout=30) as _hc:
                    _resp = await _hc.post(
                        "https://graph.microsoft.com/v1.0/search/query",
                        json=_search_body,
                        headers={"Authorization": f"Bearer {_token}", "Content-Type": "application/json"},
                    )
                _search_latency_ms = round((time.monotonic() - _t1) * 1000)
                if _resp.status_code == 200:
                    _hits = _resp.json().get("value", [{}])[0].get("hitsContainers", [{}])[0].get("total", 0)
                    report["graph"]["search_status"] = "ok"
                    report["graph"]["search_hit_count"] = _hits
                    report["graph"]["search_latency_ms"] = _search_latency_ms
                else:
                    report["graph"]["search_status"] = f"http_{_resp.status_code}"
                    report["graph"]["search_latency_ms"] = _search_latency_ms
            else:
                report["graph"]["token_status"] = "failed_to_acquire"
        except Exception as _e:
            report["graph"]["token_status"] = f"error: {type(_e).__name__}: {str(_e)[:200]}"
    else:
        report["graph"]["token_status"] = "not_configured"

    return report
