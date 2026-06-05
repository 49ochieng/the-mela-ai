"""
Mela AI - Ingestion Pipeline
Takes ConnectorDocuments, chunks them, generates embeddings,
and upserts to Azure AI Search with full ACL metadata.
"""

from __future__ import annotations

import json
import logging
from datetime import timezone
from typing import List, Optional

from app.core.config import settings
from app.services.connectors.base import ConnectorDocument

logger = logging.getLogger(__name__)

# text-embedding-ada-002 / text-embedding-3-* limit is 8192 tokens per input.
# We truncate any chunk that exceeds this to avoid 400 errors.
_EMBED_TOKEN_LIMIT = 8000  # leave a small safety margin


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class IngestionPipeline:
    def __init__(self) -> None:
        self._index_name = settings.AZURE_SEARCH_INDEX_NAME

    def _get_deps(self):
        from app.services.search.index_manager import index_manager
        from app.services.search.chunker import chunker
        from app.services.openai_service import openai_service
        return index_manager, chunker, openai_service

    async def ingest_document(
        self,
        doc: ConnectorDocument,
        index_name: Optional[str] = None,
    ) -> int:
        """Chunk → embed → upsert one ConnectorDocument. Returns chunk count."""
        idx = index_name or self._index_name
        index_manager, chunker, openai_service = self._get_deps()

        if index_manager is None:
            logger.warning("IndexManager unavailable; skipping ingestion for %s", doc.id)
            return 0

        chunks = chunker.chunk_document(
            doc.id, doc.title, doc.content,
            metadata={
                "source_type": doc.source_type,
                "path": doc.path,
                "file_type": doc.file_type,
            },
        )
        if not chunks:
            logger.debug("No chunks produced for doc %s", doc.id)
            return 0

        texts = [c["content"] for c in chunks]

        # Safety-truncate any chunk that exceeds the embedding model's token limit.
        # This can happen with JSON/binary files that don't split on sentence boundaries.
        try:
            import tiktoken
            _enc = tiktoken.get_encoding("cl100k_base")
            safe_texts = []
            for t in texts:
                toks = _enc.encode(t)
                if len(toks) > _EMBED_TOKEN_LIMIT:
                    logger.warning(
                        "Chunk for '%s' has %d tokens — truncating to %d",
                        doc.title, len(toks), _EMBED_TOKEN_LIMIT,
                    )
                    t = _enc.decode(toks[:_EMBED_TOKEN_LIMIT])
                safe_texts.append(t)
            texts = safe_texts
        except Exception:
            pass  # tiktoken unavailable; proceed without truncation

        try:
            vectors = await openai_service.create_embeddings(texts)
        except Exception as e:
            logger.error("Embedding failed for doc %s: %s", doc.id, str(e))
            vectors = [None] * len(texts)

        search_docs = []
        for chunk, vector in zip(chunks, vectors):
            search_doc = {
                "id": chunk["chunk_id"],
                "workspace_id": doc.workspace_id,
                "context_type": doc.context_type,
                "source_type": doc.source_type,
                "source_id": doc.source_id,
                "title": doc.title,
                "content": chunk["content"],
                "url": doc.url,
                "path": doc.path,
                "file_type": doc.file_type,
                "last_modified": _iso(doc.last_modified),
                "created_at": _iso(doc.created_at),
                "chunk_id": chunk["chunk_id"],
                "chunk_index": chunk["chunk_index"],
                "citation_json": json.dumps(doc.citation or doc.default_citation()),
                "acl_users": doc.acl_users,
                "acl_groups": doc.acl_groups,
                "sensitivity_label": doc.sensitivity_label,
                "memory_scope": doc.memory_scope or None,
                "tag": doc.tag or None,
                "agent_memory_item_id": doc.agent_memory_item_id or None,
                "content_vector": vector if vector else [],
            }
            if not vector:
                logger.warning(
                    "Empty vector for chunk %s of doc '%s' (%s) — "
                    "chunk will be keyword-only until reindexed via POST /admin/index/reindex",
                    chunk["chunk_id"], doc.title, doc.source_type,
                )
            search_docs.append(search_doc)

        count = index_manager.upsert_documents(idx, search_docs)
        logger.info("Ingested %d chunks for '%s' into '%s'", count, doc.title, idx)
        return count

    async def ingest_batch(
        self,
        docs: List[ConnectorDocument],
        index_name: Optional[str] = None,
    ) -> int:
        total = 0
        for doc in docs:
            try:
                total += await self.ingest_document(doc, index_name)
            except Exception as e:
                logger.error("Batch ingestion error for doc %s: %s", doc.id, str(e))
        return total

    async def delete_source(self, source_id: str, index_name: Optional[str] = None) -> None:
        idx = index_name or self._index_name
        index_manager, _, _ = self._get_deps()
        if index_manager:
            index_manager.delete_by_source(idx, source_id)


# Singleton
ingestion_pipeline = IngestionPipeline()
