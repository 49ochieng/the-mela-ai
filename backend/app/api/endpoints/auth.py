"""
Mela AI - Authentication Endpoints
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import get_current_user, create_access_token
from app.core.config import settings
from app.models import User, AuditLog
from app.models.models import UserRole
from app.schemas.auth import UserInfo, UserResponse, LoginResponse

logger = logging.getLogger(__name__)
router = APIRouter()


class DevLoginRequest(BaseModel):
    """Dev login request."""
    username: str
    password: str


class DevLoginResponse(BaseModel):
    """Dev login response with token."""
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Process user login and return user profile.
    Creates user in database if first login.
    """
    # Check if user exists
    result = await db.execute(
        select(User).where(User.azure_id == current_user.id)
    )
    user = result.scalar_one_or_none()

    # Fallback: look up by email (case-insensitive) to handle dev→real account
    # migration and email-case differences between token and DB.
    if user is None and current_user.email:
        email_result = await db.execute(
            select(User).where(
                func.lower(User.email) == current_user.email.lower()
            )
        )
        email_user = email_result.scalar_one_or_none()
        if email_user is not None:
            email_user.azure_id = current_user.id
            user = email_user
            await db.flush()
            logger.info(
                "Migrated azure_id for %s to real OID %s",
                current_user.email, current_user.id,
            )

    if not user:
        # Create new user — role defaults to 'user'; bootstrap may promote below.
        user = User(
            azure_id=current_user.id,
            email=current_user.email,
            name=current_user.name,
            department=current_user.department,
            job_title=current_user.job_title,
            role=UserRole.USER,
        )
        db.add(user)
        try:
            await db.flush()
        except Exception as _ie:
            # UNIQUE constraint on email — race condition or case-sensitivity miss.
            # Rollback and re-fetch the existing row, then migrate azure_id.
            if "unique" in str(_ie).lower() or "integrity" in type(_ie).__name__.lower():
                await db.rollback()
                _retry = await db.execute(
                    select(User).where(
                        func.lower(User.email) == (current_user.email or "").lower()
                    )
                )
                existing = _retry.scalar_one_or_none()
                if existing is not None:
                    existing.azure_id = current_user.id
                    user = existing
                    logger.info(
                        "Resolved email conflict on login for %s → migrated to OID %s",
                        current_user.email, current_user.id,
                    )
                else:
                    raise
            else:
                raise
        logger.info(f"New user created: {user.email}")
        welcome = f"Welcome to Mela AI, {user.name}! This is your first time here."
    else:
        # Update mutable profile fields from the fresh token.
        user.name = current_user.name
        user.department = current_user.department
        user.job_title = current_user.job_title
        # Backfill email if it was empty on first login (e.g. Entra token lacked
        # preferred_username on the initial call but now has it).
        if not user.email and current_user.email:
            user.email = current_user.email
        user.updated_at = datetime.utcnow()

        # Reset daily token counter if the calendar date rolled over.
        if user.last_token_reset.date() < datetime.utcnow().date():
            user.tokens_used_today = 0
            user.last_token_reset = datetime.utcnow()

        welcome = f"Welcome back, {user.name}!"

    # ── Bootstrap admin elevation ────────────────────────────────────────────
    # Elevate if the user's email matches BOOTSTRAP_ADMIN_EMAILS OR their
    # Entra OID matches BOOTSTRAP_ADMIN_OIDS (useful when the access token
    # doesn't carry the email claim).
    bootstrap_emails = settings.bootstrap_admin_email_list
    bootstrap_oids = settings.bootstrap_admin_oid_list
    user_email_lower = (current_user.email or "").lower()
    user_oid_lower = (current_user.id or "").lower()
    _is_bootstrap = (
        (bootstrap_emails and user_email_lower and user_email_lower in bootstrap_emails)
        or (bootstrap_oids and user_oid_lower and user_oid_lower in bootstrap_oids)
    )
    if _is_bootstrap and user.role != UserRole.ADMIN:
        # Bootstrap emails are always admin — no bootstrap_elevated_at guard.
        user.role = UserRole.ADMIN
        user.bootstrap_elevated_at = user.bootstrap_elevated_at or datetime.utcnow()
        db.add(AuditLog(
            user_id=user.id,
            action="bootstrap_admin_elevation",
            resource_type="user",
            resource_id=user.id,
            details={
                "email": user.email,
                "source": "BOOTSTRAP_ADMIN_EMAILS",
            },
            success=True,
        ))
        logger.warning(
            "Bootstrap admin elevation granted to %s", user.email
        )

    await db.commit()

    # First-login bridge: if get_current_user could not create a session yet
    # (because no DB user row existed), create it now with the persisted user.id.
    if not getattr(request.state, "session_id", None):
        raw_token = getattr(request.state, "access_token", None)
        if raw_token:
            try:
                from app.core.sessions import derive_jti, get_or_create_session

                token_jti = getattr(request.state, "token_jti", None) or derive_jti(raw_token)
                token_exp = getattr(request.state, "token_exp", None)
                session_row = await get_or_create_session(
                    db,
                    user_id=user.id,
                    token_jti=token_jti,
                    token_exp=token_exp if isinstance(token_exp, datetime) else None,
                    ip_address=(request.client.host if request.client else None),
                    user_agent=(request.headers.get("user-agent") or "")[:500] or None,
                )
                request.state.session_id = session_row.id
                request.state.token_jti = token_jti
            except Exception as _sess_err:
                logger.warning("Session bootstrap on login failed: %s", _sess_err)

    # ── Enqueue OneDrive delta sync for this user (app-only, background) ─────
    # First login → full sync; subsequent logins → delta sync.
    # Non-fatal: if OneDrive is disabled or tokens aren't configured, skip.
    try:
        from app.core.config import settings as _settings
        if _settings.CONNECTOR_ONEDRIVE_ENABLED:
            from app.services.ingestion_worker import ingestion_worker as _iw, SyncJob as _SJ, JobType as _JT
            import uuid as _uuid
            _user_oid = current_user.id or ""
            if _user_oid:
                _delta_key = f"onedrive:{_user_oid}"
                _is_first_sync = _iw.get_delta_token(_delta_key) is None
                _iw.enqueue(_SJ(
                    id=str(_uuid.uuid4()),
                    job_type=_JT.FULL_SYNC if _is_first_sync else _JT.DELTA_SYNC,
                    connector_type="onedrive",
                    source_id=_delta_key,
                    workspace_id=_settings.effective_tenant_id or _user_oid,
                    context_type="personal",
                    user_id=_user_oid,
                ))
                logger.info(
                    "OneDrive %s sync queued for user %s on login",
                    "full" if _is_first_sync else "delta", _user_oid[:8],
                )
    except Exception as _od_err:
        logger.debug("OneDrive login sync skipped (non-fatal): %s", _od_err)

    return LoginResponse(
        user=UserResponse.model_validate(user),
        welcome_message=welcome,
        tenant_id=current_user.tenant_id,
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's profile."""
    result = await db.execute(
        select(User).where(User.azure_id == current_user.id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please login first.",
        )

    return UserResponse.model_validate(user)


@router.put("/me", response_model=UserResponse)
async def update_current_user(
    preferred_model: str = None,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user's preferences."""
    result = await db.execute(
        select(User).where(User.azure_id == current_user.id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if preferred_model:
        user.preferred_model = preferred_model

    user.updated_at = datetime.utcnow()
    await db.commit()

    return UserResponse.model_validate(user)


@router.post("/logout")
async def logout(
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the current server-side session and audit-log the logout."""
    from app.core.sessions import derive_jti, revoke_session_by_jti

    raw_token = getattr(request.state, "access_token", None)
    claim_jti = getattr(request.state, "token_jti", None) or getattr(
        request.state, "session_jti", None
    )
    revoked = 0
    if raw_token:
        token_jti = claim_jti or derive_jti(raw_token)
        try:
            revoked = await revoke_session_by_jti(db, token_jti)
        except Exception as e:
            logger.warning("logout: session revoke failed: %s", e)

    # Audit
    try:
        # Look up DB user id for FK
        from app.models import User as _User
        u_row = (
            await db.execute(select(_User).where(_User.azure_id == current_user.id))
        ).scalar_one_or_none()
        if u_row is not None:
            db.add(AuditLog(
                user_id=u_row.id,
                action="logout",
                event_type="auth",
                resource_type="session",
                resource_id=getattr(request.state, "session_id", None),
                details={"sessions_revoked": int(revoked)},
                ip_address=(request.client.host if request.client else None),
                user_agent=(request.headers.get("user-agent") or "")[:500] or None,
                success=True,
            ))
            await db.commit()
    except Exception as e:
        logger.debug("logout audit insert skipped: %s", e)

    logger.info("User logged out: %s (revoked=%s)", current_user.email, revoked)
    return {"message": "Logged out successfully", "revoked": int(revoked)}


@router.post("/dev-login", response_model=DevLoginResponse)
async def dev_login(request: DevLoginRequest):
    """
    Dev login for local testing — development mode only.
    Credentials are set via DEV_USERNAME / DEV_PASSWORD environment variables.
    """
    # Blocked unless ENABLE_DEV_LOGIN=true AND running in development mode.
    # To retire dev login: set ENABLE_DEV_LOGIN=false in env and redeploy.
    if not settings.ENABLE_DEV_LOGIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    if settings.APP_ENV != "development" and not settings.DEBUG:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev login is only available in development mode",
        )

    # Validate dev credentials (set DEV_USERNAME / DEV_PASSWORD in env)
    if request.username != settings.DEV_USERNAME or request.password != settings.DEV_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dev credentials.",
        )

    # Create a dev user token.
    # If BOOTSTRAP_ADMIN_EMAILS is set, use the first email so the dev user
    # identity matches the bootstrap admin and automatically has admin access.
    _bootstrap = settings.bootstrap_admin_email_list
    _dev_email = _bootstrap[0] if _bootstrap else "dev@mela-ai.local"
    dev_user = {
        "id": "dev-user-001",
        "email": _dev_email,
        "name": "Dev User",
        "given_name": "Dev",
        "family_name": "User",
        "roles": ["Admin", "user"],
        "department": "Development",
        "job_title": "Developer",
        "tenant_id": "dev-tenant",
    }

    # Create access token — 24-hour expiry for dev tokens (was 30d). The session
    # lifecycle middleware enforces 30-min idle + 12h absolute on top of this.
    access_token = create_access_token(
        data={
            "sub": dev_user["id"],
            "email": dev_user["email"],
            "name": dev_user["name"],
            "roles": dev_user["roles"],
            "is_dev": True,
        },
        expires_delta=timedelta(hours=24),
    )

    logger.info("Dev user logged in")

    return DevLoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=dev_user,
    )
