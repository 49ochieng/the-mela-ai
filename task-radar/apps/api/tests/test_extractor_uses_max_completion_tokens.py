"""Verify the GPT-5.2 extractor sends `max_completion_tokens` and never `max_tokens`.

This was the root cause of the 103/0/54 incident: the deployment rejects
`max_tokens` with a 400 `unsupported_parameter` error.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.extractor import (
    _reset_client_for_tests, extract_with_diagnostics,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_client_for_tests()
    yield
    _reset_client_for_tests()


def _aoai_response_with(json_payload: dict):
    msg = SimpleNamespace(content=json.dumps(json_payload))
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage)


@pytest.mark.asyncio
async def test_extractor_uses_max_completion_tokens_only():
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _aoai_response_with({"has_task": False, "tasks": []})

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    with patch(
        "app.services.ai.extractor.AsyncAzureOpenAI",
        return_value=fake_client,
    ):
        _result, diag = await extract_with_diagnostics(
            {"source": "email", "subject_or_channel": "x", "body_text": "y"}
        )

    assert "max_completion_tokens" in captured, captured
    assert "max_tokens" not in captured, (
        "GPT-5.2 deployment rejects max_tokens — it MUST NOT be sent."
    )
    assert diag.error_category is None
    assert diag.finish_reason == "stop"
