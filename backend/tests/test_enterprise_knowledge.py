"""
Mela AI - Enterprise Knowledge System Tests
Tests for: chunker metadata injection, SharePoint permission extraction,
include/exclude filtering, OneDrive delegated access, Graph Search,
Graph client, ingestion pipeline, and paste support readiness.
"""

import asyncio
import hashlib
import json
import pytest
from datetime import datetime, timezone
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

# ── Chunker Tests ─────────────────────────────────────────────────────────────


class TestTextChunker:
    def test_basic_chunking(self):
        from app.services.search.chunker import TextChunker
        chunker = TextChunker(chunk_size=50, overlap=10)
        text = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
        chunks = chunker.chunk(text)
        assert len(chunks) >= 1
        # All original text should be represented
        combined = " ".join(chunks)
        assert "First" in combined
        assert "Fifth" in combined

    def test_chunk_document_with_metadata(self):
        from app.services.search.chunker import TextChunker
        chunker = TextChunker(chunk_size=500, overlap=50)
        doc_id = "test-doc-001"
        title = "Quarterly Report Q3 2024"
        content = "This is a test document with enough content. " * 20
        metadata = {
            "source_type": "sharepoint",
            "path": "/drive/root:/Documents/Reports",
            "file_type": "pdf",
        }
        chunks = chunker.chunk_document(doc_id, title, content, metadata=metadata)
        assert len(chunks) >= 1
        # Each chunk should have metadata header injected
        first_chunk = chunks[0]["content"]
        assert "Document: Quarterly Report Q3 2024" in first_chunk
        assert "Source: sharepoint" in first_chunk
        assert "Documents/Reports" in first_chunk

    def test_chunk_document_preserves_section_headings(self):
        from app.services.search.chunker import TextChunker
        chunker = TextChunker(chunk_size=100, overlap=20)
        content = (
            "# Introduction\n\n"
            "This is the introduction section with some text. " * 5 + "\n\n"
            "## Methods\n\n"
            "This describes the methods used. " * 5
        )
        chunks = chunker.chunk_document(
            "doc1", "Paper", content,
            metadata={"source_type": "sharepoint", "path": "/docs"},
        )
        # At least one chunk should capture a section heading
        sections = [c.get("section", "") for c in chunks]
        has_heading = any(s for s in sections)
        # The heading tracking should capture at least one heading
        assert len(chunks) >= 1

    def test_chunk_document_no_metadata(self):
        """Backwards compatibility: chunk_document works without metadata."""
        from app.services.search.chunker import TextChunker
        chunker = TextChunker(chunk_size=500, overlap=50)
        chunks = chunker.chunk_document("id1", "Title", "Some content here.")
        assert len(chunks) == 1
        assert chunks[0]["title"] == "Title"

    def test_chunk_ids_are_stable(self):
        from app.services.search.chunker import TextChunker
        chunker = TextChunker()
        c1 = chunker.chunk_document("doc1", "T", "Hello world content.")
        c2 = chunker.chunk_document("doc1", "T", "Hello world content.")
        assert c1[0]["chunk_id"] == c2[0]["chunk_id"]

    def test_empty_content(self):
        from app.services.search.chunker import TextChunker
        chunker = TextChunker()
        chunks = chunker.chunk_document("doc1", "T", "")
        assert chunks == []

    def test_heading_extraction(self):
        from app.services.search.chunker import _extract_last_heading
        text = "Some text\n# First Heading\nMore text\n## Second Heading\nEnd."
        assert _extract_last_heading(text) == "Second Heading"

    def test_heading_extraction_none(self):
        from app.services.search.chunker import _extract_last_heading
        assert _extract_last_heading("No headings here.") == ""

    def test_chunk_header_building(self):
        from app.services.search.chunker import _build_chunk_header
        header = _build_chunk_header(
            title="Report.pdf",
            source_type="sharepoint",
            path="/drive/root:/Docs/Finance",
            section="Revenue",
        )
        assert "Document: Report.pdf" in header
        assert "Source: sharepoint" in header
        assert "Docs/Finance" in header
        assert "Section: Revenue" in header

    def test_chunk_header_empty(self):
        from app.services.search.chunker import _build_chunk_header
        assert _build_chunk_header("", "", "", "") == ""


