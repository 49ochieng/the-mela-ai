import pytest
from pydantic import ValidationError
from app.schemas import ExtractedTask, ExtractionResult


def test_extracted_task_strict_fields():
    t = ExtractedTask(
        title="Review report", description="Review the Q3 report.",
        task_type="review", assigned_to="me", due_date="2025-01-15",
        priority="high", priority_reasoning="explicit deadline",
        confidence=0.92, evidence="please review by Friday",
    )
    assert t.priority.value == "high"


def test_invalid_confidence_rejected():
    with pytest.raises(ValidationError):
        ExtractedTask(
            title="x", description="y", task_type="review",
            priority="medium", priority_reasoning="z", confidence=1.5, evidence="e",
        )


def test_extraction_result_no_tasks():
    r = ExtractionResult(has_task=False, tasks=[])
    assert r.has_task is False and r.tasks == []
