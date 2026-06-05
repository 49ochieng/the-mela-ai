"""
Mela AI - Microsoft Graph Client (MSAL-backed)
App-only (client-credentials) for background crawling.
Delegated (on-behalf-of) for user-scoped connectors.
Tokens are NEVER logged or stored in plain text.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
import msal

from app.core.config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_APP_SCOPES = ["https://graph.microsoft.com/.default"]

# In-process token cache (never written to disk)
_token_cache: Dict[str, Any] = {}


def _build_confidential_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.effective_client_id,
        client_credential=settings.effective_client_secret,
        authority=settings.graph_authority,
    )


async def get_app_token() -> str:
    """Acquire application-only token via client-credentials. Cached in-process."""
    entry = _token_cache.get("app")
    if entry and time.time() < entry["exp"] - 60:
        return entry["tok"]

    app = _build_confidential_app()
    result = app.acquire_token_for_client(scopes=_APP_SCOPES)
    if "access_token" not in result:
        desc = result.get("error_description", result.get("error", "unknown"))
        raise RuntimeError(f"Graph token acquisition failed: {desc}")

    _token_cache["app"] = {
        "tok": result["access_token"],
        "exp": time.time() + result.get("expires_in", 3600),
    }
    logger.info("Acquired app-only Graph token (ttl=%ss)", result.get("expires_in"))
    return result["access_token"]


class GraphClient:
    """
    Async wrapper around Microsoft Graph REST API.
    Pass a delegated_token for user-scoped operations;
    leave it None to use the app-only token.
    """

    def __init__(self, delegated_token: Optional[str] = None) -> None:
        self._delegated_token = delegated_token

    async def _auth_header(self) -> str:
        tok = self._delegated_token or await get_app_token()
        return f"Bearer {tok}"

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict:
        auth = await self._auth_header()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{GRAPH_BASE}{path}", headers={"Authorization": auth}, params=params or {})
            if r.status_code >= 400:
                logger.error("Graph GET %s → %s", path, r.status_code)
                r.raise_for_status()
            return r.json()

    async def _get_bytes(self, path: str) -> bytes:
        auth = await self._auth_header()
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(f"{GRAPH_BASE}{path}", headers={"Authorization": auth})
            r.raise_for_status()
            return r.content

    async def get_all_pages(self, path: str, params: Optional[Dict] = None) -> List[Dict]:
        """Follow @odata.nextLink until all pages consumed."""
        items: List[Dict] = []
        data = await self._get(path, params)
        items.extend(data.get("value", []))
        while "@odata.nextLink" in data:
            auth = await self._auth_header()
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(data["@odata.nextLink"], headers={"Authorization": auth})
                r.raise_for_status()
                data = r.json()
            items.extend(data.get("value", []))
        return items

    # ── Sites & Drives ────────────────────────────────────────────────────────

    async def get_site_by_url(self, site_url: str) -> Dict:
        parsed = urlparse(site_url)
        host = parsed.netloc
        path = parsed.path.rstrip("/")
        return await self._get(f"/sites/{host}:{path}")

    async def get_site_drives(self, site_id: str) -> List[Dict]:
        data = await self._get(f"/sites/{site_id}/drives")
        return data.get("value", [])

    async def get_drive_delta(
        self, site_id: str, drive_id: str, delta_token: Optional[str] = None
    ) -> tuple[List[Dict], str]:
        """Return (changed_items, new_delta_token). Full crawl if delta_token is None."""
        if delta_token:
            url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root/delta(token='{delta_token}')"
        else:
            url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root/delta"

        items: List[Dict] = []
        new_delta = ""
        auth = await self._auth_header()

        while url:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(url, headers={"Authorization": auth})
                r.raise_for_status()
                data = r.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink", "")
            if not url:
                new_delta = data.get("@odata.deltaLink", "")

        # Extract bare token from deltaLink URL
        if "token='" in new_delta:
            new_delta = new_delta.split("token='")[-1].rstrip("')")

        return items, new_delta

    async def download_item(self, site_id: str, drive_id: str, item_id: str) -> bytes:
        return await self._get_bytes(
            f"/sites/{site_id}/drives/{drive_id}/items/{item_id}/content"
        )

    async def get_item_permissions(self, site_id: str, drive_id: str, item_id: str) -> List[Dict]:
        data = await self._get(f"/sites/{site_id}/drives/{drive_id}/items/{item_id}/permissions")
        return data.get("value", [])

    # ── OneDrive ──────────────────────────────────────────────────────────────

    async def get_my_drive(self) -> Dict:
        return await self._get("/me/drive")

    async def get_my_drive_delta(
        self, delta_token: Optional[str] = None
    ) -> tuple[List[Dict], str]:
        """Delegated-token variant for /me/drive (kept for backward compat)."""
        if delta_token:
            url = f"{GRAPH_BASE}/me/drive/root/delta(token='{delta_token}')"
        else:
            url = f"{GRAPH_BASE}/me/drive/root/delta"

        items: List[Dict] = []
        new_delta = ""
        auth = await self._auth_header()

        while url:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(url, headers={"Authorization": auth})
                r.raise_for_status()
                data = r.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink", "")
            if not url:
                new_delta = data.get("@odata.deltaLink", "")

        if "token='" in new_delta:
            new_delta = new_delta.split("token='")[-1].rstrip("')")
        return items, new_delta

    async def get_user_drive_delta(
        self, user_id: str, delta_token: Optional[str] = None
    ) -> tuple[List[Dict], str]:
        """App-only variant: crawl any user's OneDrive using /users/{id}/drive.

        Requires Files.Read.All (or Sites.Read.All) application permission.
        This is the correct method for background sync — no delegated token needed.
        """
        if not user_id or not user_id.strip():
            raise ValueError("user_id must not be empty")

        if delta_token:
            url = f"{GRAPH_BASE}/users/{user_id}/drive/root/delta(token='{delta_token}')"
        else:
            url = f"{GRAPH_BASE}/users/{user_id}/drive/root/delta"

        items: List[Dict] = []
        new_delta = ""
        # Always use app-only token for background sync — never delegated
        app_token = await get_app_token()
        auth = f"Bearer {app_token}"

        while url:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(url, headers={"Authorization": auth})
                if r.status_code >= 400:
                    logger.error(
                        "OneDrive app-only delta for user %s → %s", user_id, r.status_code
                    )
                    r.raise_for_status()
                data = r.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink", "")
            if not url:
                new_delta = data.get("@odata.deltaLink", "")

        if "token='" in new_delta:
            new_delta = new_delta.split("token='")[-1].rstrip("')")
        return items, new_delta

    async def download_user_drive_item(self, user_id: str, item_id: str) -> bytes:
        """App-only download of a OneDrive item. Requires Files.Read.All."""
        # Use a fresh app token (not the delegated path) so background workers work.
        app_token = await get_app_token()
        auth = f"Bearer {app_token}"
        url = f"{GRAPH_BASE}/users/{user_id}/drive/items/{item_id}/content"
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(url, headers={"Authorization": auth})
            r.raise_for_status()
            return r.content

    async def search_user_drive(
        self, user_id: str, query: str, top: int = 5
    ) -> List[Dict]:
        """Search a specific user's OneDrive via app-only token.

        Returns a list of driveItem metadata dicts with id, name, webUrl,
        size, lastModifiedDateTime, and createdBy fields.
        Requires Files.Read.All application permission.
        """
        app_token = await get_app_token()
        auth = f"Bearer {app_token}"
        encoded_query = query.replace("'", "''")
        url = f"{GRAPH_BASE}/users/{user_id}/drive/search(q='{encoded_query}')"
        params = {
            "$top": min(top, 25),
            "$select": "id,name,webUrl,size,lastModifiedDateTime,createdBy,parentReference,file",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(url, headers={"Authorization": auth}, params=params)
                if r.status_code == 404:
                    logger.debug("[Graph] search_user_drive: user %s has no OneDrive or not found", user_id)
                    return []
                r.raise_for_status()
                return r.json().get("value", [])
        except Exception as exc:
            logger.warning("[Graph] search_user_drive failed for user %s: %s", user_id, exc)
            return []

    # ── Mail ──────────────────────────────────────────────────────────────────

    async def list_mail_folders(self) -> List[Dict]:
        data = await self._get("/me/mailFolders")
        return data.get("value", [])

    async def list_messages(self, folder_id: str = "inbox", top: int = 50) -> List[Dict]:
        return await self.get_all_pages(
            f"/me/mailFolders/{folder_id}/messages",
            {
                "$top": top,
                "$select": "id,subject,from,receivedDateTime,bodyPreview,hasAttachments",
                "$orderby": "receivedDateTime desc",
            },
        )

    # ── Planner ───────────────────────────────────────────────────────────────

    async def list_my_groups(self) -> List[Dict]:
        return await self.get_all_pages("/me/memberOf", {"$select": "id,displayName,mail"})

    async def get_group_plans(self, group_id: str) -> List[Dict]:
        try:
            data = await self._get(f"/groups/{group_id}/planner/plans")
            return data.get("value", [])
        except Exception:
            return []

    async def list_plan_tasks(self, plan_id: str) -> List[Dict]:
        data = await self._get(f"/planner/plans/{plan_id}/tasks")
        return data.get("value", [])

    async def list_my_planner_tasks(self) -> List[Dict]:
        data = await self._get("/me/planner/tasks")
        return data.get("value", [])

    # ── Graph Search (live file discovery) ────────────────────────────────────

    async def search_files(
        self,
        query: str,
        entity_types: Optional[List[str]] = None,
        top: int = 10,
    ) -> List[Dict]:
        """Search across SharePoint and OneDrive using the Microsoft Search API.

        Uses POST /search/query which supports full-text search across
        driveItems, listItems, sites, etc.

        Returns a flat list of hit objects with resource data.
        """
        if not entity_types:
            entity_types = ["driveItem"]

        body = {
            "requests": [
                {
                    "entityTypes": entity_types,
                    "query": {"queryString": query},
                    "region": "US",
                    "from": 0,
                    "size": min(top, 25),
                }
            ]
        }

        auth = await self._auth_header()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{GRAPH_BASE}/search/query",
                headers={
                    "Authorization": auth,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if r.status_code >= 400:
                logger.error("Graph Search POST /search/query → %s: %s", r.status_code, r.text[:300])
                r.raise_for_status()
            data = r.json()

        hits = []
        for response in data.get("value", []):
            for hit_container in response.get("hitsContainers", []):
                for hit in hit_container.get("hits", []):
                    resource = hit.get("resource", {})
                    resource["_rank"] = hit.get("rank", 0)
                    resource["_summary"] = hit.get("summary", "")
                    hits.append(resource)
        return hits
