"""
Mela AI - Azure AI Search Index Manager
Creates / updates the documents index, vector index, and query-cache index.
Uses the admin key for write operations; never logs key values.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.models import VectorizedQuery
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
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)

# Embedding dimensions for text-embedding-3-large (upgraded from 3-small/1536)
EMBED_DIMS = 3072


def _admin_credential() -> AzureKeyCredential:
    return AzureKeyCredential(settings.effective_search_admin_key)


def _query_credential() -> AzureKeyCredential:
    return AzureKeyCredential(settings.effective_search_query_key)


def _vector_search_config() -> VectorSearch:
    return VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw")],
    )


def _document_fields() -> List:
    """Schema shared by both the documents index and the vector index."""
    return [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SimpleField(name="workspace_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="context_type", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_type", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="url", type=SearchFieldDataType.String, retrievable=True),
        SimpleField(name="path", type=SearchFieldDataType.String, retrievable=True),
        SimpleField(name="file_type", type=SearchFieldDataType.String, filterable=True),
        SimpleField(
            name="last_modified",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="created_at",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
        ),
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(
            name="chunk_index",
            type=SearchFieldDataType.Int32,
            filterable=True,
            sortable=True,
        ),
        SimpleField(name="citation_json", type=SearchFieldDataType.String, retrievable=True),
        SimpleField(
            name="acl_users",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SimpleField(
            name="acl_groups",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SimpleField(name="sensitivity_label", type=SearchFieldDataType.String, filterable=True),
        # ── Agent Memory metadata (filterable for scope/tag-aware retrieval) ──
        # 'personal' | 'workspace' | 'tenant' (mirrors AgentMemoryItem.scope).
        SimpleField(name="memory_scope", type=SearchFieldDataType.String, filterable=True),
        # 'knowledge' | 'template' | 'brand' | 'policy' | 'demo'.
        SimpleField(name="tag", type=SearchFieldDataType.String, filterable=True),
        # FK back to agent_memory_items.id so we can delete by item or apply
        # per-conversation soft-disable filters at query time.
        SimpleField(name="agent_memory_item_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=EMBED_DIMS,
            vector_search_profile_name="hnsw-profile",
        ),
    ]


def _cache_fields() -> List:
    return [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SimpleField(name="query_hash", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="query_text", type=SearchFieldDataType.String),
        SimpleField(name="profile", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="workspace_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="response_json", type=SearchFieldDataType.String, retrievable=True),
        SimpleField(name="source_types", type=SearchFieldDataType.String, retrievable=True),
        SimpleField(
            name="created_at",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="expires_at",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
        ),
        SimpleField(name="hit_count", type=SearchFieldDataType.Int32, retrievable=True),
    ]


class IndexManager:
    def __init__(self) -> None:
        self._endpoint = settings.AZURE_SEARCH_ENDPOINT
        self._index_client = SearchIndexClient(
            endpoint=self._endpoint,
            credential=_admin_credential(),
        )

    def _search_client(self, index_name: str) -> SearchClient:
        """Admin-credential client for write operations (upsert, delete)."""
        return SearchClient(
            endpoint=self._endpoint,
            index_name=index_name,
            credential=_admin_credential(),
        )

    def _query_search_client(self, index_name: str) -> SearchClient:
        """Query-credential client for read-only operations (search)."""
        return SearchClient(
            endpoint=self._endpoint,
            index_name=index_name,
            credential=_query_credential(),
        )

    # ── Index creation ────────────────────────────────────────────────────────

    def ensure_all_indexes(self) -> None:
        self.ensure_documents_index()
        self.ensure_vector_index()
        self.ensure_cache_index()

    def ensure_documents_index(self) -> None:
        self._ensure_index(
            settings.AZURE_SEARCH_INDEX_NAME,
            _document_fields(),
            vector_search=_vector_search_config(),
        )

    def ensure_vector_index(self) -> None:
        self._ensure_index(
            settings.AZURE_SEARCH_VECTOR_INDEX_NAME,
            _document_fields(),
            vector_search=_vector_search_config(),
        )

    def ensure_cache_index(self) -> None:
        self._ensure_index(settings.AZURE_SEARCH_CACHE_INDEX_NAME, _cache_fields())

    def _ensure_index(
        self,
        name: str,
        fields: List,
        vector_search: Optional[VectorSearch] = None,
    ) -> None:
        index = SearchIndex(name=name, fields=fields, vector_search=vector_search)
        try:
            self._index_client.create_or_update_index(index)
            logger.info("Index '%s' ready", name)
        except HttpResponseError as e:
            # Any schema-incompatible update (immutable field change, unknown
            # semantic field, etc.) requires delete + recreate. In dev/demo
            # this is acceptable — connectors re-populate the index automatically.
            _err = str(e)
            _recoverable = (
                "CannotChangeExistingField" in _err
                or "OperationNotAllowed" in _err
                or "InvalidRequestParameter" in _err
                or "UnknownField" in _err
            )
            if _recoverable:
                logger.warning(
                    "Index '%s' schema conflict — deleting and recreating. "
                    "All indexed documents will be re-ingested by the sync worker.",
                    name,
                )
                try:
                    self._index_client.delete_index(name)
                    self._index_client.create_or_update_index(index)
                    logger.info("Index '%s' recreated with updated schema", name)
                except Exception as inner:
                    logger.error(
                        "Failed to recreate index '%s': %s", name, inner
                    )
                    raise
            else:
                logger.error(
                    "Failed to create/update index '%s': %s", name, e.message
                )
                raise

    # ── Document upsert / delete ──────────────────────────────────────────────

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=60))
    def upsert_documents(self, index_name: str, documents: List[Dict]) -> int:
        """Upload documents in batches of 100. Returns total upserted count."""
        if not documents:
            return 0
        client = self._search_client(index_name)
        total = 0
        batch_size = 100
        for i in range(0, len(documents), batch_size):
            batch = documents[i : i + batch_size]
            try:
                results = client.upload_documents(batch)
                succeeded = sum(1 for r in results if r.succeeded)
                total += succeeded
                if succeeded < len(batch):
                    logger.warning(
                        "Batch partial failure: %d/%d succeeded", succeeded, len(batch)
                    )
            except Exception as e:
                logger.error("Upsert batch failed: %s", str(e))
                raise
        return total

    def delete_documents(self, index_name: str, ids: List[str]) -> None:
        if not ids:
            return
        client = self._search_client(index_name)
        docs = [{"id": doc_id} for doc_id in ids]
        try:
            client.delete_documents(docs)
            logger.info("Deleted %d documents from '%s'", len(ids), index_name)
        except Exception as e:
            logger.error("Delete failed: %s", str(e))
            raise

    def delete_by_source(self, index_name: str, source_id: str) -> None:
        """Delete all chunks belonging to a source_id."""
        client = self._search_client(index_name)
        try:
            results = client.search("*", filter=f"source_id eq '{source_id}'", select=["id"], top=1000)
            ids = [r["id"] for r in results]
            if ids:
                self.delete_documents(index_name, ids)
        except Exception as e:
            logger.error("delete_by_source failed for '%s': %s", source_id, str(e))

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_index_stats(self, index_name: str) -> Dict:
        try:
            stats = self._index_client.get_index_statistics(index_name)
            return {
                "index_name": index_name,
                "document_count": stats.document_count,
                "storage_size_mb": round(stats.storage_size / (1024 * 1024), 2),
            }
        except Exception as e:
            return {"index_name": index_name, "error": str(e)}

    def search(
        self,
        index_name: str,
        query: str,
        vector: Optional[List[float]] = None,
        filter_expr: Optional[str] = None,
        top: int = 8,
        select: Optional[List[str]] = None,
    ) -> List[Dict]:
        client = self._query_search_client(index_name)
        vector_queries = None
        search_mode = "keyword-only"
        if vector:
            # Validate vector dimensions before sending to Azure Search
            if len(vector) != EMBED_DIMS:
                logger.warning(
                    "Vector dimension mismatch: got %d, expected %d — using keyword-only",
                    len(vector), EMBED_DIMS,
                )
            else:
                vector_queries = [
                    VectorizedQuery(
                        vector=vector,
                        k_nearest_neighbors=top,
                        fields="content_vector",
                    )
                ]
                search_mode = "hybrid"
        logger.debug("Search on '%s': mode=%s, top=%d", index_name, search_mode, top)
        try:
            results = client.search(
                search_text=query,
                vector_queries=vector_queries,
                filter=filter_expr,
                top=top,
                select=select,
            )
            return list(results)
        except Exception as e:
            logger.error("Search failed on '%s': %s", index_name, str(e))
            return []


# Singleton
try:
    index_manager = IndexManager()
except Exception as e:
    logger.warning("IndexManager init failed (no Search endpoint?): %s", str(e))
    index_manager = None  # type: ignore
