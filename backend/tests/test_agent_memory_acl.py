"""ACL and scope construction tests for agent_memory_service.

These exercise pure helpers that do not require a running DB or Azure Search.
"""

from types import SimpleNamespace

import pytest

from app.services.agent_memory_service import agent_memory_service


def _user(uid="u1", azure="azure-u1"):
    return SimpleNamespace(id=uid, azure_id=azure)


# ── _build_acl ───────────────────────────────────────────────────────────────


def test_personal_scope_acl_restricts_to_owner_azure_id():
    users, groups = agent_memory_service._build_acl("personal", _user(), tenant_id="tenant-A")
    # ACL includes both Entra OID and DB primary key so retrieval succeeds
    # whether the chat path uses azure_id (prod) or user.id (dev).
    assert users == ["azure-u1", "u1"]
    assert groups == []


def test_workspace_scope_acl_uses_tenant_group():
    users, groups = agent_memory_service._build_acl("workspace", _user(), tenant_id="tenant-A")
    assert users == []
    assert groups == ["tenant-A"]


def test_workspace_scope_with_no_tenant_yields_no_acl_group():
    # Defensive: caller should reject this earlier, but the helper must not
    # accidentally produce a fully open ACL by including a literal None.
    users, groups = agent_memory_service._build_acl("workspace", _user(), tenant_id=None)
    assert users == []
    assert None not in groups


def test_tenant_scope_acl_is_empty_relying_on_workspace_id_filter():
    users, groups = agent_memory_service._build_acl("tenant", _user(), tenant_id="tenant-A")
    assert users == []
    assert groups == []


# ── _workspace_id ────────────────────────────────────────────────────────────


def test_personal_scope_workspace_is_user_namespace():
    wid = agent_memory_service._workspace_id(_user("user-7"), tenant_id="tenant-A", scope="personal")
    assert wid == "user:user-7"


def test_workspace_scope_uses_tenant_id():
    wid = agent_memory_service._workspace_id(_user(), tenant_id="tenant-A", scope="workspace")
    assert wid == "tenant-A"


def test_no_tenant_falls_back_to_user_namespace():
    wid = agent_memory_service._workspace_id(_user("user-9"), tenant_id=None, scope="workspace")
    assert wid == "user:user-9"


# ── Constants are sane ───────────────────────────────────────────────────────


def test_valid_scope_set_includes_three_levels():
    from app.services.agent_memory_service import VALID_SCOPES
    assert VALID_SCOPES == {"personal", "workspace", "tenant"}


def test_valid_tag_set_includes_template_and_policy():
    from app.services.agent_memory_service import VALID_TAGS
    assert "template" in VALID_TAGS
    assert "policy" in VALID_TAGS
    assert "knowledge" in VALID_TAGS
