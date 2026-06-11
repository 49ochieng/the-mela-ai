import pytest
from app.enums import SourceType
from app.models import SourceMessage, Tenant, User
from app.services.tasks.dedup import message_already_seen


@pytest.mark.asyncio
async def test_dedup_by_graph_id(session):
    t = Tenant(entra_tenant_id="t", name="t"); session.add(t); await session.flush()
    u = User(tenant_id=t.id, entra_user_id="u", display_name="u", email="u@x", timezone="UTC", role="user")
    session.add(u); await session.flush()
    sm = SourceMessage(
        tenant_id=t.id, user_id=u.id, source_type=SourceType.EMAIL.value,
        graph_message_id="abc", body_hash="h1",
    )
    session.add(sm); await session.commit()
    found = await message_already_seen(
        session, tenant_id=t.id, user_id=u.id, source_type=SourceType.EMAIL.value,
        graph_message_id="abc", internet_message_id=None, body_hash="h1", received_at=None,
    )
    assert found is True


@pytest.mark.asyncio
async def test_dedup_misses_unseen(session):
    t = Tenant(entra_tenant_id="t", name="t"); session.add(t); await session.flush()
    u = User(tenant_id=t.id, entra_user_id="u", display_name="u", email="u@x", timezone="UTC", role="user")
    session.add(u); await session.flush()
    found = await message_already_seen(
        session, tenant_id=t.id, user_id=u.id, source_type=SourceType.EMAIL.value,
        graph_message_id="zzz", internet_message_id=None, body_hash="zzz", received_at=None,
    )
    assert found is False
