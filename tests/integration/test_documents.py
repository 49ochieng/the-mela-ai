"""
Mela AI - Integration Tests for Document Endpoints
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.schemas.documents import SearchResponse, SearchResult


# ---------------------------------------------------------------------------
# GET /api/v1/documents/
# ---------------------------------------------------------------------------

class TestListDocuments:
    """Tests for GET /api/v1/documents/."""

    @pytest.mark.asyncio
    async def test_list_documents_empty(self, client, mock_db):
        """When no documents exist an empty list should be returned."""
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db.execute.return_value = result_mock

        response = await client.get("/api/v1/documents/")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_documents_returns_items(self, client, mock_db):
        """Active documents should be listed with correct fields."""
        fake_doc = MagicMock()
        fake_doc.id = "doc-1"
        fake_doc.title = "Annual Report"
        fake_doc.filename = "annual_report.pdf"
        fake_doc.file_type = "pdf"
        fake_doc.file_size = 204800
        fake_doc.source = "upload"
        fake_doc.source_url = None
        fake_doc.chunk_count = 10
        fake_doc.is_indexed = True
        fake_doc.is_active = True
        fake_doc.metadata = None
        fake_doc.uploaded_by = "user-001"
        fake_doc.created_at = datetime(2025, 3, 1)
        fake_doc.updated_at = datetime(2025, 3, 2)

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [fake_doc]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db.execute.return_value = result_mock

        response = await client.get("/api/v1/documents/")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "doc-1"
        assert data[0]["title"] == "Annual Report"
        assert data[0]["file_type"] == "pdf"
        assert data[0]["is_indexed"] is True

    @pytest.mark.asyncio
    async def test_list_documents_unauthenticated(self, unauthenticated_client):
        response = await unauthenticated_client.get("/api/v1/documents/")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/documents/search
# ---------------------------------------------------------------------------

class TestSearchDocuments:
    """Tests for POST /api/v1/documents/search."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self, client):
        """A valid search should return matching results."""
        fake_results = [
            SearchResult(
                document_id="doc-1",
                document_title="Budget Report",
                chunk_id="doc-1_0",
                content="The annual budget for 2025 is allocated as follows...",
                score=0.92,
                source_url=None,
                metadata=None,
            ),
        ]

        with patch(
            "app.api.endpoints.documents.rag_service.search",
            new_callable=AsyncMock,
            return_value=fake_results,
        ):
            response = await client.post(
                "/api/v1/documents/search",
                json={"query": "budget 2025", "top_k": 5},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "budget 2025"
        assert data["total_results"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["document_title"] == "Budget Report"

    @pytest.mark.asyncio
    async def test_search_empty_results(self, client):
        """A search with no matches should return an empty result set."""
        with patch(
            "app.api.endpoints.documents.rag_service.search",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = await client.post(
                "/api/v1/documents/search",
                json={"query": "nonexistent content"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_results"] == 0
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_search_with_filters(self, client):
        """Filters should be passed through to the search service."""
        with patch(
            "app.api.endpoints.documents.rag_service.search",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_search:
            response = await client.post(
                "/api/v1/documents/search",
                json={
                    "query": "finance",
                    "top_k": 3,
                    "filters": {"source": "sharepoint"},
                },
            )

        assert response.status_code == 200
        # Verify the service was called with the correct arguments
        mock_search.assert_called_once_with(
            query="finance",
            top_k=3,
            filters={"source": "sharepoint"},
        )

    @pytest.mark.asyncio
    async def test_search_respects_top_k(self, client):
        """The top_k parameter should be forwarded to the search service."""
        with patch(
            "app.api.endpoints.documents.rag_service.search",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_search:
            response = await client.post(
                "/api/v1/documents/search",
                json={"query": "report", "top_k": 10},
            )

        assert response.status_code == 200
        call_kwargs = mock_search.call_args
        assert call_kwargs.kwargs["top_k"] == 10 or call_kwargs[1]["top_k"] == 10

    @pytest.mark.asyncio
    async def test_search_missing_query_returns_422(self, client):
        """Omitting the required 'query' field should produce a 422 error."""
        response = await client.post(
            "/api/v1/documents/search",
            json={"top_k": 5},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_search_invalid_top_k_returns_422(self, client):
        """A top_k value outside the allowed range should produce 422."""
        response = await client.post(
            "/api/v1/documents/search",
            json={"query": "test", "top_k": 0},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_search_unauthenticated(self, unauthenticated_client):
        response = await unauthenticated_client.post(
            "/api/v1/documents/search",
            json={"query": "test"},
        )
        assert response.status_code == 401
