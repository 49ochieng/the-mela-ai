"""When AOAI returns a hard config error (deployment unknown / parameter
unsupported / 401), the extractor must raise ExtractorConfigError so the scan
fails loudly instead of looping through every message and accumulating errors
the operator can't act on."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from openai import BadRequestError

from app.services.ai.extractor import (
    ExtractorConfigError, _reset_client_for_tests, extract_with_diagnostics,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_client_for_tests()
    yield
    _reset_client_for_tests()


def _make_bad_request(code: str, message: str) -> BadRequestError:
    body = {"error": {"code": code, "message": message}}
    response = httpx.Response(
        400,
        json=body,
        request=httpx.Request("POST", "https://example/openai"),
    )
    return BadRequestError(message=message, response=response, body=body)


@pytest.mark.asyncio
async def test_unsupported_parameter_raises_config_error():
    async def fake_create(**_kw):
        raise _make_bad_request(
            "unsupported_parameter",
            "Unsupported parameter: 'max_tokens' is not supported with this model.",
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    with patch("app.services.ai.extractor.AsyncAzureOpenAI", return_value=fake_client):
        with pytest.raises(ExtractorConfigError):
            await extract_with_diagnostics(
                {"source": "email", "subject_or_channel": "x", "body_text": "y"}
            )
