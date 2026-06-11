import pytest
from app.enums import SourceType
from app.models import SourceMessage, Tenant, User
from app.schemas import ExtractedTask, ExtractionResult
from app.services.tasks.persistence import persist_extraction


@pytest.mark.asyncio
async def test_persist_creates_task_and_routes_low_confidence(session):
    t = Tenant(entra_tenant_id="t", name="t"); session.add(t); await session.flush()
    u = User(tenant_id=t.id, entra_user_id="u", display_name="u", email="u@x", timezone="UTC", role="user")
    session.add(u); await session.flush()
    sm = SourceMessage(
        tenant_id=t.id, user_id=u.id, source_type=SourceType.EMAIL.value,
        graph_message_id="m1", body_hash="h",
    )
    session.add(sm); await session.commit()

    result = ExtractionResult(has_task=True, tasks=[
        ExtractedTask(title="High conf task", description="d", task_type="respond",
                      priority="medium", priority_reasoning="r",
                      confidence=0.9, evidence="e"),
        ExtractedTask(title="Low conf task", description="d", task_type="respond",
                      priority="low", priority_reasoning="r",
                      confidence=0.4, evidence="e"),
    ])
    created, deduped = await persist_extraction(
        session, tenant_id=t.id, user_id=u.id, source_message=sm, extraction=result,
    )
    assert len(created) == 2
    titles = {c.title: c.status for c in created}
    assert titles["Low conf task"] == "needs_review"
    assert titles["High conf task"] == "open"
