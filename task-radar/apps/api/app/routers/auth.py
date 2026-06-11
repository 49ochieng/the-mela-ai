"""Auth + Microsoft OAuth + /me endpoint."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session
from ..deps import RequestContext, get_current_user
from ..enums import ConnectionStatus
from ..models import GraphConnection, ScanSettings, Tenant, User
from ..schemas import MeResponse
from ..services.auth.entra import acquire_token_by_auth_code_flow, expires_at_from, initiate_auth_code_flow
from ..services.auth.token_store import StoredToken, get_token_store
from ..services.auth.sessions import issue_session, revoke_all_for_user, revoke_session
from ..services.auth.oauth_state import consume_state, put_state
from ..services.tasks.audit import log
from ..utils.jwt import decode_session_token

logger = logging.getLogger("app.auth")

router = APIRouter()


def _error_redirect(reason: str) -> RedirectResponse:
    """Redirect the browser back to the frontend /auth/error page with a safe message."""
    settings = get_settings()
    safe = quote(reason[:240], safe="")
    return RedirectResponse(f"{settings.frontend_url}/auth/error?reason={safe}", status_code=302)


@router.get("/me", response_model=MeResponse)
async def me(ctx: RequestContext = Depends(get_current_user)) -> MeResponse:
    return MeResponse.model_validate(ctx.user)


# Allowed timezones for the timezone selector. Restrict to common US zones
# the product targets to keep the UI compact and DST-aware.
_ALLOWED_TIMEZONES = {
    "America/Chicago",      # CT (CDT/CST)
    "America/New_York",     # ET (EDT/EST)
    "America/Los_Angeles",  # PT (PDT/PST)
    "America/Denver",       # MT (MDT/MST)
    "America/Phoenix",      # MST (no DST)
    "Pacific/Honolulu",     # HT
    "America/Anchorage",    # AKT
    "UTC",
}


@router.patch("/me", response_model=MeResponse)
async def update_me(
    payload: dict,
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    """Update mutable user profile fields (currently: timezone)."""
    user = await session.get(User, ctx.user.id)
    if user is None:
        raise HTTPException(404, "user not found")
    tz = payload.get("timezone")
    if tz is not None:
        if tz not in _ALLOWED_TIMEZONES:
            raise HTTPException(400, f"unsupported timezone: {tz}")
        user.timezone = tz
    await session.commit()
    await session.refresh(user)
    return MeResponse.model_validate(user)


@router.get("/auth/microsoft/login")
async def microsoft_login(
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    s = get_settings()
    try:
        flow = await asyncio.to_thread(initiate_auth_code_flow, s.microsoft_redirect_uri)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("auth.login.initiate_failed")
        return _error_redirect(f"Could not start Microsoft sign-in: {exc}")
    try:
        await put_state(session, flow["state"], flow)
    except Exception:
        logger.exception("auth.login.persist_failed")
        return _error_redirect("Could not save sign-in state. Please try again.")
    logger.info("auth.login.redirect state=%s redirect_uri=%s", flow["state"], s.microsoft_redirect_uri)
    return RedirectResponse(flow["auth_uri"])


@router.get("/auth/microsoft/callback")
async def microsoft_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    params = dict(request.query_params)
    state = params.get("state", "")
    has_code = bool(params.get("code"))
    logger.info("auth.callback.received has_code=%s state=%s error=%s",
                has_code, state, params.get("error"))

    if "error" in params:
        desc = params.get("error_description", params["error"])
        logger.warning("auth.callback.provider_error %s", desc)
        return _error_redirect(desc)

    flow = await consume_state(session, state)
    if flow is None:
        logger.warning("auth.callback.invalid_state state=%s", state)
        return _error_redirect("Invalid or expired sign-in state. Please try again.")

    try:
        logger.info("auth.callback.token_exchange.start")
        result = await asyncio.to_thread(acquire_token_by_auth_code_flow, flow, params)
        logger.info("auth.callback.token_exchange.ok scopes=%s", result.get("scope", ""))
    except Exception as exc:
        logger.exception("auth.callback.token_exchange.failed")
        return _error_redirect(str(exc))

    id_claims = result.get("id_token_claims") or {}
    entra_user_id = id_claims.get("oid") or id_claims.get("sub")
    entra_tenant_id = id_claims.get("tid") or settings.azure_tenant_id
    email = id_claims.get("preferred_username") or id_claims.get("email") or ""
    name = id_claims.get("name") or email
    if not entra_user_id or not entra_tenant_id:
        logger.warning("auth.callback.missing_claims")
        return _error_redirect("Microsoft did not return identity claims.")

    try:
        # Upsert tenant
        tenant = (await session.execute(
            select(Tenant).where(Tenant.entra_tenant_id == entra_tenant_id)
        )).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(entra_tenant_id=entra_tenant_id, name=entra_tenant_id)
            session.add(tenant)
            await session.flush()
        logger.info("auth.callback.tenant_upsert ok id=%s", tenant.id)

        # Upsert user
        user = (await session.execute(
            select(User).where(User.tenant_id == tenant.id, User.entra_user_id == entra_user_id)
        )).scalar_one_or_none()
        if user is None:
            user = User(
                tenant_id=tenant.id, entra_user_id=entra_user_id,
                display_name=name, email=email, timezone="America/Chicago", role="user",
            )
            session.add(user)
            await session.flush()
            session.add(ScanSettings(tenant_id=tenant.id, user_id=user.id))
        else:
            user.display_name = name
            user.email = email
        logger.info("auth.callback.user_upsert ok id=%s email=%s", user.id, email)

        # Upsert Graph connection (encrypted token reference via token_store)
        conn = (await session.execute(
            select(GraphConnection).where(
                GraphConnection.user_id == user.id, GraphConnection.provider == "microsoft"
            )
        )).scalar_one_or_none()
        store = get_token_store()
        expires = expires_at_from(result)
        access_ref = store.put("access", StoredToken(
            access_token=result["access_token"],
            refresh_token=result.get("refresh_token"),
            expires_at=expires,
            scopes=result.get("scope", "").split(),
        ))
        refresh_ref = None
        if result.get("refresh_token"):
            refresh_ref = store.put("refresh", StoredToken(
                access_token=result["refresh_token"], refresh_token=None,
                expires_at=expires, scopes=[],
            ))
        if conn is None:
            conn = GraphConnection(
                tenant_id=tenant.id, user_id=user.id, provider="microsoft",
                scopes=result.get("scope", ""), status=ConnectionStatus.CONNECTED.value,
                token_reference=access_ref, refresh_token_reference=refresh_ref,
                expires_at=expires, last_connected_at=datetime.utcnow(),
            )
            session.add(conn)
        else:
            conn.scopes = result.get("scope", conn.scopes)
            conn.status = ConnectionStatus.CONNECTED.value
            conn.token_reference = access_ref
            if refresh_ref:
                conn.refresh_token_reference = refresh_ref
            conn.expires_at = expires
            conn.last_connected_at = datetime.utcnow()
        logger.info("auth.callback.graph_connection.ok user_id=%s expires=%s", user.id, expires.isoformat())

        await log(session, tenant_id=tenant.id, user_id=user.id,
                  action="auth.microsoft.connected", entity_type="graph_connection",
                  entity_id=conn.id if conn.id else None)
        await session.commit()
    except Exception as exc:
        logger.exception("auth.callback.persist_failed")
        await session.rollback()
        return _error_redirect("Could not save your account. Please try again.")

    token, _exp = await issue_session(
        session, user_id=user.id, tenant_id=tenant.id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    target = f"{settings.frontend_url}/dashboard"
    response = RedirectResponse(target, status_code=302)
    response.set_cookie(
        key=settings.effective_cookie_name,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path="/",
    )
    logger.info("auth.callback.session_cookie_set user_id=%s redirect=%s cookie=%s",
                user.id, target, settings.effective_cookie_name)
    return response


@router.get("/auth/dev-login")
async def dev_login(
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Dev-only bypass: creates/reuses a local user and issues a session cookie.
    Disabled automatically when APP_ENV != development."""
    settings = get_settings()
    if settings.app_env != "development":
        logger.warning("auth.dev_login.blocked app_env=%s remote=%s",
                       settings.app_env, "<masked>")
        raise HTTPException(status_code=404, detail="Not found")

    entra_tenant_id = "dev-tenant"
    entra_user_id   = "dev-user"
    email           = "dev@localhost"
    name            = "Dev User"

    tenant = (await session.execute(
        select(Tenant).where(Tenant.entra_tenant_id == entra_tenant_id)
    )).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(entra_tenant_id=entra_tenant_id, name="Dev Tenant")
        session.add(tenant)
        await session.flush()

    user = (await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.entra_user_id == entra_user_id)
    )).scalar_one_or_none()
    if user is None:
        user = User(
            tenant_id=tenant.id, entra_user_id=entra_user_id,
            display_name=name, email=email, timezone="America/Chicago", role="user",
        )
        session.add(user)
        await session.flush()
        session.add(ScanSettings(tenant_id=tenant.id, user_id=user.id))

    await session.commit()

    token, _exp = await issue_session(
        session, user_id=user.id, tenant_id=tenant.id, ip=None, user_agent="dev-login",
    )
    response = RedirectResponse(f"{settings.frontend_url}/dashboard", status_code=302)
    response.set_cookie(
        key=settings.effective_cookie_name,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path="/",
    )
    logger.info("auth.dev_login user_id=%s", user.id)
    return response


@router.post("/auth/logout")
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Revoke the current session (server-side) and clear the cookie."""
    settings = get_settings()
    cookie = request.cookies.get(settings.effective_cookie_name) or request.cookies.get(settings.cookie_name)
    if cookie:
        try:
            payload = decode_session_token(cookie)
            jti = payload.get("jti")
            if jti:
                await revoke_session(session, jti)
        except ValueError:
            pass  # invalid/expired cookie — nothing to revoke
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        key=settings.effective_cookie_name,
        path="/",
        domain=settings.cookie_domain or None,
    )
    return response


@router.post("/auth/logout-all")
async def logout_all(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Sign out *every* active session for the current user."""
    settings = get_settings()
    n = await revoke_all_for_user(session, ctx.user.id)
    await log(session, tenant_id=ctx.tenant_id, user_id=ctx.user.id,
              action="auth.logout_all", entity_type="user", entity_id=ctx.user.id,
              details={"revoked": n})
    response = JSONResponse({"ok": True, "revoked": n})
    response.delete_cookie(
        key=settings.effective_cookie_name, path="/", domain=settings.cookie_domain or None,
    )
    return response
