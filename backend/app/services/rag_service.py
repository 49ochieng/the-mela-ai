"""
Mela AI - RAG Pipeline Service
"""

import logging
from typing import List, Dict, Any, Optional
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.models import VectorizedQuery
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    SearchableField,
    SimpleField,
)
from azure.core.credentials import AzureKeyCredential

from app.core.config import settings
from app.services.openai_service import openai_service
from app.schemas.documents import SearchResult

logger = logging.getLogger(__name__)


class RAGService:
    """Service for RAG operations."""

    VECTOR_DIMENSIONS = 3072  # text-embedding-3-large

    def __init__(self):
        self.credential = AzureKeyCredential(settings.effective_search_admin_key)
        self.index_client = SearchIndexClient(
            endpoint=settings.AZURE_SEARCH_ENDPOINT,
            credential=self.credential,
        )
        self.search_client = SearchClient(
            endpoint=settings.AZURE_SEARCH_ENDPOINT,
            index_name=settings.AZURE_SEARCH_INDEX_NAME,
            credential=self.credential,
        )

    async def ensure_index_exists(self) -> None:
        """Create search index if it doesn't exist."""
        try:
            self.index_client.get_index(settings.AZURE_SEARCH_INDEX_NAME)
            logger.info(f"Index {settings.AZURE_SEARCH_INDEX_NAME} already exists")
        except Exception:
            logger.info(f"Creating index {settings.AZURE_SEARCH_INDEX_NAME}")
            await self._create_index()

    async def _create_index(self) -> None:
        """Create the search index."""
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="document_id", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SearchableField(name="title", type=SearchFieldDataType.String),
            SimpleField(name="chunk_index", type=SearchFieldDataType.Int32),
            SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="source_url", type=SearchFieldDataType.String),
            SimpleField(name="file_type", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="uploaded_by", type=SearchFieldDataType.String, filterable=True),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self.VECTOR_DIMENSIONS,
                vector_search_profile_name="vector-profile",
            ),
        ]

        vector_search = VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(name="hnsw-config"),
            ],
            profiles=[
                VectorSearchProfile(
                    name="vector-profile",
                    algorithm_configuration_name="hnsw-config",
                ),
            ],
        )

        index = SearchIndex(
            name=settings.AZURE_SEARCH_INDEX_NAME,
            fields=fields,
            vector_search=vector_search,
        )

        self.index_client.create_or_update_index(index)
        logger.info(f"Index {settings.AZURE_SEARCH_INDEX_NAME} created")

    def chunk_text(
        self,
        text: str,
        chunk_size: int = None,
        chunk_overlap: int = None,
    ) -> List[str]:
        """Split text into chunks."""
        chunk_size = chunk_size or settings.RAG_CHUNK_SIZE
        chunk_overlap = chunk_overlap or settings.RAG_CHUNK_OVERLAP

        # Simple character-based chunking
        # In production, use semantic chunking
        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_size

            # Try to break at sentence boundary
            if end < len(text):
                # Look for sentence end
                for sep in [". ", ".\n", "! ", "!\n", "? ", "?\n"]:
                    last_sep = text[start:end].rfind(sep)
                    if last_sep != -1:
                        end = start + last_sep + len(sep)
                        break

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            start = end - chunk_overlap

        return chunks

    async def index_document(
        self,
        document_id: str,
        title: str,
        content: str,
        source: str,
        source_url: Optional[str] = None,
        file_type: str = "unknown",
        uploaded_by: str = "",
        metadata: Optional[Dict] = None,
    ) -> List[str]:
        """Index a document into Azure AI Search."""
        # Chunk the content
        chunks = self.chunk_text(content)

        # Generate embeddings
        embeddings = await openai_service.create_embeddings(chunks)

        # Prepare documents for indexing
        documents = []
        chunk_ids = []

        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = f"{document_id}_{i}"
            chunk_ids.append(chunk_id)

            doc = {
                "id": chunk_id,
                "document_id": document_id,
                "content": chunk,
                "title": title,
                "chunk_index": i,
                "source": source,
                "source_url": source_url or "",
                "file_type": file_type,
                "uploaded_by": uploaded_by,
                "content_vector": embedding,
            }
            documents.append(doc)

        # Upload to Azure Search
        try:
            result = self.search_client.upload_documents(documents)
            logger.info(f"Indexed {len(documents)} chunks for document {document_id}")
            return chunk_ids
        except Exception as e:
            logger.error(f"Indexing error: {e}")
            raise

    async def search(
        self,
        query: str,
        top_k: int = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """Search for relevant documents."""
        top_k = top_k or settings.RAG_TOP_K

        # Generate query embedding
        query_embedding = await openai_service.create_embedding(query)

        # Build filter expression
        filter_expr = None
        if filters:
            conditions = []
            if "document_ids" in filters:
                ids = ",".join(
                    "'" + str(doc_id).replace("'", "''") + "'"
                    for doc_id in filters["document_ids"]
                )
                conditions.append(f"document_id in ({ids})")
            if "source" in filters:
                src = str(filters["source"]).replace("'", "''")
                conditions.append(f"source eq '{src}'")
            if "file_type" in filters:
                ft = str(filters["file_type"]).replace("'", "''")
                conditions.append(f"file_type eq '{ft}'")
            if "uploaded_by" in filters:
                ub = str(filters["uploaded_by"]).replace("'", "''")
                conditions.append(f"uploaded_by eq '{ub}'")
            if conditions:
                filter_expr = " and ".join(conditions)

        # Perform hybrid search (vector + keyword)
        try:
            results = self.search_client.search(
                search_text=query,
                vector_queries=[
                    VectorizedQuery(
                        vector=query_embedding,
                        k_nearest_neighbors=top_k,
                        fields="content_vector",
                    )
                ],
                filter=filter_expr,
                top=top_k,
                select=["id", "document_id", "content", "title", "source_url", "chunk_index"],
            )

            search_results = []
            for result in results:
                search_results.append(SearchResult(
                    document_id=result["document_id"],
                    document_title=result["title"],
                    chunk_id=result["id"],
                    content=result["content"],
                    score=result["@search.score"],
                    source_url=result.get("source_url"),
                ))

            return search_results

        except Exception as e:
            logger.error(f"Search error: {e}")
            raise

    async def delete_document(self, document_id: str) -> None:
        """Delete all chunks for a document."""
        try:
            # Find all chunks
            results = self.search_client.search(
                search_text="*",
                filter=f"document_id eq '{document_id}'",
                select=["id"],
            )

            # Delete chunks
            docs_to_delete = [{"id": r["id"]} for r in results]
            if docs_to_delete:
                self.search_client.delete_documents(docs_to_delete)
                logger.info(f"Deleted {len(docs_to_delete)} chunks for document {document_id}")

        except Exception as e:
            logger.error(f"Delete error: {e}")
            raise

    def build_context_prompt(
        self,
        results: List[SearchResult],
        max_tokens: int = 4000,
    ) -> str:
        """Build context prompt from search results."""
        if not results:
            return ""

        context_parts = []
        current_tokens = 0

        for result in results:
            chunk_tokens = openai_service.count_tokens(result.content)
            if current_tokens + chunk_tokens > max_tokens:
                break

            context_parts.append(
                f"[Source: {result.document_title}]\n{result.content}"
            )
            current_tokens += chunk_tokens

        return "\n\n---\n\n".join(context_parts)


# Singleton instance - initialized lazily to avoid import failures
try:
    rag_service = RAGService()
except Exception as e:
    logger.warning(f"Failed to initialize RAGService: {e}")
    rag_service = None
