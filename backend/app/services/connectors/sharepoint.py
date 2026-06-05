"""
Mela AI - SharePoint Connector
Crawls configured SharePoint sites via Graph delta API.
Extracts text from docx, pdf, pptx, txt, xlsx, html, md, csv, json.
Fetches per-item Graph permissions and maps them to ACL fields for
permission-trimmed retrieval.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import AsyncIterator, Dict, List, Optional, Set

from app.core.config import settings
from app.services.connectors.base import ConnectorBase, ConnectorDocument, SOURCE_TYPE_SHAREPOINT
from app.services.connectors.graph_client import GraphClient, get_app_token
from app.services.document_service import extract_text

logger = logging.getLogger(__name__)

INDEXABLE_EXTENSIONS = {
    ".docx", ".doc", ".pdf", ".pptx", ".ppt",
    ".txt", ".md", ".html", ".htm",
    ".xlsx", ".xls", ".csv", ".json", ".xml",
}
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB

# File names (case-insensitive) that are never worth indexing — typically
# large auto-generated lock files, Unicode test datasets, or build artifacts
# that would either exceed the embedding model's token limit or add noise.
SKIP_FILENAMES = {
    "package-lock.json", ".package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "shrinkwrap.json",
    "db.json",  # json-server databases
    "graphemebreaktest.txt", "wordbreaktest.txt", "linebreaktest.txt",
    "emojitest.txt", "normalizationtest.txt",
    "thumbs.db", ".ds_store",
}


def _doc_id(site_id: str, item_id: str) -> str:
    return hashlib.sha256(f"sp:{site_id}:{item_id}".encode()).hexdigest()[:40]


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _delta_token_path() -> str:
    base = "/home/site/wwwroot" if os.path.isdir("/home/site/wwwroot") else "/tmp"
    return os.path.join(base, "sp_delta_tokens.json")


def _extract_permission_ids(permissions: List[Dict]) -> tuple[List[str], List[str]]:
    """Extract user and group Azure AD OIDs from Graph permission entries.

    Returns (acl_users, acl_groups) lists of Azure AD Object IDs.
    These are the same OIDs present in the user's JWT ``oid`` / ``groups``
    claims, so they can be matched at query time.
    """
    user_ids: Set[str] = set()
    group_ids: Set[str] = set()

    for perm in permissions:
        granted = perm.get("grantedToV2") or perm.get("grantedTo") or {}
        granted_list = perm.get("grantedToIdentitiesV2") or perm.get("grantedToIdentities") or []

        # Single grantee
        _collect_identity(granted, user_ids, group_ids)

        # Multiple grantees (link-based sharing)
        for identity_set in granted_list:
            _collect_identity(identity_set, user_ids, group_ids)

    return sorted(user_ids), sorted(group_ids)


def _collect_identity(identity_set: Dict, user_ids: Set[str], group_ids: Set[str]) -> None:
    """Extract Azure AD Object IDs from a single Graph identitySet object.

    Important: We only extract `user.id` and `group.id` which are Azure AD OIDs.
    The `siteUser.id` is a SharePoint site-specific numeric ID that does NOT
    match the user's JWT `oid` claim, so it must NOT be used for ACL filtering.
    """
    user = identity_set.get("user", {})
    if user.get("id"):
        user_ids.add(user["id"])
    group = identity_set.get("group", {})
    if group.get("id"):
        group_ids.add(group["id"])
    # NOTE: siteUser.id is intentionally NOT collected - it's a SharePoint
    # site-specific ID (e.g., '6') that won't match the Azure AD OID in JWTs.


class SharePointConnector(ConnectorBase):
    source_type = SOURCE_TYPE_SHAREPOINT

    def __init__(
        self,
        workspace_id: str,
        context_type: str = "org",
        site_urls: Optional[List[str]] = None,
        crawl_permissions: bool = True,
        include_libraries: Optional[List[str]] = None,
        exclude_paths: Optional[List[str]] = None,
    ) -> None:
        super().__init__(workspace_id, context_type)
        self.site_urls = site_urls or settings.sharepoint_site_list
        self._client = GraphClient()
        self._delta_tokens: Dict[str, str] = self._load_delta_tokens()
        self._crawl_permissions = crawl_permissions
        self._include_libraries = set(include_libraries) if include_libraries else None
        self._exclude_paths = [p.lower() for p in (exclude_paths or [])]

    # ── Delta token persistence ───────────────────────────────────────────────

    def _load_delta_tokens(self) -> Dict[str, str]:
        """Load persisted delta tokens from disk (survives restarts)."""
        path = _delta_token_path()
        try:
            with open(path, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    logger.info("Loaded %d SharePoint delta tokens from %s", len(data), path)
                    return data
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Could not load delta tokens from %s: %s", path, e)
        return {}

    def _save_delta_tokens(self) -> None:
        """Persist delta tokens to disk so incremental sync survives restarts."""
        path = _delta_token_path()
        try:
            with open(path, "w") as f:
                json.dump(self._delta_tokens, f)
        except Exception as e:
            logger.warning("Could not save delta tokens to %s: %s", path, e)

    # ── ConnectorBase interface ───────────────────────────────────────────────

    async def sync(self, full: bool = False) -> AsyncIterator[ConnectorDocument]:
        for site_url in self.site_urls:
            async for doc in self._sync_site(site_url, full=full):
                yield doc

    async def health_check(self) -> bool:
        try:
            await get_app_token()
            return True
        except Exception as e:
            self._logger.warning("SharePoint health check failed: %s", str(e))
            return False

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _sync_site(self, site_url: str, full: bool) -> AsyncIterator[ConnectorDocument]:
        try:
            site = await self._client.get_site_by_url(site_url)
        except Exception as e:
            self._logger.error("Failed to resolve site %s: %s", site_url, str(e))
            return

        site_id = site["id"]
        site_name = site.get("displayName", site_url)

        try:
            drives = await self._client.get_site_drives(site_id)
        except Exception as e:
            self._logger.error("Failed to list drives for %s: %s", site_url, str(e))
            return

        for drive in drives:
            drive_id = drive["id"]
            drive_name = drive.get("name", "")

            # Include/exclude library filtering
            if self._include_libraries and drive_name not in self._include_libraries:
                self._logger.debug("Skipping library '%s' (not in include list)", drive_name)
                continue

            drive_key = f"{site_id}::{drive_id}"
            delta_token = None if full else self._delta_tokens.get(drive_key)

            try:
                items, new_token = await self._client.get_drive_delta(
                    site_id, drive_id, delta_token
                )
                self._delta_tokens[drive_key] = new_token
                # Persist immediately so tokens survive a restart mid-sync
                self._save_delta_tokens()
            except Exception as e:
                self._logger.error("Delta failed for drive %s: %s", drive_id, str(e))
                continue

            for item in items:
                # ── Handle deleted items: yield a delete marker ────────────
                if item.get("deleted"):
                    item_id = item.get("id", "")
                    if item_id:
                        doc_id = _doc_id(site_id, item_id)
                        yield ConnectorDocument(
                            id=doc_id,
                            source_type=self.source_type,
                            source_id=f"{site_id}::{drive_id}",
                            workspace_id=self.workspace_id,
                            context_type=self.context_type,
                            title="__DELETED__",
                            content="",
                            metadata={"deleted": True},
                        )
                    continue
                if item.get("folder"):
                    continue
                name: str = item.get("name", "")
                ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
                if ext not in INDEXABLE_EXTENSIONS:
                    continue
                if name.lower() in SKIP_FILENAMES:
                    self._logger.debug("Skipping excluded filename: %s", name)
                    continue
                if item.get("size", 0) > MAX_FILE_BYTES:
                    self._logger.warning("Skipping oversized file: %s", name)
                    continue

                # Exclude path filtering
                item_path = (item.get("parentReference", {}).get("path", "") + "/" + name).lower()
                if any(excl in item_path for excl in self._exclude_paths):
                    self._logger.debug("Skipping excluded path: %s", name)
                    continue

                doc = await self._build_document(
                    site_id, site_name, site_url, drive_id, drive_name, item
                )
                if doc:
                    yield doc

    async def _build_document(
        self,
        site_id: str,
        site_name: str,
        site_url: str,
        drive_id: str,
        drive_name: str,
        item: Dict,
    ) -> Optional[ConnectorDocument]:
        item_id = item["id"]
        name = item.get("name", "")
        web_url = item.get("webUrl", "")
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ".txt"
        folder_path = item.get("parentReference", {}).get("path", "")

        try:
            raw = await self._client.download_item(site_id, drive_id, item_id)
        except Exception as e:
            self._logger.warning("Cannot download %s: %s", name, str(e))
            return None

        try:
            content = extract_text(raw, f"application/{ext.lstrip('.')}", name)
        except Exception as e:
            self._logger.warning("Text extraction failed for %s: %s", name, str(e))
            return None  # Don't index if we can't extract text

        # Guard: don't index empty or filename-only content
        if not content or not content.strip() or content.strip() == name:
            self._logger.warning("Empty or placeholder content for %s — skipping", name)
            return None

        # ── Per-item permission crawling ──────────────────────────────────────
        acl_users: List[str] = []
        acl_groups: List[str] = []
        if self._crawl_permissions:
            try:
                permissions = await self._client.get_item_permissions(
                    site_id, drive_id, item_id
                )
                acl_users, acl_groups = _extract_permission_ids(permissions)
            except Exception as e:
                # SECURITY: Fail closed — do not index document if we
                # cannot determine permissions.  Empty ACLs would make
                # the document visible to every workspace user.
                self._logger.warning(
                    "Permission fetch failed for %s: %s — skipping document (fail closed)",
                    name, str(e),
                )
                return None

        creator = (
            item.get("createdBy", {}).get("user", {}).get("email")
            or item.get("createdBy", {}).get("user", {}).get("displayName", "")
        )
        modifier = (
            item.get("lastModifiedBy", {}).get("user", {}).get("email")
            or item.get("lastModifiedBy", {}).get("user", {}).get("displayName", "")
        )
        sensitivity = item.get("sensitivityLabel", {}).get("displayName", "")

        return ConnectorDocument(
            id=_doc_id(site_id, item_id),
            source_type=self.source_type,
            source_id=f"{site_id}::{drive_id}",
            workspace_id=self.workspace_id,
            context_type=self.context_type,
            title=name,
            content=content,
            url=web_url,
            file_type=ext.lstrip("."),
            path=folder_path,
            last_modified=_parse_dt(item.get("lastModifiedDateTime")),
            created_at=_parse_dt(item.get("createdDateTime")),
            acl_users=acl_users,
            acl_groups=acl_groups,
            sensitivity_label=sensitivity,
            citation={
                "source": "SharePoint",
                "site": site_name,
                "site_url": site_url,
                "drive": drive_name or drive_id,
                "library": drive_name,
                "file": name,
                "url": web_url,
                "path": folder_path,
                "author": creator,
                "last_modified_by": modifier,
                "file_type": ext.lstrip("."),
                "size": item.get("size", 0),
                # Normalized keys read by query_pipeline for rich citations
                "last_modified": item.get("lastModifiedDateTime", ""),
                "file_path": folder_path,
                "site_name": site_name,
                "source_type": "sharepoint",
            },
            metadata={
                "site_id": site_id,
                "drive_id": drive_id,
                "item_id": item_id,
                "drive_name": drive_name,
                "etag": item.get("eTag", ""),
            },
        )

    def get_delta_token(self, drive_key: str) -> Optional[str]:
        return self._delta_tokens.get(drive_key)
