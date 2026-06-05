"""
Mela AI — Azure Blob Storage service

Thin wrapper around azure-storage-blob.  Falls back gracefully to local-disk
when AZURE_STORAGE_CONNECTION_STRING is not configured (unit tests / cold local
dev without Azure credentials).

Usage
-----
from app.services.blob_storage import blob_store

url  = await blob_store.upload(data, blob_name, content_type)
data = await blob_store.download(blob_name)
await blob_store.delete(blob_name)
ok   = blob_store.is_configured()
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_FALLBACK_ROOT = Path(
    os.environ.get("BLOB_FALLBACK_ROOT", "")
    or os.path.join(os.path.dirname(__file__), "..", "..", "data", "blob_fallback")
).resolve()


class BlobStorageService:
    """Async-friendly wrapper.  Uses a thread-pool for the sync SDK calls."""

    def __init__(self) -> None:
        self._conn_str = settings.AZURE_STORAGE_CONNECTION_STRING
        self._container = settings.AZURE_STORAGE_CONTAINER_AGENT_MEMORY
        self._client = None  # lazy

    # ── Public helpers ────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        return bool(self._conn_str)

    async def upload(
        self,
        data: bytes,
        blob_name: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload bytes and return a URL/path that can later be passed to download()."""
        if not self.is_configured():
            return self._local_write(data, blob_name)
        return await asyncio.get_event_loop().run_in_executor(
            None, self._sync_upload, data, blob_name, content_type
        )

    async def download(self, blob_ref: str) -> Optional[bytes]:
        """Download bytes from a blob URL or local fallback path."""
        if blob_ref.startswith("file://") or not self.is_configured():
            return self._local_read(blob_ref)
        # blob_ref is just the blob name when from Azure
        return await asyncio.get_event_loop().run_in_executor(
            None, self._sync_download, blob_ref
        )

    async def delete(self, blob_ref: str) -> None:
        """Delete a blob (no-op if not found)."""
        if blob_ref.startswith("file://") or not self.is_configured():
            self._local_delete(blob_ref)
            return
        await asyncio.get_event_loop().run_in_executor(
            None, self._sync_delete, blob_ref
        )

    # ── Sync internals (run in thread-pool) ──────────────────────────────────

    def _get_client(self):
        if self._client is None:
            from azure.storage.blob import BlobServiceClient
            self._client = BlobServiceClient.from_connection_string(self._conn_str)
        return self._client

    def _sync_upload(self, data: bytes, blob_name: str, content_type: str) -> str:
        client = self._get_client()
        container = client.get_container_client(self._container)
        try:
            container.create_container()
        except Exception:
            pass  # already exists
        blob = container.get_blob_client(blob_name)
        blob.upload_blob(
            data,
            overwrite=True,
            content_settings=_content_settings(content_type),
        )
        url = blob.url
        logger.debug("[Blob] uploaded %s (%d bytes) → %s", blob_name, len(data), url)
        return blob_name  # return name so download() can look it up

    def _sync_download(self, blob_name: str) -> Optional[bytes]:
        try:
            client = self._get_client()
            blob = client.get_blob_client(container=self._container, blob=blob_name)
            stream = blob.download_blob()
            data = stream.readall()
            logger.debug("[Blob] downloaded %s (%d bytes)", blob_name, len(data))
            return data
        except Exception as exc:
            logger.warning("[Blob] download failed for %s: %s", blob_name, exc)
            return None

    def _sync_delete(self, blob_name: str) -> None:
        try:
            client = self._get_client()
            blob = client.get_blob_client(container=self._container, blob=blob_name)
            blob.delete_blob(delete_snapshots="include")
            logger.debug("[Blob] deleted %s", blob_name)
        except Exception as exc:
            logger.debug("[Blob] delete skipped for %s: %s", blob_name, exc)

    # ── Local fallback ────────────────────────────────────────────────────────

    def _local_write(self, data: bytes, blob_name: str) -> str:
        path = _FALLBACK_ROOT / blob_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.as_uri()

    def _local_read(self, ref: str) -> Optional[bytes]:
        if ref.startswith("file://"):
            from urllib.parse import unquote, urlparse
            parsed = urlparse(ref)
            path = unquote(parsed.path)
            if os.name == "nt" and path.startswith("/") and len(path) > 3 and path[2] == ":":
                path = path[1:]
        else:
            path = str(_FALLBACK_ROOT / ref)
        try:
            return Path(path).read_bytes()
        except OSError as exc:
            logger.warning("[Blob] local read failed %s: %s", ref, exc)
            return None

    def _local_delete(self, ref: str) -> None:
        if ref.startswith("file://"):
            from urllib.parse import unquote, urlparse
            parsed = urlparse(ref)
            path = unquote(parsed.path)
            if os.name == "nt" and path.startswith("/") and len(path) > 3 and path[2] == ":":
                path = path[1:]
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


def _content_settings(content_type: str):
    from azure.storage.blob import ContentSettings
    return ContentSettings(content_type=content_type)


# Singleton
blob_store = BlobStorageService()
