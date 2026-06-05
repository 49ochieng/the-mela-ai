"""
Mela AI - OneDrive Connector (app-only background sync)
Crawls any user's OneDrive using app-only client-credentials flow.
No delegated token required for background/scheduled sync.
ACL is set to the owning user's OID so only they can retrieve their docs.
"""

from __future__ import annotations

import hashlib
import logging
from typing import AsyncIterator, Dict, Optional

from app.services.connectors.base import ConnectorBase, ConnectorDocument, SOURCE_TYPE_ONEDRIVE
from app.services.connectors.graph_client import GraphClient
from app.services.connectors.sharepoint import INDEXABLE_EXTENSIONS, MAX_FILE_BYTES, SKIP_FILENAMES, _parse_dt
from app.services.document_service import extract_text

logger = logging.getLogger(__name__)

# Delta-token key prefix used in the ingestion worker's DB store
_DELTA_KEY_PREFIX = "onedrive"


def _doc_id(user_id: str, item_id: str) -> str:
    return hashlib.sha256(f"od:{user_id}:{item_id}".encode()).hexdigest()[:40]


class OneDriveConnector(ConnectorBase):
    source_type = SOURCE_TYPE_ONEDRIVE

    def __init__(
        self,
        workspace_id: str,
        context_type: str = "personal",
        user_id: str = "",
        # delegated_token kept as optional kwarg for backward compat — ignored.
        # App-only token is now always used for background sync.
        delegated_token: str = "",
    ) -> None:
        super().__init__(workspace_id, context_type)
        # SECURITY: user_id is mandatory — empty user_id would produce
        # documents with empty ACLs, making them visible to all workspace users.
        if not user_id or not user_id.strip():
            raise ValueError(
                "OneDriveConnector requires a non-empty user_id for ACL isolation"
            )
        # App-only client — no delegated token required
        self._client = GraphClient()
        self._user_id = user_id.strip()
        # New delta token returned by the most recent sync call.
        # Persisted externally by the ingestion worker (not kept in memory across restarts).
        self._new_delta_token: Optional[str] = None

    def get_delta_token(self, source_id: str) -> Optional[str]:  # noqa: ARG002
        """Return the delta token produced by the most recent sync() call.

        The ingestion worker calls this after sync() to persist the new token
        to the database so incremental sync survives restarts.
        """
        return self._new_delta_token

    async def sync(self, full: bool = False, delta_token: Optional[str] = None) -> AsyncIterator[ConnectorDocument]:
        """Yield ConnectorDocuments from the user's OneDrive.

        Args:
            full:        If True, ignore any delta token and do a full crawl.
            delta_token: Previously-persisted delta token (provided by
                         the ingestion worker from the DB, not stored in-memory).
        """
        use_token = None if full else delta_token
        try:
            items, new_token = await self._client.get_user_drive_delta(
                self._user_id, use_token
            )
            self._new_delta_token = new_token
        except Exception as e:
            self._logger.error(
                "OneDrive app-only delta for user %s failed: %s",
                self._user_id, str(e),
            )
            return

        for item in items:
            if item.get("folder") or item.get("deleted"):
                continue
            name: str = item.get("name", "")
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if ext not in INDEXABLE_EXTENSIONS:
                continue
            if name.lower() in SKIP_FILENAMES:
                logger.debug("Skipping excluded filename: %s", name)
                continue
            if item.get("size", 0) > MAX_FILE_BYTES:
                continue

            doc = await self._build_document(item)
            if doc:
                yield doc

    async def _build_document(self, item: Dict) -> Optional[ConnectorDocument]:
        item_id = item["id"]
        name = item.get("name", "")
        web_url = item.get("webUrl", "")
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ".txt"

        try:
            raw = await self._client.download_user_drive_item(self._user_id, item_id)
            content = extract_text(raw, f"application/{ext.lstrip('.')}", name)
        except Exception as e:
            self._logger.warning("Failed to process OneDrive file %s: %s", name, str(e))
            return None

        return ConnectorDocument(
            id=_doc_id(self._user_id, item_id),
            source_type=self.source_type,
            source_id=self._user_id or "onedrive",
            workspace_id=self.workspace_id,
            context_type=self.context_type,
            title=name,
            content=content,
            url=web_url,
            file_type=ext.lstrip("."),
            path=item.get("parentReference", {}).get("path", ""),
            last_modified=_parse_dt(item.get("lastModifiedDateTime")),
            created_at=_parse_dt(item.get("createdDateTime")),
            # Only the owner can see their OneDrive docs (enforced at constructor)
            acl_users=[self._user_id],
            citation={
                "source": "OneDrive",
                "file": name,
                "url": web_url,
                "author": item.get("createdBy", {}).get("user", {}).get("displayName", ""),
                "last_modified": item.get("lastModifiedDateTime", ""),
                "file_path": item.get("parentReference", {}).get("path", ""),
                "source_type": "onedrive",
                "site_name": "OneDrive",
            },
        )

    async def health_check(self) -> bool:
        """Verify app-only access to this user's OneDrive."""
        try:
            # A successful delta response confirms both auth and drive access
            _, _ = await self._client.get_user_drive_delta(
                self._user_id, delta_token=None
            )
            return True
        except Exception as e:
            self._logger.warning(
                "OneDrive health check failed for user %s: %s",
                self._user_id, str(e),
            )
            return False
