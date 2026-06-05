"""
Mela AI - Knowledge Base Search client.

Thin wrapper around Azure AI Search that mirrors the existing
``IndexManager`` pattern in ``app.services.search.index_manager``.
Phase 4 ships KB-only hybrid search; the schema is intentionally
simpler than the enterprise index because KB entries are summaries
not chunked documents.

Hard rules
----------

* Lazy and optional.  If ``settings.AZURE_SEARCH_KB_INDEX`` is blank,
  ``kb_search_client`` is ``None`` and the SQL fallback in
  ``SQLKnowledgeStore.search`` takes over.  The caller never sees a
  difference.
* Index creation is idempotent (``create_or_update_index``).  Schema
  conflicts auto-recreate the index, same as ``IndexManager``.
* Embedding dimensions are 1536 (text-embedding-3-small) — KB entries
  are short summaries, not enterprise-document chunks; the smaller /
  cheaper / faster small model is the right pick.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

from app.core.config import settings

logger = logging.getLogger(__name__)


# 1536 dims — text-embedding-3-small.  Enterprise index uses 3072
# (text-embedding-3-large); KB summaries are short so we use the
# smaller / cheaper model.  If you change this, also update
# ``openai_service.get_embedding`` to use a matching deployment.
KB_EMBED_DIMS = 1536


def _admin_credential() -> AzureKeyCredential:
    return AzureKeyCredential(settings.effective_search_admin_key)


def _query_credential() -> AzureKeyCredential:
    return AzureKeyCredential(settings.effective_search_query_key)


def _kb_fields() -> list:
    """Schema for the KB index.

    Strict subset of what KnowledgeEntry stores: identity (entry_id),
    isolation (tenant_id, user_id, profile_mode), classification
    (entry_type, source_worker_id, tags), retrievable text (title +
    summary), and the vector.  ``data_pointer`` and ``trace_id`` are
    retrievable for round-trip back to the SQL row.
    """
    return [
        SimpleField(
            name="entry_id", type=SearchFieldDataType.String,
            key=True, filterable=True,
        ),
        SimpleField(
            name="tenant_id", type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="user_id", type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="profile_mode", type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="entry_type", type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="source_worker_id", type=SearchFieldDataType.String,
            filterable=True,
        ),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="summary", type=SearchFieldDataType.String),
        SimpleField(
            name="tags",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SimpleField(
            name="trace_id", type=SearchFieldDataType.String,
            retrievable=True,
        ),
        SimpleField(
            name="data_pointer", type=SearchFieldDataType.String,
            retrievable=True,
        ),
        SimpleField(
            name="created_at",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True, sortable=True,
        ),
        SimpleField(
            name="expires_at",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
        ),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=KB_EMBED_DIMS,
            vector_search_profile_name="hnsw-profile",
        ),
    ]


def _vector_search_config() -> VectorSearch:
    return VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[
            VectorSearchProfile(
                name="hnsw-profile",
                algorithm_configuration_name="hnsw",
            )
        ],
    )


def _odata_literal(value: str) -> str:
    return (value or "").replace("'", "''")


def _isoformat(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── Client ───────────────────────────────────────────────────────────────


class KBSearchClient:
    """Hybrid keyword + vector search over the KB index.

    Constructed once at module import.  If
    ``AZURE_SEARCH_KB_INDEX`` is unset the module-level singleton stays
    ``None`` and the knowledge store falls back to SQL keyword search.
    """

    def __init__(self) -> None:
        self._endpoint = settings.AZURE_SEARCH_ENDPOINT
        self._index_name = settings.AZURE_SEARCH_KB_INDEX
        if not self._endpoint or not self._index_name:
            raise RuntimeError(
                "KBSearchClient requires AZURE_SEARCH_ENDPOINT and "
                "AZURE_SEARCH_KB_INDEX to be set"
            )
        self._index_client = SearchIndexClient(
            endpoint=self._endpoint, credential=_admin_credential(),
        )
        self._write_client = SearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=_admin_credential(),
        )
        self._read_client = SearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=_query_credential(),
        )

    # ── Index lifecycle ──────────────────────────────────────────────────

    def ensure_index(self) -> None:
        """Idempotent index create.  Recreates on schema conflict."""
        index = SearchIndex(
            name=self._index_name,
            fields=_kb_fields(),
            vector_search=_vector_search_config(),
        )
        try:
            self._index_client.create_or_update_index(index)
            logger.info("KB index '%s' ready", self._index_name)
        except HttpResponseError as e:
            err = str(e)
            if any(
                k in err for k in (
                    "CannotChangeExistingField",
                    "OperationNotAllowed",
                    "InvalidRequestParameter",
                    "UnknownField",
                )
            ):
                logger.warning(
                    "KB index '%s' schema conflict — deleting + recreating",
                    self._index_name,
                )
                try:
                    self._index_client.delete_index(self._index_name)
                    self._index_client.create_or_update_index(index)
                except Exception as inner:
                    logger.error(
                        "KB index recreate failed: %s", inner
                    )
                    raise
            else:
                logger.error(
                    "KB index ensure failed: %s", e.message
                )
                raise

    # ── Writes ───────────────────────────────────────────────────────────

    def upsert(
        self, *, entry: dict[str, Any], embedding: Optional[list[float]]
    ) -> None:
        """Upsert one KB entry (with optional embedding) into the index.

        ``entry`` is a flat dict matching the index schema; the caller
        is responsible for shape — this client doesn't transform.
        Failures here MUST not block ingestion: callers wrap in
        try/except and persist to SQL even when the search write fails.
        """
        doc = dict(entry)
        if embedding is not None and len(embedding) == KB_EMBED_DIMS:
            doc["content_vector"] = embedding
        try:
            self._write_client.upload_documents([doc])
        except Exception as exc:
            logger.warning(
                "KB index upsert failed entry_id=%s: %s",
                doc.get("entry_id"), exc,
            )
            raise

    def delete(self, entry_id: str) -> None:
        try:
            self._write_client.delete_documents([{"entry_id": entry_id}])
        except Exception as exc:
            logger.warning("KB delete failed entry_id=%s: %s", entry_id, exc)

    def delete_stale(self, *, now: Optional[datetime] = None) -> int:
        """Delete every KB entry whose ``expires_at`` is past *now*.

        Returns the number of deletions.  Best-effort: any error
        returns 0 and logs.  Mirrors the SQL ``expire_stale`` so the
        sweep keeps both stores in sync.
        """
        cutoff = _isoformat(now or datetime.now(timezone.utc))
        if cutoff is None:
            return 0
        try:
            results = self._read_client.search(
                "*",
                filter=f"expires_at lt {cutoff}",
                select=["entry_id"],
                top=1000,
            )
            ids = [r["entry_id"] for r in results]
            if not ids:
                return 0
            self._write_client.delete_documents(
                [{"entry_id": eid} for eid in ids]
            )
            return len(ids)
        except Exception as exc:
            logger.warning("KB delete_stale failed: %s", exc)
            return 0

    # ── Reads ────────────────────────────────────────────────────────────

    def search(
        self,
        *,
        tenant_id: Optional[str],
        user_id: str,
        query: str,
        embedding: Optional[list[float]] = None,
        limit: int = 5,
        entry_types: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Hybrid query: vector similarity + BM25 over title/summary.

        Filter logic mirrors ``SQLKnowledgeStore.search``: tenant
        match OR own-user fallback; expires_at is null or in the
        future; optional entry_type whitelist.  Returns raw search
        result dicts ordered by ``@search.score`` descending.
        """
        if not query or not query.strip():
            return []

        # ── OData filter ────────────────────────────────────────────
        clauses: list[str] = []
        if tenant_id:
            clauses.append(
                f"(tenant_id eq '{_odata_literal(tenant_id)}'"
                f" or user_id eq '{_odata_literal(user_id)}')"
            )
        else:
            clauses.append(f"user_id eq '{_odata_literal(user_id)}'")

        # Skip expired entries.  Search OData supports `lt`/`gt` on
        # DateTimeOffset; use the same cutoff trick the SQL path uses.
        cutoff = _isoformat(datetime.now(timezone.utc))
        if cutoff:
            clauses.append(
                f"(expires_at eq null or expires_at gt {cutoff})"
            )

        if entry_types:
            ored = " or ".join(
                f"entry_type eq '{_odata_literal(t)}'" for t in entry_types
            )
            clauses.append(f"({ored})")

        filter_expr = " and ".join(clauses)

        # ── Vector query (only if embedding present + correct dims) ─
        vector_queries = None
        if embedding and len(embedding) == KB_EMBED_DIMS:
            vector_queries = [
                VectorizedQuery(
                    vector=embedding,
                    k_nearest_neighbors=limit,
                    fields="content_vector",
                )
            ]

        try:
            results = self._read_client.search(
                search_text=query,
                filter=filter_expr,
                vector_queries=vector_queries,
                top=limit,
                select=[
                    "entry_id",
                    "tenant_id",
                    "user_id",
                    "profile_mode",
                    "entry_type",
                    "source_worker_id",
                    "title",
                    "summary",
                    "tags",
                    "trace_id",
                    "data_pointer",
                    "created_at",
                    "expires_at",
                ],
            )
            return [dict(r) for r in results]
        except Exception as exc:
            logger.warning("KB search failed: %s", exc)
            return []

    # ── Diagnostics ──────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        try:
            s = self._index_client.get_index_statistics(self._index_name)
            return {
                "index_name": self._index_name,
                "document_count": s.document_count,
                "storage_size_mb": round(s.storage_size / (1024 * 1024), 2),
            }
        except Exception as exc:
            return {"index_name": self._index_name, "error": str(exc)}


