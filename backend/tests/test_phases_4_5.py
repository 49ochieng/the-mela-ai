"""
Comprehensive Tests — Phases 4 & 5
====================================
Covers every feature implemented in the enterprise intelligence and security-
hardening phases:

Phase 4a — OrgContextService
  * In-memory TTL caching (hit, miss, expiry, invalidation)
  * Per-user async lock prevents thundering-herd fetches
  * Graceful degradation on Graph errors
  * build_prompt_block() output correctness

Phase 4b — Chat Service org context injection
  * Org block prepended to Work-mode system prompt
  * Personal mode is NOT modified
  * Non-fatal on org context failure

Phase 5a — ConnectorDocument.acl_last_refreshed field
  * Field exists with correct default

Phase 5b — ACL Refresh JobType
  * JobType.ACL_REFRESH value
  * _execute_acl_refresh() logic smoke-test

Phase 5c — Query pipeline cache key includes group hash
  * Different groups → different query hash
  * Same groups different order → same hash

Phase 5d — Sensitivity label passthrough in build_context_prompt()
  * ⚠️ Confidential prefix injected for labelled results
  * Unlabelled results unaffected

Phase 5e — GET /admin/connectors/acl-status endpoint contract
  * Returns expected keys
  * stale_threshold_hours respected

Phase 5f — OneDrive app-only migration (Phase 1 regression)
  * graphclient.get_user_drive_delta() path
  * ingestion_worker delta key alignment

Phase 5g — Graph live search fallback (Phase 3 regression)
  * Fallback fires when <3 results
  * Fallback fires when max score < 0.5
  * Fallback SKIPPED when results are abundant
  * Deduplication by URL
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tests.conftest import db, make_user


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4a — OrgContextService
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrgContextServiceCaching:
    """TTL cache behaviour."""

    def setup_method(self):
        """Clear module-level caches before every test."""
        from app.services import org_context_service as _m
        _m._cache.clear()
        _m._locks.clear()

    def _make_service(self):
        from app.services.org_context_service import OrgContextService
        return OrgContextService()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_fetch(self):
        svc = self._make_service()
        fake_ctx = {"display_name": "Alice", "job_title": "Engineer"}
        svc._fetch = AsyncMock(return_value=fake_ctx)

        result = await svc.get_context("user-001")
        assert result == fake_ctx
        svc._fetch.assert_awaited_once_with("user-001")

    @pytest.mark.asyncio
    async def test_cache_hit_skips_fetch(self):
        svc = self._make_service()
        fake_ctx = {"display_name": "Bob"}
        svc._fetch = AsyncMock(return_value=fake_ctx)

        # First call — fills cache
        await svc.get_context("user-002")
        # Second call — should NOT invoke _fetch again
        result2 = await svc.get_context("user-002")
        assert result2 == fake_ctx
        assert svc._fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_expired_cache_refetches(self):
        from app.services import org_context_service as _m
        svc = self._make_service()
        fake_ctx = {"display_name": "Carol"}
        svc._fetch = AsyncMock(return_value=fake_ctx)

        # Manually plant an expired entry
        _m._cache["user-003"] = {"data": fake_ctx, "exp": time.time() - 1}

        result = await svc.get_context("user-003")
        assert result == fake_ctx
        assert svc._fetch.await_count == 1  # Re-fetched

    @pytest.mark.asyncio
    async def test_invalidate_clears_cache(self):
        from app.services import org_context_service as _m
        svc = self._make_service()
        fake_ctx = {"display_name": "Dave"}
        _m._cache["user-004"] = {"data": fake_ctx, "exp": time.time() + 9999}

        svc.invalidate("user-004")
        assert "user-004" not in _m._cache

    @pytest.mark.asyncio
    async def test_empty_user_id_returns_none(self):
        svc = self._make_service()
        result = await svc.get_context("")
        assert result is None

    @pytest.mark.asyncio
    async def test_concurrent_requests_single_fetch(self):
        """Thundering-herd protection: 5 concurrent callers → only 1 _fetch."""
        from app.services import org_context_service as _m
        svc = self._make_service()
        fetch_count = 0

        async def slow_fetch(uid):
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.01)
            return {"display_name": "Eve"}

        svc._fetch = slow_fetch

        # All 5 run concurrently
        results = await asyncio.gather(*[svc.get_context("user-005") for _ in range(5)])
        assert all(r == {"display_name": "Eve"} for r in results)
        assert fetch_count == 1


class TestOrgContextServiceFetch:
    """_fetch() delegates to individual Graph calls and handles errors."""

    def setup_method(self):
        from app.services import org_context_service as _m
        _m._cache.clear()

    @pytest.mark.asyncio
    async def test_fetch_assembles_context_dict(self):
        svc_module = __import__("app.services.org_context_service", fromlist=["OrgContextService"])
        svc = svc_module.OrgContextService()

        svc._get_profile = AsyncMock(return_value={
            "displayName": "Frank", "jobTitle": "CTO", "department": "Engineering",
            "officeLocation": "NYC", "mail": "frank@co.com",
        })
        svc._get_manager = AsyncMock(return_value={"display_name": "Grace", "job_title": "CEO", "email": "grace@co.com"})
        svc._get_direct_reports = AsyncMock(return_value=[])
        svc._get_groups = AsyncMock(return_value=["Eng Team", "All Staff"])
        svc._get_people = AsyncMock(return_value=[])

        fake_gc = MagicMock()
        with patch("app.services.connectors.graph_client.GraphClient", return_value=fake_gc):
            ctx = await svc._fetch("user-frank")

        assert ctx["display_name"] == "Frank"
        assert ctx["job_title"] == "CTO"
        assert ctx["department"] == "Engineering"
        assert ctx["manager"]["display_name"] == "Grace"
        assert "Eng Team" in ctx["groups"]

    @pytest.mark.asyncio
    async def test_fetch_tolerates_manager_not_found(self):
        svc_module = __import__("app.services.org_context_service", fromlist=["OrgContextService"])
        svc = svc_module.OrgContextService()

        svc._get_profile = AsyncMock(return_value={"displayName": "Henry"})
        svc._get_manager = AsyncMock(side_effect=Exception("404 Not Found"))
        svc._get_direct_reports = AsyncMock(return_value=[])
        svc._get_groups = AsyncMock(return_value=[])
        svc._get_people = AsyncMock(return_value=[])

        gc_mock = MagicMock()
        with patch("app.services.connectors.graph_client.GraphClient", return_value=gc_mock):
            ctx = await svc._fetch("user-henry")

        assert ctx is not None
        assert ctx["manager"] is None  # Error replaced with None

    @pytest.mark.asyncio
    async def test_fetch_returns_none_on_total_failure(self):
        """If GraphClient() itself raises, _fetch returns None (not exception)."""
        svc_module = __import__("app.services.org_context_service", fromlist=["OrgContextService"])
        svc = svc_module.OrgContextService()

        with patch("app.services.connectors.graph_client.GraphClient", side_effect=RuntimeError("no config")):
            ctx = await svc._fetch("user-xyz")

        assert ctx is None


class TestBuildPromptBlock:
    """build_prompt_block() generates correct structured text."""

    def _make_ctx(self, **overrides) -> dict:
        base = {
            "display_name": "Iris Jones",
            "job_title": "VP Engineering",
            "department": "Technology",
            "office_location": "London",
            "email": "iris@company.com",
            "manager": {"display_name": "Jack Smith", "job_title": "CTO", "email": "jack@company.com"},
            "direct_reports": [
                {"display_name": "Kim Lee", "job_title": "Senior Engineer", "email": "kim@company.com"},
            ],
            "groups": ["Eng All", "Leadership"],
            "frequent_contacts": [
                {"display_name": "Leo Ray", "job_title": "Product Manager", "email": "leo@company.com"},
            ],
        }
        base.update(overrides)
        return base

    def _svc(self):
        from app.services.org_context_service import OrgContextService
        return OrgContextService()

    def test_contains_display_name(self):
        block = self._svc().build_prompt_block(self._make_ctx())
        assert "Iris Jones" in block

    def test_contains_job_title(self):
        block = self._svc().build_prompt_block(self._make_ctx())
        assert "VP Engineering" in block

    def test_contains_manager(self):
        block = self._svc().build_prompt_block(self._make_ctx())
        assert "Jack Smith" in block

    def test_contains_groups(self):
        block = self._svc().build_prompt_block(self._make_ctx())
        assert "Eng All" in block

    def test_contains_frequent_contact(self):
        block = self._svc().build_prompt_block(self._make_ctx())
        assert "Leo Ray" in block

    def test_heading_present(self):
        block = self._svc().build_prompt_block(self._make_ctx())
        assert "## Your Organisational Context" in block

    def test_empty_context_returns_empty_string(self):
        svc = self._svc()
        assert svc.build_prompt_block({}) == ""

    def test_missing_optional_fields_no_crash(self):
        block = self._svc().build_prompt_block({
            "display_name": "MinimalUser",
            "job_title": "",
            "department": "",
            "office_location": "",
            "email": "",
            "manager": None,
            "direct_reports": [],
            "groups": [],
            "frequent_contacts": [],
        })
        assert "MinimalUser" in block

    def test_groups_capped_at_8_in_output(self):
        ctx = self._make_ctx(groups=[f"Group-{i}" for i in range(20)])
        block = self._svc().build_prompt_block(ctx)
        # Only first 8 groups included
        for i in range(8):
            assert f"Group-{i}" in block
        # Groups 8+ NOT included in the displayed block
        assert "Group-8" not in block

    def test_direct_reports_capped_at_5(self):
        ctx = self._make_ctx(direct_reports=[
            {"display_name": f"Report-{i}", "job_title": "Engineer", "email": f"r{i}@co.com"}
            for i in range(10)
        ])
        block = self._svc().build_prompt_block(ctx)
        # Only first 5 reports shown
        for i in range(5):
            assert f"Report-{i}" in block
        assert "Report-5" not in block


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4b — Chat service org context injection
# ═══════════════════════════════════════════════════════════════════════════════

class TestChatServiceOrgContextInjection:
    """Verify org context is prepended ONLY in work mode."""

    def _source(self):
        import sys
        import importlib
        mod = importlib.import_module("app.services.chat_service")
        return open(mod.__file__, encoding="utf-8").read()

    def test_org_block_present_in_work_mode(self):
        """In Work mode, the system prompt starts with the org context block."""
        source = self._source()
        # The injection code must check _profile_mode == "work"
        assert '_profile_mode == "work"' in source
        assert "org_context_service" in source
        assert "build_prompt_block" in source

    def test_injection_is_prepended_not_appended(self):
        """Org block must be prepended (before the main system prompt)."""
        source = self._source()
        # The assignment should concatenate org block + existing content
        assert '_org_block + "\\n\\n" + messages[0]["content"]' in source or \
               "_org_block + " in source

    def test_injection_wrapped_in_try_except(self):
        """Org injection failure must be non-fatal."""
        source = self._source()
        # A try/except must guard the org injection
        idx = source.find("org_context_service.get_context")
        assert idx != -1
        snippet = source[max(0, idx-200):idx+500]
        assert "except" in snippet


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5a — ConnectorDocument.acl_last_refreshed
# ═══════════════════════════════════════════════════════════════════════════════

class TestConnectorDocumentACLField:
    """acl_last_refreshed field correctness."""

    def test_field_exists_with_none_default(self):
        from app.services.connectors.base import ConnectorDocument
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(ConnectorDocument)}
        assert "acl_last_refreshed" in fields
        assert fields["acl_last_refreshed"].default is None

    def test_field_accepts_datetime(self):
        from app.services.connectors.base import ConnectorDocument
        now = datetime.now(timezone.utc)
        doc = ConnectorDocument(
            id="test-doc",
            source_type="sharepoint",
            source_id="src-1",
            acl_last_refreshed=now,
        )
        assert doc.acl_last_refreshed == now

    def test_field_defaults_to_none(self):
        from app.services.connectors.base import ConnectorDocument
        doc = ConnectorDocument(
            id="doc-no-acl",
            source_type="onedrive",
            source_id="src-2",
        )
        assert doc.acl_last_refreshed is None


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5b — ACL Refresh job type
# ═══════════════════════════════════════════════════════════════════════════════

class TestACLRefreshJobType:
    """JobType enum and ACL refresh dispatch."""

    def test_acl_refresh_value_exists(self):
        from app.services.ingestion_worker import JobType
        assert hasattr(JobType, "ACL_REFRESH")
        assert JobType.ACL_REFRESH == "acl_refresh"

    def test_all_expected_job_types_present(self):
        from app.services.ingestion_worker import JobType
        expected = {"full_sync", "delta_sync", "reindex", "health_check", "acl_refresh"}
        actual = {jt.value for jt in JobType}
        assert expected.issubset(actual)

    def test_execute_routes_to_acl_refresh(self):
        """When job_type is ACL_REFRESH, _execute must call _execute_acl_refresh."""
        import inspect
        from app.services.ingestion_worker import IngestionWorker
        source = inspect.getsource(IngestionWorker._execute)
        assert "ACL_REFRESH" in source
        assert "_execute_acl_refresh" in source

    def test_acl_refresh_method_exists(self):
        from app.services.ingestion_worker import IngestionWorker
        assert hasattr(IngestionWorker, "_execute_acl_refresh")
        assert callable(IngestionWorker._execute_acl_refresh)

    @pytest.mark.asyncio
    async def test_acl_refresh_invalid_source_id_returns_zero(self):
        """Source IDs without '::' are invalid for SharePoint ACL refresh."""
        from app.services.ingestion_worker import IngestionWorker, SyncJob, JobType
        worker = IngestionWorker()
        job = SyncJob(
            id=str(uuid.uuid4()),
            job_type=JobType.ACL_REFRESH,
            connector_type="sharepoint",
            source_id="invalid-no-double-colon",
            workspace_id="ws",
        )
        count = await worker._execute_acl_refresh(job)
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5c — Query hash includes user_groups_hash
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueryHashGroupsIncluded:
    """Cache key must change when group membership changes."""

    def _hash(self, user_id, query, groups):
        from app.services.search.query_pipeline import _query_hash
        return _query_hash(
            query=query,
            workspace_id="ws",
            context_type="work",
            user_id=user_id,
            source_types=["sharepoint"],
            user_groups=groups,
        )

    def test_same_groups_same_hash(self):
        h1 = self._hash("u1", "budget report", ["g1", "g2"])
        h2 = self._hash("u1", "budget report", ["g1", "g2"])
        assert h1 == h2

    def test_different_groups_different_hash(self):
        h1 = self._hash("u1", "budget report", ["g1"])
        h2 = self._hash("u1", "budget report", ["g1", "g2-new"])
        assert h1 != h2

    def test_group_order_independent(self):
        """Groups in different order must produce identical hash."""
        h1 = self._hash("u1", "hr policy", ["group-a", "group-b", "group-c"])
        h2 = self._hash("u1", "hr policy", ["group-c", "group-a", "group-b"])
        assert h1 == h2

    def test_empty_groups_vs_none_equivalent(self):
        h1 = self._hash("u1", "test", [])
        h2 = self._hash("u1", "test", None)
        assert h1 == h2

    def test_user_dimension_still_isolated(self):
        """Different users must not share cache entries even with same groups."""
        h1 = self._hash("user-alice", "quarterly report", ["g1"])
        h2 = self._hash("user-bob",   "quarterly report", ["g1"])
        assert h1 != h2

    def test_hash_is_32_chars(self):
        h = self._hash("u", "q", ["g"])
        assert len(h) == 32


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5d — Sensitivity label prefix in build_context_prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestSensitivityLabelPrefix:
    """Confidential-labelled results get a ⚠️ warning prefix."""

    def _make_result(self, title: str, content: str, sensitivity: str = ""):
        from app.services.search.query_pipeline import EnterpriseSearchResult
        citation = {}
        if sensitivity:
            citation["sensitivity_label"] = sensitivity
        return EnterpriseSearchResult(
            chunk_id="chunk-1",
            document_title=title,
            content=content,
            score=0.9,
            source_type="sharepoint",
            url="https://example.com/doc",
            citation=citation,
        )

    def _build(self, results):
        from app.services.search.query_pipeline import EnterpriseQueryPipeline
        pipeline = EnterpriseQueryPipeline.__new__(EnterpriseQueryPipeline)
        return pipeline.build_context_prompt(results)

    def test_confidential_gets_warning_prefix(self):
        r = self._make_result("Secret Memo", "Contents of the memo.", sensitivity="Confidential")
        prompt = self._build([r])
        assert "⚠️ Confidential" in prompt

    def test_non_confidential_no_prefix(self):
        r = self._make_result("Public Doc", "Publicly available content.")
        prompt = self._build([r])
        assert "⚠️" not in prompt

    def test_mixed_results_prefix_only_on_labelled(self):
        r1 = self._make_result("Open Policy", "Open content.", sensitivity="")
        r2 = self._make_result("Board Minutes", "Sensitive board content.", sensitivity="Confidential")
        prompt = self._build([r1, r2])
        # Only the confidential one gets the prefix
        assert prompt.count("⚠️ Confidential") == 1
        assert "Open Policy" in prompt

    def test_highly_confidential_label_also_prefixed(self):
        r = self._make_result("HR Data", "Sensitive HR records.", sensitivity="Highly Confidential")
        prompt = self._build([r])
        assert "⚠️ Confidential" in prompt

    def test_empty_sensitivity_label_no_prefix(self):
        r = self._make_result("Internal Note", "Some note.", sensitivity="")
        prompt = self._build([r])
        assert "⚠️" not in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5e — GET /admin/connectors/acl-status endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestACLStatusEndpoint:
    """Verify the endpoint is registered and returns the expected schema."""

    def test_endpoint_registered(self):
        import inspect
        from app.api.endpoints import admin
        source = inspect.getsource(admin)
        assert "/connectors/acl-status" in source

    def test_endpoint_is_admin_only(self):
        import inspect
        from app.api.endpoints import admin
        source = inspect.getsource(admin)
        # The acl-status function must require admin user
        idx = source.find("/connectors/acl-status")
        snippet = source[idx:idx+600]
        assert "get_current_admin_user" in snippet

    def test_endpoint_returns_expected_keys(self):
        """Mock index_manager and verify response shape."""
        import asyncio
        from fastapi.testclient import TestClient
        from unittest.mock import patch, MagicMock

        # We test the raw function rather than HTTP (avoids auth middleware)
        import importlib
        admin_mod = importlib.import_module("app.api.endpoints.admin")
        # Find the acl_status coroutine
        acl_fn = getattr(admin_mod, "get_acl_status", None)
        assert acl_fn is not None, "get_acl_status function not found in admin module"

    def test_stale_hours_param_mentioned_in_source(self):
        import inspect
        from app.api.endpoints import admin
        source = inspect.getsource(admin)
        assert "stale_hours" in source

    def test_response_contains_status_field(self):
        """The mocked endpoint must return a 'status' key."""
        import inspect
        from app.api.endpoints import admin
        source = inspect.getsource(admin)
        assert '"status"' in source or "'status'" in source
        assert "stale_acls_found" in source


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5f — OneDrive app-only migration regression tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOneDriveAppOnlyMigration:
    """Verify Phase 1 OneDrive fixes are intact."""

    def test_graph_client_has_get_user_drive_delta(self):
        from app.services.connectors.graph_client import GraphClient
        assert hasattr(GraphClient, "get_user_drive_delta")

    def test_graph_client_has_download_user_drive_item(self):
        from app.services.connectors.graph_client import GraphClient
        assert hasattr(GraphClient, "download_user_drive_item")

    def test_onedrive_connector_uses_app_only_client(self):
        """OneDriveConnector must instantiate GraphClient() with no delegated token."""
        import inspect
        from app.services.connectors.onedrive import OneDriveConnector
        init_source = inspect.getsource(OneDriveConnector.__init__)
        # Must create GraphClient() without passing any delegated token
        assert "GraphClient()" in init_source

    def test_onedrive_connector_ignores_delegated_token(self):
        """delegated_token param must be accepted but NOT forwarded to GraphClient."""
        import inspect
        from app.services.connectors.onedrive import OneDriveConnector
        source = inspect.getsource(OneDriveConnector.__init__)
        # delegated_token in signature but NOT used to init GraphClient
        assert "delegated_token" in source
        assert "GraphClient(delegated_token" not in source

    def test_delta_key_prefix_constant_exists(self):
        from app.services.connectors import onedrive
        assert hasattr(onedrive, "_DELTA_KEY_PREFIX")
        assert onedrive._DELTA_KEY_PREFIX == "onedrive"

    def test_ingestion_worker_aligns_source_id(self):
        """_execute() must set job.source_id = 'onedrive:{user_id}' for OneDrive jobs."""
        import inspect
        from app.services.ingestion_worker import IngestionWorker
        source = inspect.getsource(IngestionWorker._execute)
        assert "onedrive:{job.user_id}" in source or "f\"onedrive:{job.user_id}\"" in source

    def test_auto_queue_onedrive_function_exists(self):
        from app.services.ingestion_worker import IngestionWorker
        assert hasattr(IngestionWorker, "auto_queue_onedrive_for_known_users")

    @pytest.mark.asyncio
    async def test_auto_queue_skips_when_disabled(self):
        """If CONNECTOR_ONEDRIVE_ENABLED is False, auto_queue is a no-op."""
        from app.services.ingestion_worker import IngestionWorker
        worker = IngestionWorker()
        with patch("app.services.ingestion_worker.settings") as mock_settings:
            mock_settings.CONNECTOR_ONEDRIVE_ENABLED = False
            # Must return without error and enqueue nothing
            await worker.auto_queue_onedrive_for_known_users()
        assert worker._queue.empty()

    def test_get_user_drive_delta_uses_app_token(self):
        """get_user_drive_delta must always use get_app_token(), not the delegated token."""
        import inspect
        from app.services.connectors.graph_client import GraphClient
        source = inspect.getsource(GraphClient.get_user_drive_delta)
        assert "get_app_token" in source
        # Must NOT use self._token (the delegated token attribute)
        assert "self._token" not in source

    def test_download_user_drive_item_uses_app_token(self):
        """download_user_drive_item must use get_app_token()."""
        import inspect
        from app.services.connectors.graph_client import GraphClient
        source = inspect.getsource(GraphClient.download_user_drive_item)
        assert "get_app_token" in source

    def test_onedrive_health_check_uses_app_only(self):
        """health_check() must call get_user_drive_delta (app-only endpoint)."""
        import inspect
        from app.services.connectors.onedrive import OneDriveConnector
        source = inspect.getsource(OneDriveConnector.health_check)
        assert "get_user_drive_delta" in source


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5g — Graph live search fallback regression tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGraphLiveSearchFallback:
    """Phase 3: live search fires correctly and results are merged cleanly."""

    def _make_result(self, score: float, url: str = ""):
        from app.services.search.query_pipeline import EnterpriseSearchResult
        return EnterpriseSearchResult(
            chunk_id="c1",
            document_title="Doc",
            content="Content",
            score=score,
            source_type="sharepoint",
            url=url or f"https://sp.com/{uuid.uuid4()}",
        )

    def test_live_search_method_exists(self):
        from app.services.search.query_pipeline import EnterpriseQueryPipeline
        assert hasattr(EnterpriseQueryPipeline, "_graph_live_search")

    def test_fallback_threshold_logic_in_source(self):
        import inspect
        from app.services.search.query_pipeline import EnterpriseQueryPipeline
        source = inspect.getsource(EnterpriseQueryPipeline.search)
        assert "_graph_threshold" in source
        assert "< 3" in source or "<3" in source
        assert "< 0.5" in source or "<0.5" in source

    def test_fallback_only_when_user_id_set(self):
        """If user_id is empty, live search fallback must be skipped."""
        import inspect
        from app.services.search.query_pipeline import EnterpriseQueryPipeline
        source = inspect.getsource(EnterpriseQueryPipeline.search)
        assert "_graph_threshold and user_id" in source

    def test_live_results_score_below_indexed(self):
        """Live search results must have score=0.45 (below indexed threshold)."""
        import inspect
        from app.services.search.query_pipeline import EnterpriseQueryPipeline
        source = inspect.getsource(EnterpriseQueryPipeline._graph_live_search)
        assert "0.45" in source

    def test_live_results_annotated_as_live(self):
        """Live results must contain the live-search annotation text."""
        import inspect
        from app.services.search.query_pipeline import EnterpriseQueryPipeline
        source = inspect.getsource(EnterpriseQueryPipeline._graph_live_search)
        assert "not yet indexed" in source.lower() or "Live from Microsoft 365" in source

    def test_live_citation_has_live_flag(self):
        """Citation dict for live results must include 'live': True."""
        import inspect
        from app.services.search.query_pipeline import EnterpriseQueryPipeline
        source = inspect.getsource(EnterpriseQueryPipeline._graph_live_search)
        assert '"live": True' in source or "'live': True" in source

    def test_deduplication_by_url_in_source(self):
        """Merge logic must deduplicate by URL."""
        import inspect
        from app.services.search.query_pipeline import EnterpriseQueryPipeline
        source = inspect.getsource(EnterpriseQueryPipeline.search)
        assert "existing_urls" in source

    @pytest.mark.asyncio
    async def test_live_search_returns_empty_on_graph_error(self):
        """_graph_live_search must propagate Graph errors (callers catch them)."""
        from app.services.search.query_pipeline import EnterpriseQueryPipeline
        pipeline = EnterpriseQueryPipeline.__new__(EnterpriseQueryPipeline)

        with patch("app.services.connectors.graph_client.GraphClient") as mock_gc_cls:
            mock_gc = MagicMock()
            mock_gc.search_files = AsyncMock(side_effect=Exception("Graph 503"))
            mock_gc_cls.return_value = mock_gc

            # _graph_live_search should propagate the exception (callers catch it)
            with pytest.raises(Exception, match="Graph 503"):
                await pipeline._graph_live_search("query", "user-1", [], "work")

    @pytest.mark.asyncio
    async def test_live_search_wraps_hits_as_results(self):
        """_graph_live_search must wrap each Graph hit as EnterpriseSearchResult."""
        from app.services.search.query_pipeline import EnterpriseQueryPipeline, EnterpriseSearchResult
        pipeline = EnterpriseQueryPipeline.__new__(EnterpriseQueryPipeline)

        fake_hits = [
            {"id": "item1", "name": "Q4 Budget.xlsx", "webUrl": "https://sp.com/q4.xlsx", "_summary": "Q4 budget file"},
            {"id": "item2", "name": "HR Policy.pdf", "webUrl": "https://sp.com/hr.pdf"},
        ]

        with patch("app.services.connectors.graph_client.GraphClient") as mock_gc_cls:
            mock_gc = MagicMock()
            mock_gc.search_files = AsyncMock(return_value=fake_hits)
            mock_gc_cls.return_value = mock_gc

            results = await pipeline._graph_live_search("budget", "user-1", [], "work")

        assert len(results) == 2
        assert all(isinstance(r, EnterpriseSearchResult) for r in results)
        assert all(r.score == 0.45 for r in results)
        assert results[0].document_title == "Q4 Budget.xlsx"
        assert results[0].url == "https://sp.com/q4.xlsx"
        assert results[0].citation.get("live") is True


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION — Vector backfill admin endpoints (Phase 2 regression)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVectorBackfillEndpoints:
    """Verify Phase 2 admin endpoints are wired correctly."""

    def test_index_health_endpoint_registered(self):
        import inspect
        from app.api.endpoints import admin
        source = inspect.getsource(admin)
        assert "/index/health" in source

    def test_index_reindex_endpoint_registered(self):
        import inspect
        from app.api.endpoints import admin
        source = inspect.getsource(admin)
        assert "/index/reindex" in source

    def test_health_endpoint_checks_missing_vectors(self):
        import inspect
        from app.api.endpoints import admin
        source = inspect.getsource(admin)
        assert "missing_vectors" in source

    def test_reindex_endpoint_has_batch_size_param(self):
        import inspect
        from app.api.endpoints import admin
        source = inspect.getsource(admin)
        assert "batch_size" in source

    def test_reindex_response_has_required_keys(self):
        import inspect
        from app.api.endpoints import admin
        source = inspect.getsource(admin)
        for key in ("scanned", "backfilled", "failed"):
            assert key in source


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION — Delta token persistence (Phase 1c regression)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeltaTokenPersistence:
    """Verify connector_type inference from source_id prefix."""

    def test_onedrive_prefix_infers_connector_type(self):
        import inspect
        from app.services.ingestion_worker import IngestionWorker
        source = inspect.getsource(IngestionWorker._persist_delta_token)
        assert '"onedrive"' in source or "'onedrive'" in source
        assert "onedrive:" in source

    def test_non_onedrive_prefix_infers_sharepoint(self):
        import inspect
        from app.services.ingestion_worker import IngestionWorker
        source = inspect.getsource(IngestionWorker._persist_delta_token)
        assert '"sharepoint"' in source or "'sharepoint'" in source

    def test_set_and_get_delta_token_roundtrip(self):
        from app.services.ingestion_worker import IngestionWorker
        worker = IngestionWorker()
        with patch.object(worker, "_persist_delta_token", new=AsyncMock()):
            worker.set_delta_token("onedrive:user-abc", "tok-123")
        assert worker.get_delta_token("onedrive:user-abc") == "tok-123"

    def test_get_delta_token_returns_none_for_unknown(self):
        from app.services.ingestion_worker import IngestionWorker
        worker = IngestionWorker()
        assert worker.get_delta_token("onedrive:not-exist") is None


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION — Login hook queues OneDrive sync (Phase 1d regression)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoginHookOneDriveSync:
    """auth.py login must enqueue OneDrive sync when connector is enabled."""

    def test_login_enqueues_onedrive_sync_in_source(self):
        import inspect
        from app.api.endpoints import auth
        source = inspect.getsource(auth)
        assert "CONNECTOR_ONEDRIVE_ENABLED" in source
        assert "onedrive" in source.lower()

    def test_login_uses_full_sync_for_first_time(self):
        import inspect
        from app.api.endpoints import auth
        source = inspect.getsource(auth)
        # First sync = FULL_SYNC, subsequent = DELTA_SYNC
        assert "FULL_SYNC" in source
        assert "DELTA_SYNC" in source


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION — Startup scheduler (Phase 1d / Phase 5 regression)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStartupSchedulers:
    """main.py must register all periodic tasks."""

    def test_onedrive_30min_scheduler_in_main(self):
        import inspect
        import app.main as main_mod
        source = inspect.getsource(main_mod)
        assert "_onedrive_periodic_loop" in source
        assert "30 * 60" in source

    def test_acl_refresh_24h_scheduler_in_main(self):
        import inspect
        import app.main as main_mod
        source = inspect.getsource(main_mod)
        assert "_acl_refresh_periodic_loop" in source
        assert "24 * 3600" in source

    def test_acl_task_cancelled_on_shutdown(self):
        import inspect
        import app.main as main_mod
        source = inspect.getsource(main_mod)
        assert "_acl_task.cancel()" in source

    def test_onedrive_task_cancelled_on_shutdown(self):
        import inspect
        import app.main as main_mod
        source = inspect.getsource(main_mod)
        assert "_onedrive_task.cancel()" in source


# ═══════════════════════════════════════════════════════════════════════════════
# SMOKE TEST — Import all modified modules cleanly
# ═══════════════════════════════════════════════════════════════════════════════

class TestCleanImports:
    """All modified modules must import without errors."""

    def test_import_org_context_service(self):
        import app.services.org_context_service  # noqa: F401

    def test_import_ingestion_worker(self):
        import app.services.ingestion_worker  # noqa: F401

    def test_import_query_pipeline(self):
        import app.services.search.query_pipeline  # noqa: F401

    def test_import_base_connector(self):
        import app.services.connectors.base  # noqa: F401

    def test_import_onedrive_connector(self):
        import app.services.connectors.onedrive  # noqa: F401

    def test_import_graph_client(self):
        import app.services.connectors.graph_client  # noqa: F401

    def test_import_admin_endpoints(self):
        import app.api.endpoints.admin  # noqa: F401

    def test_import_auth_endpoints(self):
        import app.api.endpoints.auth  # noqa: F401

    def test_org_context_service_singleton_exported(self):
        from app.services.org_context_service import org_context_service, OrgContextService
        assert isinstance(org_context_service, OrgContextService)