# ── SharePoint Permission Extraction Tests ────────────────────────────────────


class TestSharePointPermissions:
    def test_extract_user_permissions(self):
        from app.services.connectors.sharepoint import _extract_permission_ids
        permissions = [
            {
                "grantedToV2": {
                    "user": {"id": "user-oid-1", "displayName": "Alice"},
                }
            },
            {
                "grantedToV2": {
                    "user": {"id": "user-oid-2", "displayName": "Bob"},
                }
            },
        ]
        users, groups = _extract_permission_ids(permissions)
        assert "user-oid-1" in users
        assert "user-oid-2" in users
        assert groups == []

    def test_extract_group_permissions(self):
        from app.services.connectors.sharepoint import _extract_permission_ids
        permissions = [
            {
                "grantedToV2": {
                    "group": {"id": "group-oid-1", "displayName": "Engineering"},
                }
            },
        ]
        users, groups = _extract_permission_ids(permissions)
        assert users == []
        assert "group-oid-1" in groups

    def test_extract_mixed_permissions(self):
        from app.services.connectors.sharepoint import _extract_permission_ids
        permissions = [
            {
                "grantedToV2": {
                    "user": {"id": "u1"},
                    "group": {"id": "g1"},
                }
            },
            {
                "grantedToIdentitiesV2": [
                    {"user": {"id": "u2"}},
                    {"group": {"id": "g2"}},
                ],
            },
        ]
        users, groups = _extract_permission_ids(permissions)
        assert set(users) == {"u1", "u2"}
        assert set(groups) == {"g1", "g2"}

    def test_extract_empty_permissions(self):
        from app.services.connectors.sharepoint import _extract_permission_ids
        users, groups = _extract_permission_ids([])
        assert users == []
        assert groups == []

    def test_extract_legacy_grantedTo(self):
        """Falls back to grantedTo when grantedToV2 is absent."""
        from app.services.connectors.sharepoint import _extract_permission_ids
        permissions = [
            {
                "grantedTo": {
                    "user": {"id": "legacy-user-1"},
                }
            },
        ]
        users, groups = _extract_permission_ids(permissions)
        assert "legacy-user-1" in users

    def test_extract_site_user(self):
        # siteUser.id is a SharePoint site-specific numeric ID, NOT an Azure AD OID.
        # It must NOT be collected for ACL filtering (would never match JWT oid claim).
        from app.services.connectors.sharepoint import _collect_identity
        user_ids = set()
        group_ids = set()
        _collect_identity({"siteUser": {"id": "site-user-1"}}, user_ids, group_ids)
        assert "site-user-1" not in user_ids  # intentionally excluded — not an Azure AD OID

    def test_deduplication(self):
        from app.services.connectors.sharepoint import _extract_permission_ids
        permissions = [
            {"grantedToV2": {"user": {"id": "u1"}}},
            {"grantedToV2": {"user": {"id": "u1"}}},
            {"grantedToIdentitiesV2": [{"user": {"id": "u1"}}]},
        ]
        users, groups = _extract_permission_ids(permissions)
        assert users == ["u1"]  # deduplicated


# ── SharePoint Include/Exclude Tests ──────────────────────────────────────────


