import pytest
from app.models import Task, Tenant, User


@pytest.mark.asyncio
async def test_two_tenants_are_isolated(session):
    t1 = Tenant(entra_tenant_id="t1", name="t1"); session.add(t1)
    t2 = Tenant(entra_tenant_id="t2", name="t2"); session.add(t2)
    await session.flush()
    u1 = User(tenant_id=t1.id, entra_user_id="u1", display_name="u", email="a@x", timezone="UTC", role="user")
    u2 = User(tenant_id=t2.id, entra_user_id="u2", display_name="u", email="b@x", timezone="UTC", role="user")
    session.add_all([u1, u2]); await session.flush()
    session.add(Task(tenant_id=t1.id, user_id=u1.id, title="A", description="", task_type="action_item",
                      priority="medium", confidence=0.9, status="open", source_type="email"))
    session.add(Task(tenant_id=t2.id, user_id=u2.id, title="B", description="", task_type="action_item",
                      priority="medium", confidence=0.9, status="open", source_type="email"))
    await session.commit()
    from sqlalchemy import select
    rows1 = (await session.execute(select(Task).where(Task.tenant_id == t1.id))).scalars().all()
    rows2 = (await session.execute(select(Task).where(Task.tenant_id == t2.id))).scalars().all()
    assert {r.title for r in rows1} == {"A"}
    assert {r.title for r in rows2} == {"B"}