def _build_singleton() -> Optional[KBSearchClient]:
    if not settings.AZURE_SEARCH_KB_INDEX:
        logger.info(
            "KBSearchClient disabled: AZURE_SEARCH_KB_INDEX not set "
            "(SQL fallback active)"
        )
        return None
    if not settings.AZURE_SEARCH_ENDPOINT:
        logger.warning(
            "KBSearchClient disabled: AZURE_SEARCH_KB_INDEX is set but "
            "AZURE_SEARCH_ENDPOINT is blank"
        )
        return None
    try:
        return KBSearchClient()
    except Exception as exc:
        logger.warning("KBSearchClient init failed: %s — using SQL fallback", exc)
        return None


# Module-level singleton.  ``None`` means "Search is not configured;
# the SQL fallback is in effect" — callers MUST handle the None case.
kb_search_client: Optional[KBSearchClient] = _build_singleton()


def serialise_for_index(*, row: Any, tags: list[str]) -> dict[str, Any]:
    """Map a ``KnowledgeEntry`` ORM row → flat search-document dict.

    Centralised so ingest/upsert/test mocks all see the same shape.
    Only used when the search client is configured; SQL-only paths
    never touch this.
    """
    return {
        "entry_id": row.entry_id,
        "tenant_id": row.tenant_id or "",
        "user_id": row.user_id,
        "profile_mode": row.profile_mode,
        "entry_type": row.entry_type,
        "source_worker_id": row.source_worker_id or "",
        "title": row.title,
        "summary": row.summary,
        "tags": list(tags or []),
        "trace_id": row.trace_id or "",
        "data_pointer": row.data_pointer or "",
        "created_at": _isoformat(row.created_at),
        "expires_at": _isoformat(row.expires_at),
    }


# Re-export for convenience
__all__ = [
    "KBSearchClient",
    "KB_EMBED_DIMS",
    "kb_search_client",
    "serialise_for_index",
]


# Quiet ruff for the unused json import — it's reserved for future
# debug helpers in this module.
_ = json