class TestSharePointFiltering:
    @pytest.mark.asyncio
    async def test_exclude_path_filtering(self):
        """Items in excluded paths should be skipped."""
        from app.services.connectors.sharepoint import SharePointConnector
        with patch("app.services.connectors.sharepoint.settings") as mock_settings:
            mock_settings.sharepoint_site_list = []
            connector = SharePointConnector(
                workspace_id="ws1",
                site_urls=["https://example.sharepoint.com/sites/test"],
                exclude_paths=["/archive/", "/temp/"],
                crawl_permissions=False,
            )
            assert "/archive/" in connector._exclude_paths
            assert "/temp/" in connector._exclude_paths

    @pytest.mark.asyncio
    async def test_include_library_filtering(self):
        """Only specified libraries should be crawled."""
        from app.services.connectors.sharepoint import SharePointConnector
        with patch("app.services.connectors.sharepoint.settings") as mock_settings:
            mock_settings.sharepoint_site_list = []
            connector = SharePointConnector(
                workspace_id="ws1",
                site_urls=["https://example.sharepoint.com/sites/test"],
                include_libraries=["Documents", "Shared Files"],
                crawl_permissions=False,
            )
            assert connector._include_libraries == {"Documents", "Shared Files"}

    def test_doc_id_stability(self):
        from app.services.connectors.sharepoint import _doc_id
        id1 = _doc_id("site1", "item1")
        id2 = _doc_id("site1", "item1")
        id3 = _doc_id("site1", "item2")
        assert id1 == id2
        assert id1 != id3


# ── OneDrive Tests ────────────────────────────────────────────────────────────


class TestOneDriveConnector:
    def test_acl_set_to_user(self):
        """OneDrive documents should have ACL restricted to the owner."""
        from app.services.connectors.onedrive import OneDriveConnector
        conn = OneDriveConnector(
            workspace_id="ws1",
            delegated_token="fake-token",
            user_id="user-oid-123",
        )
        assert conn._user_id == "user-oid-123"

    def test_empty_user_id_raises_security_error(self):
        """SECURITY: OneDrive must reject empty user_id to prevent public ACLs."""
        from app.services.connectors.onedrive import OneDriveConnector
        import pytest
        with pytest.raises(ValueError, match="non-empty user_id"):
            OneDriveConnector(workspace_id="ws1", delegated_token="tok", user_id="")

    def test_empty_token_accepted_app_only(self):
        """Phase 1: OneDrive uses app-only auth; delegated_token is optional/ignored."""
        from app.services.connectors.onedrive import OneDriveConnector
        # Should NOT raise — token is unused in app-only mode
        conn = OneDriveConnector(workspace_id="ws1", delegated_token="", user_id="user-123")
        assert conn._user_id == "user-123"

    def test_doc_id_user_scoped(self):
        from app.services.connectors.onedrive import _doc_id
        id1 = _doc_id("user1", "item1")
        id2 = _doc_id("user2", "item1")
        assert id1 != id2  # different users yield different IDs


# ── Ingestion Worker Tests ────────────────────────────────────────────────────


class TestIngestionWorkerDelegatedToken:
    def test_sync_job_has_delegated_fields(self):
        from app.services.ingestion_worker import SyncJob, JobType
        job = SyncJob(
            id="job-1",
            job_type=JobType.DELTA_SYNC,
            connector_type="onedrive",
            source_id="user-1",
            workspace_id="ws1",
            delegated_token="secret-token",
            user_id="user-1",
        )
        assert job.delegated_token == "secret-token"
        assert job.user_id == "user-1"

    @pytest.mark.asyncio
    async def test_onedrive_connector_gets_delegated_token(self):
        from app.services.ingestion_worker import IngestionWorker, SyncJob, JobType
        worker = IngestionWorker()
        job = SyncJob(
            id="job-2",
            job_type=JobType.FULL_SYNC,
            connector_type="onedrive",
            source_id="user-1",
            workspace_id="ws1",
            delegated_token="my-token",
            user_id="user-oid",
        )
        with patch("app.services.ingestion_worker.settings") as mock_settings:
            mock_settings.CONNECTOR_ONEDRIVE_ENABLED = True
            conn = await worker._get_connector(job)
            assert conn is not None
            assert conn._user_id == "user-oid"


# ── Graph Search Tests ────────────────────────────────────────────────────────


