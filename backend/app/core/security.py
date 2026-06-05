"""
Mela AI - Security and Authentication
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from app.core.config import settings
from app.core.database import get_db
from app.schemas.auth import TokenData, UserInfo

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


def _audit_auth_failure(reason: str, *, token_jti: Optional[str] = None,
                        ip: Optional[str] = None, ua: Optional[str] = None,
                        detail: Optional[str] = None) -> None:
    """Phase 2 (H-7): log a failed-auth event to the audit logger.

    We deliberately do NOT write an AuditLog DB row here because:
      * failed-auth requests never carry a verified user_id (FK NOT NULL),
      * the audit logger forwards to Azure Application Insights when wired,
        which is the right sink for high-volume auth failure telemetry.
    """
    try:
        from app.core.logging import audit_logger
        audit_logger.log_action(
            user_id="<unauthenticated>",
            action="auth_failed",
            resource="token",
            details={
                "reason": reason,
                "token_jti": token_jti,
                "ip_address": ip,
                "user_agent": (ua or "")[:200] or None,
                "detail": detail,
            },
            success=False,
        )
    except Exception:
        # Audit logging must never break the request.
        pass


class AzureADAuth:
    """Azure AD authentication handler."""

    def __init__(self):
        self.tenant_id = settings.effective_tenant_id
        # Use the login-dedicated app registration for token validation.
        # Falls back to the data-app client ID when ENTRA_AUTH_CLIENT_ID is
        # not set, keeping single-registration deployments working.
        self.client_id = settings.auth_client_id
        # Accept both bare GUID and api:// prefixed audience (v1 vs v2 tokens)
        self.valid_audiences = [
            self.client_id,
            f"api://{self.client_id}",
        ]
        # Phase 0: explicit issuer validation. Azure AD emits two issuer formats
        # depending on the app's accessTokenAcceptedVersion:
        #   v1: https://sts.windows.net/{tid}/
        #   v2: https://login.microsoftonline.com/{tid}/v2.0
        # We accept either for the configured tenant — both are equally
        # authoritative — but reject any other issuer outright.
        self.valid_issuers = [
            f"https://sts.windows.net/{self.tenant_id}/",
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
        ] if self.tenant_id else []
        self.jwks_uri = f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
        self._jwks_cache = None
        self._jwks_cache_time = None

    async def get_jwks(self) -> Dict:
        """Get JSON Web Key Set from Azure AD."""
        # Cache JWKS for 1 hour
        if self._jwks_cache and self._jwks_cache_time:
            if datetime.utcnow() - self._jwks_cache_time < timedelta(hours=1):
                return self._jwks_cache

        async with httpx.AsyncClient() as client:
            response = await client.get(self.jwks_uri)
            response.raise_for_status()
            self._jwks_cache = response.json()
            self._jwks_cache_time = datetime.utcnow()
            return self._jwks_cache

    async def validate_token(self, token: str) -> Dict[str, Any]:
        """Validate Azure AD access token."""
        if not self.tenant_id or not self.client_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Azure AD is not configured. Set AZURE_TENANT_ID and AZURE_CLIENT_ID.",
            )
        try:
            # Get JWKS
            jwks = await self.get_jwks()

            # Decode without verification to get header
            unverified_header = jwt.get_unverified_header(token)
            token_kid = unverified_header.get("kid", "")

            # Find the key — use .get() for optional JWKS fields so a missing
            # field never raises KeyError and masks the real error.
            rsa_key = {}
            for key in jwks.get("keys", []):
                if key.get("kid") == token_kid:
                    rsa_key = {
                        "kty": key.get("kty", "RSA"),
                        "kid": key.get("kid", ""),
                        "use": key.get("use", "sig"),
                        "n": key.get("n", ""),
                        "e": key.get("e", ""),
                    }
                    break

            if not rsa_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unable to find appropriate key",
                )

            # Try each valid audience (bare GUID for v1, api:// prefix for v2).
            # Phase 0: also enforce issuer. Azure AD has two formats (v1 vs v2);
            # we try every (audience, issuer) pair so we accept whichever the
            # token actually carries while rejecting all foreign issuers.
            payload = None
            last_err = None
            _multi = {"common", "organizations", "consumers"}
            _check_iss = bool(
                self.valid_issuers
                and self.tenant_id
                and self.tenant_id.lower() not in _multi
            )
            for aud in self.valid_audiences:
                if _check_iss:
                    for iss in self.valid_issuers:
                        try:
                            payload = jwt.decode(
                                token,
                                rsa_key,
                                algorithms=["RS256"],
                                audience=aud,
                                issuer=iss,
                                options={"leeway": 60},  # 60s clock skew
                            )
                            break
                        except JWTError as e:
                            last_err = e
                    if payload is not None:
                        break
                else:
                    try:
                        payload = jwt.decode(
                            token,
                            rsa_key,
                            algorithms=["RS256"],
                            audience=aud,
                            options={"leeway": 60},
                        )
                        break
                    except JWTError as e:
                        last_err = e

            if payload is None:
                logger.error("JWT token validation failed (audience/issuer mismatch): %s", last_err)
                _audit_auth_failure(
                    reason="audience_or_issuer_mismatch",
                    detail=str(last_err) if last_err else None,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication token",
                )

            # ── Tenant isolation (single-tenant mode) ─────────────────────────
            # When AZURE_TENANT_ID is configured, reject tokens from any other
            # tenant.  This prevents a user from a foreign tenant authenticating
            # against this app even if they somehow obtained a valid access token.
            token_tid = payload.get("tid", "")
            configured_tid = self.tenant_id
            # "common" / "organizations" / "consumers" are multi-tenant endpoints —
            # skip the check only when the app is intentionally multi-tenant.
            _multi_tenant_endpoints = {"common", "organizations", "consumers"}
            if (
                configured_tid
                and configured_tid.lower() not in _multi_tenant_endpoints
                and token_tid
                and token_tid.lower() != configured_tid.lower()
            ):
                logger.error(
                    "Tenant mismatch: token tid=%s, expected=%s", token_tid, configured_tid
                )
                _audit_auth_failure(
                    reason="tenant_mismatch",
                    detail=f"token_tid={token_tid} expected={configured_tid}",
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token issued by an untrusted tenant",
                )

            return payload

        except HTTPException:
            raise
        except JWTError as e:
            logger.error("JWT validation error: %s", e)
            _audit_auth_failure(reason="jwt_error", detail=str(e))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )
        except Exception as e:
            # Log the full exception so we know exactly what went wrong —
            # e.g. JWKS network error, KeyError on missing JWKS field, etc.
            logger.error("Unexpected token validation error (%s): %s", type(e).__name__, e, exc_info=True)
            raise


azure_auth = AzureADAuth()


def _token_expiry_from_claim(exp_claim: Any) -> Optional[datetime]:
    """Convert a JWT `exp` claim to a naive UTC datetime."""
    if exp_claim is None:
        return None
    try:
        return datetime.utcfromtimestamp(int(exp_claim))
    except Exception:
        return None


async def _enforce_server_side_session(
    request: Request,
    db: AsyncSession,
    user: UserInfo,
    token_jti: str,
    token_exp: Optional[datetime],
) -> None:
    """Enforce active-user + session validity when a DB user row exists.

    First-login requests can arrive before a user row exists (e.g. /auth/login).
    In that bootstrap path we skip session creation here and let /auth/login
    create the initial session once the user row is persisted.
    """
    from app.core.sessions import get_or_create_session, session_is_valid, touch_session
    from app.models.models import User as UserModel

    user_row = (
        await db.execute(select(UserModel).where(UserModel.azure_id == user.id))
    ).scalar_one_or_none()

    if user_row is None:
        return

    if not user_row.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    session_row = await get_or_create_session(
        db,
        user_id=user_row.id,
        token_jti=token_jti,
        token_exp=token_exp,
        ip_address=(request.client.host if request.client else None),
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
    )

    ok, reason = session_is_valid(session_row)
    if not ok:
        detail = "Session expired. Please sign in again."
        if reason == "session_revoked":
            detail = "Session revoked. Please sign in again."
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    await touch_session(db, session_row.id)
    request.state.session_id = session_row.id


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> UserInfo:
    """Get current authenticated user from token."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        raw_token = credentials.credentials
        request.state.access_token = raw_token

        # First, try to verify as internal dev token
        token_data = verify_internal_token(raw_token)
        if token_data and token_data.user_id:
            # This is a dev token - fetch real user identity and groups from Graph API
            dev_email = token_data.email or "dev@mela-ai.local"
            dev_user_id = token_data.user_id
            dev_groups: list[str] = []
            dev_tenant_id = "dev-tenant"

            # Allow internal-token issuers to explicitly set tenant scope via a
            # `tid` claim. An empty string means "global / unscoped" — required
            # for control-plane operations gated by `_require_global_admin`.
            try:
                # NOTE: Cannot use module-level `settings` here — a later
                # `from app.core.config import settings` in this function makes
                # `settings` a function-local name, triggering UnboundLocalError.
                from app.core.config import settings as _cfg_settings
                _raw_payload = jwt.decode(
                    raw_token,
                    _cfg_settings.JWT_SECRET_KEY,
                    algorithms=[_cfg_settings.JWT_ALGORITHM],
                    options={"verify_exp": False},
                )
                if "tid" in _raw_payload:
                    dev_tenant_id = _raw_payload.get("tid") or ""
            except Exception:
                pass

            # Try to get real Azure AD identity and groups for the dev user
            if "@" in dev_email and not dev_email.endswith("@mela-ai.local"):
                try:
                    from app.services.graph_service import GraphAPIService
                    gs = GraphAPIService()
                    # Look up the real user by email (UPN)
                    real_user = await gs.get_user_app(dev_email)
                    if real_user:
                        dev_user_id = real_user.get("id", token_data.user_id)
                        # Fetch user's groups
                        user_groups_data = await gs.get_user_groups_app(dev_user_id)
                        dev_groups = [
                            g.get("id") for g in user_groups_data
                            if g.get("@odata.type") == "#microsoft.graph.group" and g.get("id")
                        ]
                        # Get tenant from config
                        from app.core.config import settings
                        dev_tenant_id = settings.AZURE_TENANT_ID or "dev-tenant"
                        logger.info("Dev mode: Fetched real identity for %s (id=%s, %d groups)",
                                    dev_email, dev_user_id, len(dev_groups))
                except Exception as e:
                    logger.warning("Dev mode: Failed to fetch real user identity: %s", e)

            user_info = UserInfo(
                id=dev_user_id,
                email=dev_email,
                name="Dev User",
                given_name="Dev",
                family_name="User",
                roles=token_data.roles or ["Admin", "user"],
                department="Development",
                job_title="Developer",
                tenant_id=dev_tenant_id,
                groups=dev_groups,
            )
            token_jti = raw_token
            try:
                from app.core.sessions import derive_jti
                token_jti = derive_jti(raw_token)
            except Exception:
                pass
            request.state.token_jti = token_jti
            request.state.token_exp = None
            request.state.user_id = user_info.id
            request.state.user_email = user_info.email
            request.state.tenant_id = user_info.tenant_id

            await _enforce_server_side_session(
                request=request,
                db=db,
                user=user_info,
                token_jti=token_jti,
                token_exp=None,
            )

            request.state.is_dev_user = True
            return user_info

        # Otherwise, validate token with Azure AD
        payload = await azure_auth.validate_token(raw_token)

        # Use `oid` (Object ID) as the immutable user identifier.
        # `oid` is guaranteed stable across re-authentications and tenant changes
        # for the same user. Fall back to `sub` only when `oid` is absent (rare).
        user_id = payload.get("oid") or payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing required claim: oid",
            )

        # Extract user info
        # Groups claim contains Azure AD group object IDs for ACL filtering
        # Requires "groups" optional claim configured in Azure AD app manifest
        groups_claim = payload.get("groups", [])
        if not isinstance(groups_claim, list):
            groups_claim = []

        # If groups claim is empty, fetch from Graph API as fallback
        # This handles cases where Azure AD app doesn't have groups claim configured
        if not groups_claim:
            try:
                from app.services.graph_service import GraphAPIService
                gs = GraphAPIService()
                user_groups_data = await gs.get_user_groups_app(user_id)
                # Extract group IDs from the response
                groups_claim = [
                    g.get("id") for g in user_groups_data
                    if g.get("@odata.type") == "#microsoft.graph.group" and g.get("id")
                ]
                logger.info("Fetched %d groups from Graph API for user %s", len(groups_claim), user_id)
            except Exception as e:
                logger.warning("Failed to fetch user groups from Graph API: %s", e)
                groups_claim = []

        user_info = UserInfo(
            id=user_id,
            email=(
                payload.get("preferred_username")
                or payload.get("email")
                or payload.get("upn")
                or payload.get("unique_name")
                or ""
            ),
            name=payload.get("name", ""),
            given_name=payload.get("given_name", ""),
            family_name=payload.get("family_name", ""),
            roles=payload.get("roles", []),
            groups=groups_claim,
            department=payload.get("department", ""),
            job_title=payload.get("jobTitle", ""),
            tenant_id=payload.get("tid", ""),
        )

        claim_jti = payload.get("jti")
        token_jti = raw_token
        try:
            from app.core.sessions import derive_jti
            token_jti = derive_jti(raw_token, claim_jti)
        except Exception:
            pass
        token_exp = _token_expiry_from_claim(payload.get("exp"))

        request.state.token_jti = token_jti
        request.state.token_exp = token_exp
        request.state.user_id = user_info.id
        request.state.user_email = user_info.email
        request.state.tenant_id = user_info.tenant_id

        await _enforce_server_side_session(
            request=request,
            db=db,
            user=user_info,
            token_jti=token_jti,
            token_exp=token_exp,
        )

        request.state.is_dev_user = False

        return user_info

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Authentication error (%s): %s", type(e).__name__, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        )


