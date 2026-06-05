"""
Provider contract test for the Gemini adapter.

Verifies that the message converter produces correctly-typed
google-genai Content/Part objects before any network call is made.
The streaming test is marked 'live' and skipped in CI (requires a
valid GOOGLE_AI_API_KEY with available quota).

Run contract tests only (no network):
    pytest tests/test_gemini_provider.py -m "not live" -v

Run live test locally:
    pytest tests/test_gemini_provider.py -m live -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Message converter — pure unit tests (no network, no API key needed)
# ---------------------------------------------------------------------------

class TestConvertMessages:
    """Verify _convert_messages produces valid google-genai Content objects."""

    @pytest.fixture(autouse=True)
    def _guard(self):
        """Skip if google-genai is not installed."""
        pytest.importorskip("google.genai")

    def _convert(self, messages):
        import sys
        import os
        sys.path.insert(0, ".")
        # Patch settings so we don't need a real API key
        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": "test-key"}):
            from app.services.gemini_service import _convert_messages
            return _convert_messages(messages)

    def test_single_user_message(self):
        from google.genai import types
        sys_text, contents = self._convert([
            {"role": "user", "content": "hello"},
        ])
        assert sys_text == ""
        assert len(contents) == 1
        assert isinstance(contents[0], types.Content)
        assert contents[0].role == "user"
        assert contents[0].parts[0].text == "hello"

    def test_system_extracted(self):
        sys_text, contents = self._convert([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ])
        assert sys_text == "You are helpful."
        assert len(contents) == 1
        assert contents[0].role == "user"

    def test_assistant_role_mapped_to_model(self):
        from google.genai import types
        _, contents = self._convert([
            {"role": "user",      "content": "ping"},
            {"role": "assistant", "content": "pong"},
            {"role": "user",      "content": "again"},
        ])
        assert contents[0].role == "user"
        assert contents[1].role == "model"
        assert contents[2].role == "user"

    def test_consecutive_user_messages_merged(self):
        _, contents = self._convert([
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ])
        assert len(contents) == 1
        assert "first" in contents[0].parts[0].text
        assert "second" in contents[0].parts[0].text

    def test_last_turn_forced_to_user(self):
        _, contents = self._convert([
            {"role": "user",      "content": "ping"},
            {"role": "assistant", "content": "pong"},
        ])
        assert contents[-1].role == "user"

    def test_empty_messages_returns_fallback(self):
        _, contents = self._convert([])
        assert len(contents) == 1
        assert contents[0].role == "user"

    def test_tool_result_becomes_user_turn(self):
        _, contents = self._convert([
            {"role": "user",   "content": "search for X"},
            {"role": "tool",   "content": "result: Y"},
        ])
        assert all(c.role in ("user", "model") for c in contents)
        assert any("Tool result" in c.parts[0].text for c in contents)

    def test_parts_are_part_objects(self):
        from google.genai import types
        _, contents = self._convert([{"role": "user", "content": "test"}])
        assert isinstance(contents[0].parts[0], types.Part)

    def test_vision_content_extracts_text(self):
        _, contents = self._convert([{
            "role": "user",
            "content": [
                {"type": "text", "text": "describe this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }])
        assert "describe this" in contents[0].parts[0].text


# ---------------------------------------------------------------------------
# Live streaming test (skipped in CI)
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.asyncio
async def test_gemini_hello_world():
    """
    End-to-end: sends 'Say hello' to Gemini and verifies a content chunk
    is received.  Requires GOOGLE_AI_API_KEY with available quota.
    Skipped automatically when quota is exhausted (429).
    """
    import os
    if not os.environ.get("GOOGLE_AI_API_KEY"):
        pytest.skip("GOOGLE_AI_API_KEY not set")

    from app.services.gemini_service import GeminiService
    svc = GeminiService()

    chunks = []
    async for chunk in svc.stream_completion(
        [{"role": "user", "content": "Say hello in 3 words."}]
    ):
        chunks.append(chunk)

    types_seen = {c.type for c in chunks}

    if any("quota" in (c.content or "").lower() for c in chunks if c.type == "error"):
        pytest.skip("Gemini quota exhausted — skipping live assertion")

    assert "error" not in types_seen, f"Unexpected error: {chunks}"
    assert "content" in types_seen, "No content chunk received"
    assert "done" in types_seen, "Stream did not complete"
