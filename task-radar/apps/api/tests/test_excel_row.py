from datetime import datetime
from app.enums import SourceType, TaskType, Priority, TaskStatus
from app.models import SourceMessage, Task
from app.services.excel.sync import _row


def test_excel_row_layout():
    sm = SourceMessage(
        source_type=SourceType.EMAIL.value, sender_email="boss@x.com",
        sender_name="Boss", subject_or_channel="Q3 Plan",
        received_at=datetime(2025, 1, 1, 10, 0), graph_message_id="g1",
        conversation_id="c1",
    )
    t = Task(
        title="Send report", description="Send the Q3 report",
        task_type=TaskType.RESPOND.value, priority=Priority.HIGH.value,
        priority_reasoning="explicit deadline",
        status=TaskStatus.OPEN.value, source_type=SourceType.EMAIL.value,
        source_link="https://outlook.office.com/...",
    )
    t.source_message = sm
    row = _row(t)
    assert len(row) == 15
    assert row[0] == "email"
    assert row[2] == "boss@x.com"
    assert row[5] == "respond"
    assert row[8] == "high"