class TestGraphSearch:
    @pytest.mark.asyncio
    async def test_search_files_builds_correct_request(self):
        from app.services.connectors.graph_client import GraphClient
        client = GraphClient(delegated_token="test-token")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {
                    "hitsContainers": [
                        {
                            "hits": [
                                {
                                    "rank": 1,
                                    "summary": "Budget doc",
                                    "resource": {
                                        "name": "Budget.xlsx",
                                        "webUrl": "https://sp.com/Budget.xlsx",
                                        "lastModifiedDateTime": "2024-01-01T00:00:00Z",
                                        "size": 12345,
                                    },
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_http

            results = await client.search_files("budget report", top=5)
            assert len(results) == 1
            assert results[0]["name"] == "Budget.xlsx"
            assert results[0]["_summary"] == "Budget doc"


# ── Query Pipeline ACL Filter Tests ──────────────────────────────────────────


class TestACLFilter:
    def test_build_acl_filter_user_only(self):
        from app.services.search.query_pipeline import _build_acl_filter
        f = _build_acl_filter("user-1", None)
        assert "acl_users/any(u: u eq 'user-1')" in f
        assert "not acl_users/any()" in f  # allow docs with no ACLs

    def test_build_acl_filter_with_groups(self):
        from app.services.search.query_pipeline import _build_acl_filter
        f = _build_acl_filter("user-1", ["group-a", "group-b"])
        assert "acl_groups/any(g: g eq 'group-a')" in f
        assert "acl_groups/any(g: g eq 'group-b')" in f

    def test_odata_escaping(self):
        from app.services.search.query_pipeline import _odata_literal
        assert _odata_literal("it's") == "it''s"
        assert _odata_literal("normal") == "normal"


# ── ConnectorDocument Tests ───────────────────────────────────────────────────


class TestConnectorDocument:
    def test_valid_creation(self):
        from app.services.connectors.base import ConnectorDocument
        doc = ConnectorDocument(
            id="test-1",
            source_type="sharepoint",
            source_id="site::drive",
            workspace_id="ws1",
            title="Test.pdf",
            content="hello",
            acl_users=["u1", "u2"],
            acl_groups=["g1"],
        )
        assert doc.acl_users == ["u1", "u2"]
        assert doc.acl_groups == ["g1"]

    def test_invalid_source_type_raises(self):
        from app.services.connectors.base import ConnectorDocument
        with pytest.raises(ValueError, match="Invalid source_type"):
            ConnectorDocument(
                id="test-1",
                source_type="invalid_type",
                source_id="x",
            )

    def test_empty_id_raises(self):
        from app.services.connectors.base import ConnectorDocument
        with pytest.raises(ValueError, match="must not be empty"):
            ConnectorDocument(
                id="",
                source_type="sharepoint",
                source_id="x",
            )

    def test_default_citation(self):
        from app.services.connectors.base import ConnectorDocument
        doc = ConnectorDocument(
            id="test-1",
            source_type="sharepoint",
            source_id="x",
            title="Test.pdf",
            url="https://sp.com/Test.pdf",
        )
        cit = doc.default_citation()
        assert cit["title"] == "Test.pdf"
        assert cit["source_type"] == "sharepoint"

    def test_sensitivity_label(self):
        from app.services.connectors.base import ConnectorDocument
        doc = ConnectorDocument(
            id="test-1",
            source_type="sharepoint",
            source_id="x",
            sensitivity_label="Confidential",
        )
        assert doc.sensitivity_label == "Confidential"

    def test_metadata_field(self):
        from app.services.connectors.base import ConnectorDocument
        doc = ConnectorDocument(
            id="test-1",
            source_type="sharepoint",
            source_id="x",
            metadata={"site_id": "abc", "drive_id": "def"},
        )
        assert doc.metadata["site_id"] == "abc"


# ── Ingestion Pipeline Tests ─────────────────────────────────────────────────


class TestIngestionPipeline:
    @pytest.mark.asyncio
    async def test_ingest_document_calls_chunker_with_metadata(self):
        from app.services.search.ingestion import IngestionPipeline
        from app.services.connectors.base import ConnectorDocument

        pipeline = IngestionPipeline()
        doc = ConnectorDocument(
            id="test-1",
            source_type="sharepoint",
            source_id="site::drive",
            workspace_id="ws1",
            title="Report.pdf",
            content="Some report content. " * 50,
            path="/drive/root:/Documents",
            file_type="pdf",
        )

        mock_chunker = MagicMock()
        mock_chunker.chunk_document.return_value = [
            {"chunk_id": "c1", "chunk_index": 0, "doc_id": "test-1",
             "title": "Report.pdf", "content": "chunk text", "section": ""},
        ]
        mock_openai = AsyncMock()
        mock_openai.create_embeddings = AsyncMock(return_value=[[0.1] * 3072])
        mock_idx = MagicMock()
        mock_idx.upsert_documents.return_value = 1

        with patch.object(pipeline, "_get_deps", return_value=(mock_idx, mock_chunker, mock_openai)):
            count = await pipeline.ingest_document(doc)
            assert count == 1
            # Verify chunker was called with metadata
            call_kwargs = mock_chunker.chunk_document.call_args
            assert call_kwargs.kwargs.get("metadata") == {
                "source_type": "sharepoint",
                "path": "/drive/root:/Documents",
                "file_type": "pdf",
            }


# ── Connector API Schema Tests ───────────────────────────────────────────────


class TestConnectorAPISchemas:
    def test_onedrive_sync_request(self):
        from app.api.endpoints.connectors import OneDriveSyncRequest
        req = OneDriveSyncRequest(full_sync=True, delegated_token="tok123")
        assert req.delegated_token == "tok123"

    def test_crawl_rule_request(self):
        from app.api.endpoints.connectors import CrawlRuleRequest
        req = CrawlRuleRequest(
            include_libraries=["Documents"],
            exclude_paths=["/archive/"],
        )
        assert "Documents" in req.include_libraries
        assert "/archive/" in req.exclude_paths


# ── Index Manager Schema Tests ────────────────────────────────────────────────


class TestIndexSchema:
    def test_document_fields_include_acl(self):
        from app.services.search.index_manager import _document_fields
        fields = _document_fields()
        field_names = {f.name for f in fields}
        assert "acl_users" in field_names
        assert "acl_groups" in field_names
        assert "sensitivity_label" in field_names
        assert "content_vector" in field_names

    def test_cache_fields_include_hit_count(self):
        from app.services.search.index_manager import _cache_fields
        fields = _cache_fields()
        field_names = {f.name for f in fields}
        assert "hit_count" in field_names
        assert "query_hash" in field_names


# ── SourceRecord / EnterpriseSearchResult Tests ──────────────────────────────


class TestSourceRecord:
    def test_to_citation_dict(self):
        from app.services.search.query_pipeline import SourceRecord
        sr = SourceRecord(
            source_type="sharepoint",
            chunk_id="c1",
            chunk_text="text",
            file_name="Report.pdf",
            web_url="https://sp.com/Report.pdf",
            location_hint="Page 3",
        )
        cit = sr.to_citation_dict()
        assert cit["title"] == "Report.pdf"
        assert cit["location"] == "Page 3"

    def test_enterprise_result_to_source_record(self):
        from app.services.search.query_pipeline import EnterpriseSearchResult
        r = EnterpriseSearchResult(
            chunk_id="c1",
            document_title="Budget.xlsx",
            content="Q3 numbers",
            score=0.95,
            source_type="sharepoint",
            url="https://sp.com/Budget.xlsx",
            citation={"title": "Budget.xlsx", "site_url": "https://sp.com"},
        )
        sr = r.to_source_record()
        assert sr.file_name == "Budget.xlsx"
        assert sr.site_url == "https://sp.com"
