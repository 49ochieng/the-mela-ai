"""Central Graph HTTP client with throttling-aware retry."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ...config import get_settings
from ...enums import ConnectionStatus
from ...models import GraphConnection
from ..auth.entra import acquire_token_by_refresh, expires_at_from
from ..auth.token_store import StoredToken, get_token_store

logger = logging.getLogger(__name__)
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphHTTPError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"Graph {status}: {body}")
        self.status = status
        self.body = body


class GraphRetryableError(GraphHTTPError):
    """401 (after refresh), 429 throttle, or 5xx server error — safe to retry."""


class NeedsReconnect(RuntimeError):
    pass


class GraphClient:
    """Per-connection async HTTP client.

    Construct via `GraphClient.for_user(session, connection)`. Handles token
    refresh transparently and surfaces `NeedsReconnect` when refresh fails so
    the UI can prompt the user.
    """

    def __init__(self, session: AsyncSession, connection: GraphConnection) -> None:
        self._session = session
        self._connection = connection
        self._http = httpx.AsyncClient(timeout=30.0)
        self._store = get_token_store()

    @classmethod
    async def for_user(cls, session: AsyncSession, user_id: str, tenant_id: str) -> "GraphClient":
        result = await session.execute(
            select(GraphConnection).where(
                GraphConnection.user_id == user_id,
                GraphConnection.tenant_id == tenant_id,
                GraphConnection.provider == "microsoft",
            )
        )
        conn = result.scalar_one_or_none()
        if conn is None or conn.status != ConnectionStatus.CONNECTED.value:
            raise NeedsReconnect("Microsoft 365 not connected")
        return cls(session, conn)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "GraphClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ── auth ──────────────────────────────────────────────────
    async def _access_token(self) -> str:
        if not self._connection.token_reference:
            raise NeedsReconnect("No token on file")
        token = self._store.get(self._connection.token_reference)
        # Refresh if within 60s of expiry. Normalize both sides to aware UTC.
        exp = token.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= datetime.now(timezone.utc) + timedelta(seconds=60):
            await self._refresh()
            token = self._store.get(self._connection.token_reference)
        return token.access_token

    async def _refresh(self) -> None:
        if not self._connection.refresh_token_reference:
            await self._mark_needs_reconnect("no refresh token")
            raise NeedsReconnect("No refresh token")
        rt = self._store.get(self._connection.refresh_token_reference)
        try:
            result = await asyncio.to_thread(acquire_token_by_refresh, rt.access_token)
        except Exception as e:
            await self._mark_needs_reconnect(f"refresh failed: {e}")
            raise NeedsReconnect("Refresh failed") from e
        new_access = StoredToken(
            access_token=result["access_token"],
            refresh_token=result.get("refresh_token") or rt.access_token,
            expires_at=expires_at_from(result),
            scopes=result.get("scope", "").split(),
        )
        self._connection.token_reference = self._store.put("access", new_access)
        if result.get("refresh_token"):
            self._connection.refresh_token_reference = self._store.put(
                "refresh",
                StoredToken(
                    access_token=result["refresh_token"],
                    refresh_token=None,
                    expires_at=expires_at_from(result),
                    scopes=[],
                ),
            )
        self._connection.expires_at = new_access.expires_at
        await self._session.commit()

    async def _mark_needs_reconnect(self, reason: str) -> None:
        logger.warning("Marking connection needs_reconnect: %s", reason)
        self._connection.status = ConnectionStatus.NEEDS_RECONNECT.value
        await self._session.commit()

    # ── HTTP ──────────────────────────────────────────────────
    @retry(
        reraise=True,
        retry=retry_if_exception_type(GraphRetryableError),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
    )
    async def request(self, method: str, path_or_url: str, *, params: Optional[dict] = None,
                      json: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
        token = await self._access_token()
        url = path_or_url if path_or_url.startswith("http") else f"{GRAPH_BASE}{path_or_url}"
        req_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if headers:
            req_headers.update(headers)
        resp = await self._http.request(method, url, params=params, json=json, headers=req_headers)
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            retry_after = float(resp.headers.get("Retry-After", "1"))
            await asyncio.sleep(min(retry_after, 30))
            raise GraphRetryableError(resp.status_code, resp.text)
        if resp.status_code == 401:
            await self._refresh()
            raise GraphRetryableError(401, "token refreshed, retrying")
        if resp.status_code >= 400:
            # Non-retryable 4xx (400/403/404/409/412 etc.) — fail fast.
            raise GraphHTTPError(resp.status_code, resp.text)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    async def get(self, path: str, **kw) -> Any: return await self.request("GET", path, **kw)
    async def post(self, path: str, **kw) -> Any: return await self.request("POST", path, **kw)
    async def patch(self, path: str, **kw) -> Any: return await self.request("PATCH", path, **kw)

    async def paged(self, path: str, params: Optional[dict] = None) -> list[dict]:
        items: list[dict] = []
        next_url: Optional[str] = None
        first = True
        while first or next_url:
            data = await self.get(next_url or path, params=params if first else None)
            first = False
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
        return items

    async def paged_with_delta(
        self, path: str, params: Optional[dict] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """Like ``paged`` but also returns the final ``@odata.deltaLink``
        when present (Graph delta queries). The deltaLink is the URL to
        pass back next time to fetch only changes since this point."""
        items: list[dict] = []
        next_url: Optional[str] = None
        delta_link: Optional[str] = None
        first = True
        while first or next_url:
            data = await self.get(next_url or path, params=params if first else None)
            first = False
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
            if not next_url:
                delta_link = data.get("@odata.deltaLink") or delta_link
        return items, delta_link
