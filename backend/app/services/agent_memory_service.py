"""
Mela AI - Agent Memory Service

Orchestrates the lifecycle of user-curated knowledge items (uploaded files,
crawled websites, and templates):

    create_from_upload(user, scope, tag, file_bytes, filename, content_type)
    create_from_url(user, scope, tag, url)
    list_items(user, scope=?, tag=?)
    get_item(user, item_id)
    delete_item(user, item_id)
    reindex(user, item_id)
    set_session_disabled(user, item_id, conversation_id, disabled)
    process(item_id)                 # state-machine driver

Each item moves through:
    pending → parsing → embedding → ready
                     → crawling → embedding → ready
                     → failed (with error_message)

The service writes one row per AgentMemoryItem and 1+ chunks into Azure AI
Search (with memory_scope / tag / agent_memory_item_id metadata so retrieval
can filter, boost, and respect per-conversation soft-disable).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import AgentMemoryItem, User
from app.services.connectors.base import (
    ConnectorDocument,
    SOURCE_TYPE_AGENT_MEMORY,
)

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

VALID_SCOPES = {"personal", "workspace", "tenant"}
VALID_TAGS = {"knowledge", "template", "brand", "policy", "demo"}
TEMPLATE_FILE_TYPES = {"docx", "md"}

from app.services.blob_storage import blob_store as _blob_store  # noqa: E402

# Local fallback root — only used when Azure Storage is not configured.
_STORAGE_ROOT = Path(
    getattr(settings, "AGENT_MEMORY_STORAGE_ROOT", None)
    or os.environ.get("AGENT_MEMORY_STORAGE_ROOT")
    or os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent_memory")
).resolve()


# ── Errors ───────────────────────────────────────────────────────────────────


class AgentMemoryError(Exception):
    """Base error for agent_memory_service."""


class InvalidScopeError(AgentMemoryError):
    pass


class InvalidTagError(AgentMemoryError):
    pass


class ItemNotFoundError(AgentMemoryError):
    pass


class ForbiddenError(AgentMemoryError):
    pass


# ── Service ──────────────────────────────────────────────────────────────────


class AgentMemoryService:
    """Owns the agent_memory_items table and Azure AI Search side-effects."""

    def __init__(self) -> None:
        self._index_name = settings.AZURE_SEARCH_INDEX_NAME
        try:
            _STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - filesystem permissions
            logger.warning("Could not create agent memory storage root %s: %s",
                           _STORAGE_ROOT, exc)

    # ── Validation ───────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_scope(scope: str) -> str:
        s = (scope or "personal").lower().strip()
        if s not in VALID_SCOPES:
            raise InvalidScopeError(
                f"scope must be one of {sorted(VALID_SCOPES)}, got {scope!r}"
            )
        return s

    @staticmethod
    def _normalise_tag(tag: str) -> str:
        t = (tag or "knowledge").lower().strip()
        if t not in VALID_TAGS:
            raise InvalidTagError(
                f"tag must be one of {sorted(VALID_TAGS)}, got {tag!r}"
            )
        return t

    @staticmethod
    def _ensure_owner_or_admin(user: User, item: AgentMemoryItem) -> None:
        if item.user_id == user.id:
            return
        # Tenant-scoped items can be deleted by tenant admins; treated separately
        # at the endpoint layer. By default, only the owner may mutate an item.
        raise ForbiddenError("not the owner of this agent memory item")

    # ── Storage helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    async def _store_bytes(
        item_id: str, user_id: str, filename: str, data: bytes
    ) -> str:
        """Upload raw bytes to Azure Blob Storage (or local disk as fallback).

        Returns a blob reference string suitable for passing back to _read_bytes.
        """
        safe_name = os.path.basename(filename) or "upload.bin"
        blob_name = f"agent_memory/{user_id}/{item_id}/{safe_name}"
        ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        ct_map = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "txt": "text/plain", "md": "text/markdown",
            "html": "text/html", "csv": "text/csv", "json": "application/json",
        }
        content_type = ct_map.get(ext, "application/octet-stream")
        ref = await _blob_store.upload(data, blob_name, content_type)
        logger.info("[AgentMemory] stored %s (%d bytes) ref=%s", safe_name, len(data), ref)
        return ref

    @staticmethod
    async def _read_bytes(blob_ref: str) -> Optional[bytes]:
        """Download bytes from blob storage or local fallback."""
        if not blob_ref:
            return None
        return await _blob_store.download(blob_ref)

    # ── ACL derivation ───────────────────────────────────────────────────────

    @staticmethod
    def _build_acl(scope: str, user: User, tenant_id: Optional[str]) -> Tuple[List[str], List[str]]:
        """Return (acl_users, acl_groups) for a given scope.

        - personal  → only the owner. We include BOTH the Entra OID
          (`user.azure_id`) and the DB primary key (`user.id`) so retrieval
          succeeds regardless of which identifier the chat path uses to build
          the ACL filter (dev-mode auth resolves to `user.id`, production auth
          resolves to the Entra OID).
        - workspace → all members of the tenant (acl_groups=[tenant_id])
        - tenant    → tenant-public; empty ACLs (workspace_id filter still scopes)
        """
        if scope == "personal":
            ids: List[str] = []
            for _v in (getattr(user, "azure_id", None), str(getattr(user, "id", "") or "")):
                if _v and _v not in ids:
                    ids.append(_v)
            return ids, []
        if scope == "workspace":
            return [], [tenant_id] if tenant_id else []
        # tenant
        return [], []

    # ── Workspace id helper ──────────────────────────────────────────────────

    @staticmethod
    def _workspace_id(user: User, tenant_id: Optional[str], scope: str) -> str:
        """All agent-memory rows live under tenant_id when in work-mode, or
        under the user's personal namespace otherwise."""
        if scope == "personal" or not tenant_id:
            return f"user:{user.id}"
        return tenant_id

    # ── Public API: create ───────────────────────────────────────────────────

    async def create_from_upload(
        self,
        db: AsyncSession,
        user: User,
        *,
        scope: str,
        tag: str,
        tenant_id: Optional[str],
        filename: str,
        content_type: str,
        data: bytes,
        title: Optional[str] = None,
    ) -> AgentMemoryItem:
        """Create a new item from raw uploaded bytes and schedule processing."""
        from app.services.file_security import scan_file

        scope_n = self._normalise_scope(scope)
        tag_n = self._normalise_tag(tag)

        # Security scan — refuse anything obviously dangerous up-front.
        scan = scan_file(data, filename, content_type)
        if not scan.safe:
            raise AgentMemoryError(f"file rejected: {scan.reason}")

        content_hash = self._hash_bytes(data)

        # Dedupe: same user + same content_hash means re-upload.
        existing = await db.scalar(
            select(AgentMemoryItem).where(
                and_(
                    AgentMemoryItem.user_id == user.id,
                    AgentMemoryItem.content_hash == content_hash,
                )
            )
        )
        if existing is not None:
            logger.info("Duplicate upload for user %s — returning existing item %s",
                        user.id, existing.id)
            return existing

        item_id = str(uuid.uuid4())
        blob_url = await self._store_bytes(item_id, user.id, filename, data)

        # Detect file type now so the UI can render an icon immediately.
        from app.services.document_service import DocumentProcessor
        file_type = DocumentProcessor().detect_type(content_type, filename)

        item = AgentMemoryItem(
            id=item_id,
            user_id=user.id,
            tenant_id=tenant_id if scope_n != "personal" else None,
            scope=scope_n,
            tag=tag_n,
            source_type="upload",
            source_id=blob_url,
            title=title or filename,
            url=None,
            blob_url=blob_url,
            file_type=file_type,
            file_size=len(data),
            content_hash=content_hash,
            status="pending",
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)

        # Hand off to the worker. Best-effort; failures do not block the API.
        self._enqueue(item.id)
        return item

    async def create_from_url(
        self,
        db: AsyncSession,
        user: User,
        *,
        scope: str,
        tag: str,
        tenant_id: Optional[str],
        url: str,
        title: Optional[str] = None,
    ) -> AgentMemoryItem:
        """Create a new item that points at an external URL and schedule a crawl."""
        scope_n = self._normalise_scope(scope)
        tag_n = self._normalise_tag(tag)

        if not url or not url.lower().startswith(("http://", "https://")):
            raise AgentMemoryError("url must start with http:// or https://")

        # SSRF guard runs again inside the connector; we also block obviously
        # invalid inputs at the API edge.
        from app.services.connectors.user_web_connector import (  # noqa: WPS433
            is_safe_public_url,
        )
        ok, reason = is_safe_public_url(url)
        if not ok:
            raise AgentMemoryError(f"url rejected: {reason}")

        # Stable hash so re-adding the same URL is a no-op.
        content_hash = hashlib.sha256(url.lower().encode("utf-8")).hexdigest()
        existing = await db.scalar(
            select(AgentMemoryItem).where(
                and_(
                    AgentMemoryItem.user_id == user.id,
                    AgentMemoryItem.content_hash == content_hash,
                )
            )
        )
        if existing is not None:
            return existing

        item = AgentMemoryItem(
            id=str(uuid.uuid4()),
            user_id=user.id,
            tenant_id=tenant_id if scope_n != "personal" else None,
            scope=scope_n,
            tag=tag_n,
            source_type="web",
            source_id=url,
            title=title or url,
            url=url,
            blob_url=None,
            file_type="html",
            content_hash=content_hash,
            status="pending",
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)

        self._enqueue(item.id)
        return item

    # ── Public API: read ─────────────────────────────────────────────────────

    async def list_items(
        self,
        db: AsyncSession,
        user: User,
        *,
        tenant_id: Optional[str] = None,
        scope: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[AgentMemoryItem]:
        """Return items the user is allowed to see.

        Personal items: owner only.
        Workspace/tenant items: any user in the same tenant.
        """
        clauses = []
        # Owner can always see their own.
        own = AgentMemoryItem.user_id == user.id
        if tenant_id:
            shared = and_(
                AgentMemoryItem.tenant_id == tenant_id,
                AgentMemoryItem.scope.in_(["workspace", "tenant"]),
            )
            visibility = own | shared
        else:
            visibility = own
        clauses.append(visibility)

        if scope:
            clauses.append(AgentMemoryItem.scope == self._normalise_scope(scope))
        if tag:
            clauses.append(AgentMemoryItem.tag == self._normalise_tag(tag))

        rows = await db.scalars(
            select(AgentMemoryItem)
            .where(and_(*clauses))
            .order_by(AgentMemoryItem.created_at.desc())
        )
        return list(rows.all())

    async def get_item(
        self, db: AsyncSession, user: User, item_id: str,
        *, tenant_id: Optional[str] = None,
    ) -> AgentMemoryItem:
        item = await db.get(AgentMemoryItem, item_id)
        if item is None:
            raise ItemNotFoundError(item_id)
        # Read access: owner OR same-tenant for shared scopes
        if item.user_id != user.id:
            if not (tenant_id and item.tenant_id == tenant_id and item.scope in ("workspace", "tenant")):
                raise ForbiddenError("no access to this item")
        return item

    # ── Public API: mutate ───────────────────────────────────────────────────

    async def delete_item(self, db: AsyncSession, user: User, item_id: str) -> None:
        item = await db.get(AgentMemoryItem, item_id)
        if item is None:
            return
        self._ensure_owner_or_admin(user, item)
        # Delete chunks from search index first
        try:
            from app.services.search.index_manager import index_manager
            if index_manager is not None:
                index_manager.delete_by_source(self._index_name, item.id)
        except Exception as exc:
            logger.warning("Search delete failed for item %s: %s", item.id, exc)
        # Delete blob (Blob Storage or local fallback)
        if item.blob_url:
            try:
                await _blob_store.delete(item.blob_url)
            except Exception as exc:
                logger.warning("Blob delete failed for %s: %s", item.id, exc)
        await db.delete(item)
        await db.commit()

    async def reindex(self, db: AsyncSession, user: User, item_id: str) -> AgentMemoryItem:
        item = await db.get(AgentMemoryItem, item_id)
        if item is None:
            raise ItemNotFoundError(item_id)
        self._ensure_owner_or_admin(user, item)
        item.status = "pending"
        item.error_message = None
        item.chunk_count = 0
        await db.commit()
        await db.refresh(item)
        self._enqueue(item.id)
        return item

    async def set_session_disabled(
        self,
        db: AsyncSession,
        user: User,
        item_id: str,
        conversation_id: str,
        disabled: bool,
    ) -> AgentMemoryItem:
        item = await db.get(AgentMemoryItem, item_id)
        if item is None:
            raise ItemNotFoundError(item_id)
        # Read access is enough to mute for one's own conversation.
        if item.user_id != user.id:
            raise ForbiddenError("only the owner can change session state")
        current = dict(item.session_disabled or {})
        if disabled:
            current[conversation_id] = True
        else:
            current.pop(conversation_id, None)
        item.session_disabled = current
        await db.commit()
        await db.refresh(item)
        return item

    # ── Worker: process one item end-to-end ──────────────────────────────────

    async def process(self, item_id: str) -> None:
        """Run the state machine for one item. Safe to call repeatedly.

        Uses a distributed Redis lock (SET NX EX=300) to ensure only one
        replica processes a given item at a time.  If the lock cannot be
        acquired, the call is a no-op (another replica holds the lock).
        """
        from app.core import database as _db_mod
        from app.core.database import async_session_maker
        from app.core.redis_client import get_redis, key as rkey

        if not _db_mod.db_available:
            logger.warning("DB unavailable — skipping process(%s)", item_id)
            return

        # ── Distributed lock ─────────────────────────────────────────────────
        lock_key = rkey("lock", "agmem", item_id)
        status_key = rkey("agmem", "status", item_id)
        r = await get_redis()
        lock_acquired = False
        if r is not None:
            try:
                lock_acquired = await r.set(lock_key, "1", nx=True, ex=300)
            except Exception as _le:
                logger.debug("Redis lock unavailable for agmem %s: %s", item_id, _le)
                lock_acquired = True  # proceed without distributed lock
        else:
            lock_acquired = True  # Redis not configured — no lock needed

        if not lock_acquired:
            logger.debug("agmem process skipped (lock held by another replica): %s", item_id)
            return

        async def _set_status(status: str) -> None:
            if r is not None:
                try:
                    await r.set(status_key, status, ex=3600)
                    await r.publish("mela:agmem:events", f"{item_id}:{status}")
                except Exception:
                    pass

        try:
            await _set_status("processing")

            async with async_session_maker() as db:
                item = await db.get(AgentMemoryItem, item_id)
                if item is None:
                    logger.warning("process: item %s not found", item_id)
                    return

                try:
                    user = await db.get(User, item.user_id)
                    if user is None:
                        raise AgentMemoryError("owner user no longer exists")

                    # 1. Acquire raw text
                    if item.source_type == "upload":
                        text, metadata = await self._extract_upload(item)
                    elif item.source_type == "web":
                        text, metadata = await self._crawl_web(item)
                    else:
                        raise AgentMemoryError(
                            f"unsupported source_type for processing: {item.source_type}"
                        )

                    if not text or not text.strip():
                        raise AgentMemoryError("no extractable text")

                    item.status = "embedding"
                    await _set_status("embedding")
                    logger.info(
                        "agent_memory.transition item=%s status=embedding scope=%s tag=%s",
                        item.id, item.scope, item.tag,
                    )
                    await db.commit()

                    # 2. Build a ConnectorDocument and ingest
                    acl_users, acl_groups = self._build_acl(item.scope, user, item.tenant_id)
                    workspace_id = self._workspace_id(user, item.tenant_id, item.scope)

                    doc = ConnectorDocument(
                        id=item.id,
                        source_type=SOURCE_TYPE_AGENT_MEMORY,
                        source_id=item.id,  # also used as delete key
                        workspace_id=workspace_id,
                        context_type="org" if item.tenant_id else "personal",
                        title=item.title,
                        content=text,
                        url=item.url or "",
                        path=item.blob_url or "",
                        file_type=item.file_type or "",
                        last_modified=datetime.now(timezone.utc),
                        created_at=item.created_at,
                        acl_users=acl_users,
                        acl_groups=acl_groups,
                        sensitivity_label="",
                        citation={
                            "title": item.title,
                            "url": item.url,
                            "source_type": "agent_memory",
                            "tag": item.tag,
                            "scope": item.scope,
                            "agent_memory_item_id": item.id,
                        },
                        memory_scope=item.scope,
                        tag=item.tag,
                        agent_memory_item_id=item.id,
                    )

                    from app.services.search.ingestion import ingestion_pipeline
                    count = await ingestion_pipeline.ingest_document(doc, self._index_name)

                    # 3. If this is a template, parse and persist its schema
                    template_schema: Optional[Dict[str, Any]] = None
                    if item.tag == "template" and item.file_type in TEMPLATE_FILE_TYPES:
                        try:
                            from app.services.template_service import template_service
                            template_schema = template_service.parse(
                                text=text,
                                file_type=item.file_type,
                                raw_bytes=await self._read_bytes(item.blob_url) if item.blob_url else None,
                            )
                        except Exception as exc:
                            logger.warning("Template parse failed for %s: %s", item.id, exc)

                    # 4. Mark ready
                    item.chunk_count = int(count or 0)
                    item.page_count = int(metadata.get("page_count", 0) or 0)
                    if template_schema:
                        item.template_schema_json = template_schema
                    elif metadata.get("structured_profile") and item.tag != "template":
                        # Tabular files (csv/xlsx) store their pandas profile here so
                        # the chat pipeline can surface column names + stats as a
                        # [DATA_CARD] block in the system prompt.
                        item.template_schema_json = {
                            "kind": "data_card",
                            "profile": metadata["structured_profile"],
                        }
                    item.last_synced_at = datetime.utcnow()
                    item.status = "ready"
                    item.error_message = None
                    await db.commit()
                    await _set_status("ready")
                    logger.info(
                        "Agent memory item %s ready: %d chunks (%s/%s)",
                        item.id, item.chunk_count, item.scope, item.tag,
                    )

                except Exception as exc:  # noqa: BLE001 - we want to capture all
                    logger.exception("Agent memory processing failed for %s", item_id)
                    item.status = "failed"
                    item.error_message = str(exc)[:1000]
                    await _set_status("failed")
                    try:
                        await db.commit()
                    except Exception:
                        pass

        finally:
            # Release distributed lock regardless of outcome.
            if r is not None and lock_acquired:
                try:
                    await r.delete(lock_key)
                except Exception:
                    pass

    # ── Internal: extract & crawl ────────────────────────────────────────────

    async def _extract_upload(self, item: AgentMemoryItem) -> Tuple[str, Dict[str, Any]]:
        from app.services.document_service import DocumentProcessor

        async with self._db_lock_noop():
            pass  # placeholder for future row-level locking
        item.status = "parsing"
        logger.info(
            "agent_memory.transition item=%s status=parsing source_type=%s",
            item.id, item.source_type,
        )
        # We commit in the caller; here we just do work.
        data = await self._read_bytes(item.blob_url or "")
        if data is None:
            raise AgentMemoryError("upload bytes no longer available")

        proc = DocumentProcessor()
        # Best-effort content-type guess from extension
        ext = (item.file_type or "").lower()
        ct_map = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "html": "text/html", "txt": "text/plain", "md": "text/markdown",
            "csv": "text/csv", "json": "application/json",
        }
        text, metadata = proc.extract_text(data, ct_map.get(ext, ""), item.title)
        return text, metadata

    async def _crawl_web(self, item: AgentMemoryItem) -> Tuple[str, Dict[str, Any]]:
        from app.services.connectors.user_web_connector import UserWebConnector

        item.status = "crawling"
        logger.info(
            "agent_memory.transition item=%s status=crawling url=%s",
            item.id, item.url,
        )
        connector = UserWebConnector(
            workspace_id=item.tenant_id or f"user:{item.user_id}",
            context_type="org" if item.tenant_id else "personal",
            seed_url=item.url or item.source_id,
        )
        # Aggregate text from all crawled pages into a single content blob.
        # The chunker takes care of splitting it later.
        parts: List[str] = []
        page_count = 0
        async for page_doc in connector.sync(full=True):
            if page_doc.content:
                header = f"\n\n# {page_doc.title}\n[{page_doc.url}]\n\n"
                parts.append(header + page_doc.content)
                page_count += 1
        return "".join(parts), {"page_count": page_count}

    # ── Job dispatch ─────────────────────────────────────────────────────────

    # Strong references to in-flight background tasks. Without this set,
    # `loop.create_task(...)` returns a Task that may be garbage-collected
    # mid-flight (the event loop only holds a weak reference), silently
    # killing ingestion. See https://docs.python.org/3/library/asyncio-task.html
    # #asyncio.create_task — "Important: Save a reference to the result of
    # this function, to avoid a task disappearing mid-execution."
    _bg_tasks: "set[asyncio.Task]" = set()

    async def _safe_process(self, item_id: str) -> None:
        """Run process() with a top-level guard so ANY failure is logged AND
        recorded on the item row (status=failed, error_message=...)."""
        try:
            await self.process(item_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "agent_memory: top-level failure processing item %s", item_id,
            )
            # Best-effort: mark the row failed so the UI surfaces something.
            try:
                from app.core.database import async_session_maker
                async with async_session_maker() as db:
                    item = await db.get(AgentMemoryItem, item_id)
                    if item is not None and item.status not in {"ready", "failed"}:
                        item.status = "failed"
                        item.error_message = f"worker crashed: {exc!r}"[:1000]
                        await db.commit()
            except Exception:
                logger.exception(
                    "agent_memory: also failed to mark item %s as failed",
                    item_id,
                )

    def _enqueue(self, item_id: str) -> None:
        """Schedule async processing without blocking the API thread."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. invoked from a sync test) — run synchronously.
            asyncio.run(self._safe_process(item_id))
            return
        task = loop.create_task(self._safe_process(item_id))
        # Keep a strong reference until the task completes.
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ── Misc ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _db_lock_noop():
        class _Noop:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
        return _Noop()


# Singleton
agent_memory_service = AgentMemoryService()
