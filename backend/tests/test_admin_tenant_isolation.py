"""Admin tenant-isolation and auth-session hardening regression tests."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.endpoints.admin import (
    admin_revoke_user_sessions,
    get_analytics,
    get_acl_status,
    get_audit_logs,
    get_bootstrap_list,
    get_bootstrap_audit_logs,
    get_error_detail,
    index_vector_health,
    get_monitoring_data,
    get_offboarding_run,
    get_onboarding_run,
    get_org_settings,
    get_user,
    probe_model_health,
    reindex_missing_vectors,
    search_diagnostic,
    get_token_usage,
    get_usage_stats,
    list_model_governance,
    list_settings,
    list_tools,
    list_users,
    list_access_requests,
    list_error_logs,
    list_offboarding_runs,
    list_onboarding_logs,
    list_onboarding_runs,
    update_model_quota,
    update_org_settings,
    update_setting,
    update_user,
)
from app.schemas.admin import SystemSettingUpdate
from app.core.security import _enforce_server_side_session
from app.core.sessions import get_or_create_session, revoke_session_by_jti
from app.models.models import (
    AuditLog,
    Conversation,
    Document,
    ErrorLog,
    HRWorkflowRun,
    Message,
    ModelUsage,
    OnboardingLog,
    User,
)
from app.schemas.auth import UserInfo, UserUpdate
from app.schemas.settings import OrgSettings


def _admin_user(tenant_id: str | None) -> UserInfo:
    return UserInfo(
        id="admin-oid",
        email="admin@example.com",
        name="Admin",
        roles=["Admin"],
        tenant_id=tenant_id,
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


async def _seed_tenant_activity(
    db,
    *,
    tenant_id: str,
    suffix: str,
    tokens: int,
    message_tokens: int,
) -> User:
    now = datetime.utcnow()
    user = User(
        id=str(uuid.uuid4()),
        azure_id=f"oid-{suffix}",
        email=f"{suffix}@example.com",
        name=f"User {suffix}",
        is_active=True,
    )
    db.add(user)
    await db.flush()

    conversation = Conversation(
        id=str(uuid.uuid4()),
        user_id=user.id,
        title=f"Conversation {suffix}",
        model="gpt-5.2-chat",
        is_private=False,
        profile_mode="work",
        context_type="org",
        tenant_id=tenant_id,
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    await db.flush()

    db.add(
        Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="user",
            content="hello",
            tokens_used=message_tokens,
            created_at=now,
            tenant_id=tenant_id,
            profile_mode="work",
        )
    )
    db.add(
        Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="assistant",
            content="world",
            tokens_used=message_tokens,
            created_at=now,
            tenant_id=tenant_id,
            profile_mode="work",
        )
    )

    db.add(
        ModelUsage(
            id=str(uuid.uuid4()),
            user_id=user.id,
            conversation_id=conversation.id,
            model=f"model-{suffix}",
            prompt_tokens=tokens // 2,
            completion_tokens=tokens // 2,
            total_tokens=tokens,
            created_at=now,
        )
    )

    db.add(
        Document(
            id=str(uuid.uuid4()),
            title=f"Doc {suffix}",
            filename=f"doc-{suffix}.txt",
            file_type="txt",
            file_size=128,
            blob_url=f"https://example.com/{suffix}",
            source="upload",
            content_hash=f"hash-{suffix}",
            chunk_count=1,
            is_indexed=True,
            is_active=True,
            uploaded_by=user.id,
            created_at=now,
            updated_at=now,
        )
    )

    await db.commit()
    return user


@pytest.mark.asyncio
async def test_usage_stats_are_tenant_scoped(db):
    await _seed_tenant_activity(
        db,
        tenant_id="tenant-A",
        suffix="a",
        tokens=100,
        message_tokens=10,
    )
    await _seed_tenant_activity(
        db,
        tenant_id="tenant-B",
        suffix="b",
        tokens=300,
        message_tokens=30,
    )

    scoped = await get_usage_stats(current_user=_admin_user("tenant-A"), db=db)
    assert scoped.total_users == 1
    assert scoped.total_conversations == 1
    assert scoped.total_messages == 2
    assert scoped.total_tokens_used == 100
    assert scoped.total_documents == 1
    assert scoped.indexed_documents == 1

    global_stats = await get_usage_stats(current_user=_admin_user(None), db=db)
    assert global_stats.total_users == 2
    assert global_stats.total_conversations == 2
    assert global_stats.total_messages == 4
    assert global_stats.total_tokens_used == 400
    assert global_stats.total_documents == 2


@pytest.mark.asyncio
async def test_analytics_are_tenant_scoped(db):
    user_a = await _seed_tenant_activity(
        db,
        tenant_id="tenant-A",
        suffix="a",
        tokens=120,
        message_tokens=12,
    )
    await _seed_tenant_activity(
        db,
        tenant_id="tenant-B",
        suffix="b",
        tokens=220,
        message_tokens=22,
    )

    analytics = await get_analytics(
        days=2,
        current_user=_admin_user("tenant-A"),
        db=db,
    )

    assert analytics.overview.total_tokens_used == 120
    assert len(analytics.model_usage) == 1
    assert analytics.model_usage[0].model == "model-a"
    assert analytics.model_usage[0].total_tokens == 120
    assert len(analytics.top_users) == 1
    assert analytics.top_users[0].user_id == user_a.id
    assert analytics.top_users[0].user_email == user_a.email


@pytest.mark.asyncio
async def test_token_usage_is_tenant_scoped(db):
    user_a = await _seed_tenant_activity(
        db,
        tenant_id="tenant-A",
        suffix="a",
        tokens=100,
        message_tokens=10,
    )
    await _seed_tenant_activity(
        db,
        tenant_id="tenant-B",
        suffix="b",
        tokens=300,
        message_tokens=30,
    )

    scoped = await get_token_usage(
        days=7,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert scoped["days"] == 7
    assert len(scoped["users"]) == 1
    assert scoped["users"][0]["user_id"] == user_a.id
    assert scoped["users"][0]["period_tokens"] == 100
    assert scoped["users"][0]["period_requests"] == 1

    global_usage = await get_token_usage(
        days=7,
        current_user=_admin_user(None),
        db=db,
    )
    assert len(global_usage["users"]) == 2


@pytest.mark.asyncio
async def test_monitoring_snapshot_is_tenant_scoped(db):
    user_a = await _seed_tenant_activity(
        db,
        tenant_id="tenant-A",
        suffix="a",
        tokens=110,
        message_tokens=11,
    )
    await _seed_tenant_activity(
        db,
        tenant_id="tenant-B",
        suffix="b",
        tokens=210,
        message_tokens=21,
    )

    now = datetime.utcnow()
    db.add_all(
        [
            AuditLog(
                user_id=user_a.id,
                action="ok-A",
                resource_type="system",
                success=True,
                created_at=now,
            ),
            AuditLog(
                user_id=user_a.id,
                action="fail-A",
                resource_type="system",
                success=False,
                created_at=now,
            ),
            AuditLog(
                user_id="non-a-user",
                action="non-tenant-noise",
                resource_type="system",
                success=False,
                created_at=now,
            ),
        ]
    )
    await db.commit()

    scoped = await get_monitoring_data(
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert scoped["activity"]["active_sessions_1h"] == 1
    assert scoped["activity"]["messages_24h"] == 2
    assert scoped["activity"]["tokens_24h"] == 110
    assert scoped["quality"]["error_rate_pct"] == 50.0
    assert len(scoped["recent_errors"]) == 1
    assert scoped["recent_errors"][0]["user_id"] == user_a.id
    assert len(scoped["model_health"]) == 1
    assert scoped["model_health"][0]["model"] == "model-a"

    global_snapshot = await get_monitoring_data(
        current_user=_admin_user(None),
        db=db,
    )
    assert global_snapshot["activity"]["messages_24h"] == 4
    assert global_snapshot["activity"]["tokens_24h"] == 320


@pytest.mark.asyncio
async def test_audit_and_bootstrap_logs_are_tenant_scoped(db):
    user_a = await _seed_tenant_activity(
        db,
        tenant_id="tenant-A",
        suffix="a",
        tokens=120,
        message_tokens=12,
    )
    user_b = await _seed_tenant_activity(
        db,
        tenant_id="tenant-B",
        suffix="b",
        tokens=220,
        message_tokens=22,
    )

    now = datetime.utcnow()
    db.add_all(
        [
            AuditLog(
                user_id=user_a.id,
                action="tenant-a-action",
                resource_type="system",
                success=True,
                created_at=now,
            ),
            AuditLog(
                user_id=user_b.id,
                action="tenant-b-action",
                resource_type="system",
                success=True,
                created_at=now,
            ),
            AuditLog(
                user_id=user_a.id,
                action="bootstrap_admin_elevation",
                resource_type="user",
                resource_id=user_a.id,
                success=True,
                created_at=now,
            ),
            AuditLog(
                user_id=user_b.id,
                action="bootstrap_admin_elevation",
                resource_type="user",
                resource_id=user_b.id,
                success=True,
                created_at=now,
            ),
        ]
    )
    await db.commit()

    scoped_audit = await get_audit_logs(
        limit=100,
        offset=0,
        user_id=None,
        action=None,
        start_date=None,
        end_date=None,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert len(scoped_audit) == 2
    assert all(log.user_id == user_a.id for log in scoped_audit)

    scoped_bootstrap = await get_bootstrap_audit_logs(
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert len(scoped_bootstrap) == 1
    assert scoped_bootstrap[0].user_id == user_a.id

    global_bootstrap = await get_bootstrap_audit_logs(
        current_user=_admin_user(None),
        db=db,
    )
    assert len(global_bootstrap) == 2


@pytest.mark.asyncio
async def test_error_logs_and_error_detail_are_tenant_scoped(db):
    user_a = await _seed_tenant_activity(
        db,
        tenant_id="tenant-A",
        suffix="a",
        tokens=100,
        message_tokens=10,
    )
    await _seed_tenant_activity(
        db,
        tenant_id="tenant-B",
        suffix="b",
        tokens=100,
        message_tokens=10,
    )

    error_a = ErrorLog(
        user_id=user_a.id,
        user_email=user_a.email,
        tenant_id="tenant-A",
        method="GET",
        route="/api/v1/admin/stats",
        status_code=500,
        error_type="RuntimeError",
        message="tenant A error",
        severity="error",
    )
    error_b = ErrorLog(
        user_id="user-b",
        user_email="b@example.com",
        tenant_id="tenant-B",
        method="GET",
        route="/api/v1/admin/stats",
        status_code=500,
        error_type="RuntimeError",
        message="tenant B error",
        severity="error",
    )
    db.add_all([error_a, error_b])
    await db.commit()

    scoped = await list_error_logs(
        limit=50,
        offset=0,
        severity=None,
        user_id=None,
        tenant_id=None,
        route=None,
        start_date=None,
        end_date=None,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert scoped["total"] == 1
    assert len(scoped["errors"]) == 1
    assert scoped["errors"][0]["id"] == error_a.id

    with pytest.raises(HTTPException) as cross_tenant_err:
        await list_error_logs(
            limit=50,
            offset=0,
            severity=None,
            user_id=None,
            tenant_id="tenant-B",
            route=None,
            start_date=None,
            end_date=None,
            current_user=_admin_user("tenant-A"),
            db=db,
        )
    assert cross_tenant_err.value.status_code == 403

    detail = await get_error_detail(
        error_id=error_a.id,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert detail["id"] == error_a.id

    with pytest.raises(HTTPException) as not_found_err:
        await get_error_detail(
            error_id=error_b.id,
            current_user=_admin_user("tenant-A"),
            db=db,
        )
    assert not_found_err.value.status_code == 404


@pytest.mark.asyncio
async def test_access_requests_are_tenant_scoped(db):
    user_a = await _seed_tenant_activity(
        db,
        tenant_id="tenant-A",
        suffix="a",
        tokens=100,
        message_tokens=10,
    )
    user_b = await _seed_tenant_activity(
        db,
        tenant_id="tenant-B",
        suffix="b",
        tokens=100,
        message_tokens=10,
    )

    now = datetime.utcnow()
    db.add_all(
        [
            AuditLog(
                user_id=user_a.id,
                action="admin_access_requested",
                resource_type="user",
                resource_id=user_a.id,
                success=True,
                created_at=now,
            ),
            AuditLog(
                user_id=user_b.id,
                action="admin_access_requested",
                resource_type="user",
                resource_id=user_b.id,
                success=True,
                created_at=now,
            ),
        ]
    )
    await db.commit()

    scoped = await list_access_requests(
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert len(scoped) == 1
    assert scoped[0]["user_id"] == user_a.id

    global_requests = await list_access_requests(
        current_user=_admin_user(None),
        db=db,
    )
    assert {row["user_id"] for row in global_requests} == {
        user_a.id,
        user_b.id,
    }


@pytest.mark.asyncio
async def test_global_control_plane_endpoints_require_global_admin(db):
    tenant_admin = _admin_user("tenant-A")

    with pytest.raises(HTTPException) as tools_err:
        await list_tools(current_user=tenant_admin, db=db)
    assert tools_err.value.status_code == 403
    assert tools_err.value.detail == "Global admin access required"

    with pytest.raises(HTTPException) as settings_err:
        await list_settings(current_user=tenant_admin, db=db)
    assert settings_err.value.status_code == 403

    with pytest.raises(HTTPException) as bootstrap_err:
        await get_bootstrap_list(current_user=tenant_admin, db=db)
    assert bootstrap_err.value.status_code == 403

    with pytest.raises(HTTPException) as org_settings_err:
        await get_org_settings(current_user=tenant_admin, db=db)
    assert org_settings_err.value.status_code == 403

    with pytest.raises(HTTPException) as governance_err:
        await list_model_governance(current_user=tenant_admin, db=db)
    assert governance_err.value.status_code == 403

    with pytest.raises(HTTPException) as update_setting_err:
        await update_setting(
            key="tenant-should-not-set-this",
            data=SystemSettingUpdate(value="1"),
            current_user=tenant_admin,
            db=db,
        )
    assert update_setting_err.value.status_code == 403

    with pytest.raises(HTTPException) as update_org_settings_err:
        await update_org_settings(
            data=OrgSettings(private_chat_enabled=False),
            current_user=tenant_admin,
            db=db,
        )
    assert update_org_settings_err.value.status_code == 403

    with pytest.raises(HTTPException) as quota_update_err:
        await update_model_quota(
            model_id="gpt-4.1",
            data={"is_enabled": False},
            current_user=tenant_admin,
            db=db,
        )
    assert quota_update_err.value.status_code == 403

    with pytest.raises(HTTPException) as model_health_err:
        await probe_model_health(current_user=tenant_admin)
    assert model_health_err.value.status_code == 403

    with pytest.raises(HTTPException) as index_health_err:
        await index_vector_health(current_user=tenant_admin)
    assert index_health_err.value.status_code == 403

    with pytest.raises(HTTPException) as reindex_err:
        await reindex_missing_vectors(batch_size=8, current_user=tenant_admin)
    assert reindex_err.value.status_code == 403

    with pytest.raises(HTTPException) as acl_status_err:
        await get_acl_status(current_user=tenant_admin, stale_hours=24)
    assert acl_status_err.value.status_code == 403

    with pytest.raises(HTTPException) as search_diag_err:
        await search_diagnostic(current_user=tenant_admin)
    assert search_diag_err.value.status_code == 403


@pytest.mark.asyncio
async def test_global_control_plane_endpoints_allow_unscoped_admin(db):
    global_admin = _admin_user(None)

    tools = await list_tools(current_user=global_admin, db=db)
    assert tools == []

    settings = await list_settings(current_user=global_admin, db=db)
    assert settings == []

    bootstrap = await get_bootstrap_list(current_user=global_admin, db=db)
    assert "bootstrap_admins" in bootstrap

    initial_org = await get_org_settings(current_user=global_admin, db=db)
    assert initial_org.private_chat_enabled is True

    await update_setting(
        key="global_test_key",
        data=SystemSettingUpdate(value="enabled", description="test setting"),
        current_user=global_admin,
        db=db,
    )
    settings_after_update = await list_settings(current_user=global_admin, db=db)
    assert any(s.key == "global_test_key" for s in settings_after_update)

    await update_org_settings(
        data=OrgSettings(private_chat_enabled=False, private_chat_retention_days=14),
        current_user=global_admin,
        db=db,
    )
    updated_org = await get_org_settings(current_user=global_admin, db=db)
    assert updated_org.private_chat_enabled is False
    assert updated_org.private_chat_retention_days == 14

    governance = await list_model_governance(current_user=global_admin, db=db)
    assert "models" in governance
    assert len(governance["models"]) > 0


@pytest.mark.asyncio
async def test_user_management_endpoints_are_tenant_scoped(db, monkeypatch):
    user_a = await _seed_tenant_activity(
        db,
        tenant_id="tenant-A",
        suffix="a",
        tokens=100,
        message_tokens=10,
    )
    user_b = await _seed_tenant_activity(
        db,
        tenant_id="tenant-B",
        suffix="b",
        tokens=100,
        message_tokens=10,
    )

    scoped_users = await list_users(
        limit=50,
        offset=0,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert len(scoped_users) == 1
    assert scoped_users[0].id == user_a.id

    own_user = await get_user(
        user_id=user_a.id,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert own_user.id == user_a.id

    with pytest.raises(HTTPException) as get_cross_tenant:
        await get_user(
            user_id=user_b.id,
            current_user=_admin_user("tenant-A"),
            db=db,
        )
    assert get_cross_tenant.value.status_code == 404

    updated = await update_user(
        user_id=user_a.id,
        data=UserUpdate(daily_token_limit=54321),
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert updated.daily_token_limit == 54321

    with pytest.raises(HTTPException) as update_cross_tenant:
        await update_user(
            user_id=user_b.id,
            data=UserUpdate(daily_token_limit=12345),
            current_user=_admin_user("tenant-A"),
            db=db,
        )
    assert update_cross_tenant.value.status_code == 404

    async def _fake_revoke_all_user_sessions(_db, _user_id):
        return 2

    monkeypatch.setattr(
        "app.core.sessions.revoke_all_user_sessions",
        _fake_revoke_all_user_sessions,
    )

    own_revoke = await admin_revoke_user_sessions(
        user_id=user_a.id,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert own_revoke["revoked"] == 2

    with pytest.raises(HTTPException) as revoke_cross_tenant:
        await admin_revoke_user_sessions(
            user_id=user_b.id,
            current_user=_admin_user("tenant-A"),
            db=db,
        )
    assert revoke_cross_tenant.value.status_code == 404

    global_users = await list_users(
        limit=50,
        offset=0,
        current_user=_admin_user(None),
        db=db,
    )
    assert {u.id for u in global_users} == {user_a.id, user_b.id}


@pytest.mark.asyncio
async def test_onboarding_offboarding_runs_are_tenant_scoped(db):
    user_a = await _seed_tenant_activity(
        db,
        tenant_id="tenant-A",
        suffix="a",
        tokens=100,
        message_tokens=10,
    )
    user_b = await _seed_tenant_activity(
        db,
        tenant_id="tenant-B",
        suffix="b",
        tokens=100,
        message_tokens=10,
    )

    now = datetime.utcnow()
    onboarding_log_a = OnboardingLog(
        new_user_email="new-a@example.com",
        new_user_name="New A",
        initiated_by=user_a.id,
        initiated_by_email=user_a.email,
        steps_requested="[]",
        steps_completed="[]",
        steps_failed="[]",
        status="completed",
        created_at=now,
    )
    onboarding_log_b = OnboardingLog(
        new_user_email="new-b@example.com",
        new_user_name="New B",
        initiated_by=user_b.id,
        initiated_by_email=user_b.email,
        steps_requested="[]",
        steps_completed="[]",
        steps_failed="[]",
        status="completed",
        created_at=now,
    )

    onboarding_run_a = HRWorkflowRun(
        workflow_type="onboarding",
        actor_user_id=user_a.id,
        actor_email=user_a.email,
        target_email="new-a@example.com",
        target_upn="new-a@example.com",
        target_display_name="New A",
        payload_json="{}",
        step_results_json="[]",
        status="completed",
        started_at=now,
        completed_at=now,
    )
    onboarding_run_b = HRWorkflowRun(
        workflow_type="onboarding",
        actor_user_id=user_b.id,
        actor_email=user_b.email,
        target_email="new-b@example.com",
        target_upn="new-b@example.com",
        target_display_name="New B",
        payload_json="{}",
        step_results_json="[]",
        status="completed",
        started_at=now,
        completed_at=now,
    )
    offboarding_run_a = HRWorkflowRun(
        workflow_type="offboarding",
        actor_user_id=user_a.id,
        actor_email=user_a.email,
        target_email="old-a@example.com",
        target_upn="old-a@example.com",
        target_display_name="Old A",
        payload_json="{}",
        step_results_json="[]",
        status="completed",
        started_at=now,
        completed_at=now,
    )
    offboarding_run_b = HRWorkflowRun(
        workflow_type="offboarding",
        actor_user_id=user_b.id,
        actor_email=user_b.email,
        target_email="old-b@example.com",
        target_upn="old-b@example.com",
        target_display_name="Old B",
        payload_json="{}",
        step_results_json="[]",
        status="completed",
        started_at=now,
        completed_at=now,
    )

    db.add_all(
        [
            onboarding_log_a,
            onboarding_log_b,
            onboarding_run_a,
            onboarding_run_b,
            offboarding_run_a,
            offboarding_run_b,
        ]
    )
    await db.commit()

    scoped_logs = await list_onboarding_logs(
        limit=50,
        offset=0,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert scoped_logs["total"] == 1
    assert len(scoped_logs["logs"]) == 1
    assert scoped_logs["logs"][0]["id"] == onboarding_log_a.id

    scoped_onboarding_runs = await list_onboarding_runs(
        limit=50,
        offset=0,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert scoped_onboarding_runs["total"] == 1
    assert len(scoped_onboarding_runs["runs"]) == 1
    assert scoped_onboarding_runs["runs"][0]["id"] == onboarding_run_a.id

    scoped_offboarding_runs = await list_offboarding_runs(
        limit=50,
        offset=0,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert scoped_offboarding_runs["total"] == 1
    assert len(scoped_offboarding_runs["runs"]) == 1
    assert scoped_offboarding_runs["runs"][0]["id"] == offboarding_run_a.id

    own_onboarding_detail = await get_onboarding_run(
        run_id=onboarding_run_a.id,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert own_onboarding_detail["id"] == onboarding_run_a.id

    own_offboarding_detail = await get_offboarding_run(
        run_id=offboarding_run_a.id,
        current_user=_admin_user("tenant-A"),
        db=db,
    )
    assert own_offboarding_detail["id"] == offboarding_run_a.id

    with pytest.raises(HTTPException) as onboarding_cross_tenant:
        await get_onboarding_run(
            run_id=onboarding_run_b.id,
            current_user=_admin_user("tenant-A"),
            db=db,
        )
    assert onboarding_cross_tenant.value.status_code == 404

    with pytest.raises(HTTPException) as offboarding_cross_tenant:
        await get_offboarding_run(
            run_id=offboarding_run_b.id,
            current_user=_admin_user("tenant-A"),
            db=db,
        )
    assert offboarding_cross_tenant.value.status_code == 404

    global_onboarding_runs = await list_onboarding_runs(
        limit=50,
        offset=0,
        current_user=_admin_user(None),
        db=db,
    )
    assert global_onboarding_runs["total"] == 2

    global_offboarding_runs = await list_offboarding_runs(
        limit=50,
        offset=0,
        current_user=_admin_user(None),
        db=db,
    )
    assert global_offboarding_runs["total"] == 2


@pytest.mark.asyncio
async def test_enforce_server_side_session_rejects_disabled_user(db):
    user = User(
        id=str(uuid.uuid4()),
        azure_id="oid-disabled",
        email="disabled@example.com",
        name="Disabled",
        is_active=False,
    )
    db.add(user)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await _enforce_server_side_session(
            request=_request(),
            db=db,
            user=UserInfo(
                id="oid-disabled",
                email="disabled@example.com",
                name="Disabled",
                roles=["user"],
                tenant_id="tenant-A",
            ),
            token_jti="jti-disabled",
            token_exp=None,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "User account is disabled"


@pytest.mark.asyncio
async def test_enforce_server_side_session_rejects_revoked_session(db):
    user = User(
        id=str(uuid.uuid4()),
        azure_id="oid-revoked",
        email="revoked@example.com",
        name="Revoked",
        is_active=True,
    )
    db.add(user)
    await db.commit()

    await get_or_create_session(
        db,
        user_id=user.id,
        token_jti="jti-revoked",
    )
    await revoke_session_by_jti(db, "jti-revoked")

    with pytest.raises(HTTPException) as exc:
        await _enforce_server_side_session(
            request=_request(),
            db=db,
            user=UserInfo(
                id="oid-revoked",
                email="revoked@example.com",
                name="Revoked",
                roles=["user"],
                tenant_id="tenant-A",
            ),
            token_jti="jti-revoked",
            token_exp=None,
        )

    assert exc.value.status_code == 401
    assert "Session revoked" in exc.value.detail