async def get_current_admin_user(
    current_user: UserInfo = Depends(get_current_user),
) -> UserInfo:
    """Ensure current user has admin role.

    Primary check: DB user.role == 'admin' (set by bootstrap elevation or
    manual promotion via PUT /admin/users/{id}).
    Fallback: Entra App Role claim 'Admin' / 'admin' — covers service
    principals and dev tokens that have no DB row.
    """
    from app.core.database import get_db  # avoid circular at import time
    from app.models.models import User as UserModel, UserRole

    # Fast path: token already carries an admin role claim (dev token / Entra
    # App Role assignment).  Skip the DB round-trip.
    token_is_admin = (
        "Admin" in current_user.roles or "admin" in current_user.roles
    )

    # Authoritative path: look up the DB row.
    db_is_admin = False
    try:
        async for db in get_db():
            result = await db.execute(
                select(UserModel).where(UserModel.azure_id == current_user.id)
            )
            db_user = result.scalar_one_or_none()
            if db_user is not None:
                db_is_admin = db_user.role == UserRole.ADMIN
            break
    except Exception:
        # DB unavailable — fall back to token claims only
        pass

    if not (db_is_admin or token_is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create internal JWT access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_internal_token(token: str) -> Optional[TokenData]:
    """Verify internal JWT token. In dev/debug mode, expired tokens are still accepted."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
        return TokenData(user_id=user_id, email=payload.get("email"), roles=payload.get("roles", []))
    except JWTError:
        # In development mode, try ignoring token expiry so long-lived dev sessions still work
        if settings.DEBUG or settings.APP_ENV == "development":
            try:
                payload = jwt.decode(
                    token,
                    settings.JWT_SECRET_KEY,
                    algorithms=[settings.JWT_ALGORITHM],
                    options={"verify_exp": False},
                )
                user_id = payload.get("sub")
                # Only accept tokens that were originally issued as dev tokens
                if user_id and payload.get("is_dev"):
                    return TokenData(
                        user_id=user_id,
                        email=payload.get("email"),
                        roles=payload.get("roles", []),
                    )
            except Exception:
                pass
        return None
