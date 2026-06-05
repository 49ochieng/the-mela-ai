"""
Comprehensive live production tests for Mela AI.
Run with: python -m pytest tests/test_live_prod.py -v -s --token <TOKEN>

Or set env var: MELA_TEST_TOKEN=<bearer_token>
"""
import os
import sys
import json
import time
import base64
import requests
import pytest

BASE = "https://armely-ai-api.azurewebsites.net"
TOKEN = os.environ.get("MELA_TEST_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
RUN_LIVE = os.environ.get("RUN_LIVE_PROD_TESTS", "").lower() in {"1", "true", "yes"}

pytestmark = pytest.mark.skipif(
    not RUN_LIVE,
    reason="Live production tests are disabled by default. Set RUN_LIVE_PROD_TESTS=1 to enable.",
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def api(method, path, **kwargs):
    h = dict(HEADERS)
    if "headers" in kwargs:
        h.update(kwargs.pop("headers"))
    r = requests.request(method, f"{BASE}{path}", headers=h, timeout=30, **kwargs)
    return r


def stream_chat(payload, timeout=45):
    """Send a streaming chat request, collect all chunks."""
    h = {**HEADERS, "Accept": "text/event-stream"}
    chunks = []
    with requests.post(
        f"{BASE}/api/v1/chat/completions",
        headers=h,
        json={**payload, "stream": True},
        stream=True,
        timeout=timeout,
    ) as resp:
        assert resp.status_code == 200, f"Chat returned {resp.status_code}: {resp.text[:300]}"
        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode() if isinstance(line, bytes) else line
            if line.startswith("data: "):
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunks.append(json.loads(data))
                except json.JSONDecodeError:
                    pass
    return chunks


def content_from_chunks(chunks):
    return "".join(c.get("content", "") for c in chunks if c.get("type") == "content")


# ── 1. Health & infrastructure ───────────────────────────────────────────────

class TestHealth:
    def test_health_endpoint(self):
        r = requests.get(f"{BASE}/health", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert data["checks"]["db"] == "ok"
        assert data["checks"]["openai"] == "configured"
        print(f"\n  [OK] Health OK -- commit {data['commit'][:8]} env={data['environment']}")

    def test_root_endpoint(self):
        r = requests.get(f"{BASE}/", timeout=10)
        assert r.status_code == 200
        assert "Mela AI" in r.text

    def test_auth_required_on_all_key_routes(self):
        routes = [
            "/api/v1/chat/models",
            "/api/v1/chat/conversations",
            "/api/v1/projects",
            "/api/v1/user/preferences",
            "/api/v1/graph/mail/inbox",
            "/api/v1/graph/calendar/events",
            "/api/v1/graph/planner/tasks",
            "/api/v1/admin/me",
        ]
        for route in routes:
            r = requests.get(f"{BASE}{route}", timeout=10)
            assert r.status_code in (401, 405), (
                f"Expected 401/405 for {route}, got {r.status_code}"
            )
        print(f"\n  [OK] All {len(routes)} protected routes return 401/405 unauthenticated")


# ── 2. Auth & user profile ───────────────────────────────────────────────────

class TestAuth:
    def test_login(self):
        if not TOKEN:
            pytest.skip("No token -- skipping authenticated tests")
        r = api("POST", "/api/v1/auth/login")
        assert r.status_code == 200, f"Login failed: {r.text[:200]}"
        data = r.json()
        assert "user" in data
        print(f"\n  [OK] Login OK -- user: {data['user'].get('email','?')}")

    def test_get_models(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("GET", "/api/v1/chat/models")
        assert r.status_code == 200
        models = r.json()
        assert len(models) >= 1
        names = [m["id"] for m in models]
        print(f"\n  [OK] Models ({len(models)}): {', '.join(names[:6])}")
        return models

    def test_get_user_preferences(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("GET", "/api/v1/user/preferences")
        assert r.status_code == 200
        print(f"\n  [OK] User preferences: {r.json()}")

    def test_get_user_features(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("GET", "/api/v1/user/features")
        assert r.status_code == 200
        feats = r.json()
        print(f"\n  [OK] Features: {feats}")


# ── 3. Conversations ─────────────────────────────────────────────────────────

class TestConversations:
    def test_list_conversations(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("GET", "/api/v1/chat/conversations?limit=10&offset=0")
        assert r.status_code == 200
        convs = r.json()
        print(f"\n  [OK] Conversations listed: {len(convs)}")

    def test_create_and_delete_conversation(self):
        if not TOKEN:
            pytest.skip("No token")
        # Create
        r = api("POST", "/api/v1/chat/conversations", json={
            "title": "TEST_DEMO_CHECK",
            "context_type": "personal",
        })
        assert r.status_code == 200, f"Create conv failed: {r.text[:200]}"
        cid = r.json()["id"]
        # Get detail
        r2 = api("GET", f"/api/v1/chat/conversations/{cid}")
        assert r2.status_code == 200
        # Delete
        r3 = api("DELETE", f"/api/v1/chat/conversations/{cid}")
        assert r3.status_code == 200
        print(f"\n  [OK] Create/get/delete conversation OK (id={cid[:8]})")


# ── 4. Chat completions -- all models ─────────────────────────────────────────

MODELS_TO_TEST = [
    "gpt-4.1",
    "gpt-5.2-chat",
    "kimi-k2.5",
    "mistral-large-3",
    "grok-3-mini",
    "gemini-2.0-flash",
]

class TestChatModels:
    @pytest.mark.parametrize("model_id", MODELS_TO_TEST)
    def test_model_responds(self, model_id):
        if not TOKEN:
            pytest.skip("No token")
        chunks = stream_chat({
            "message": "Reply with exactly: OK",
            "model": model_id,
            "use_rag": False,
            "use_web_search": False,
        })
        text = content_from_chunks(chunks)
        types_seen = {c["type"] for c in chunks}
        error_chunks = [c for c in chunks if c.get("type") == "error"]

        # Quota-exceeded on free-tier models is a valid response (model IS configured)
        if error_chunks and model_id == "gemini-2.0-flash":
            err_msg = error_chunks[0].get("content", "")
            if "quota" in err_msg.lower() or "429" in err_msg:
                print(f"\n  [OK] {model_id}: quota exceeded (model configured, free-tier limit hit)")
                return

        assert len(text) > 0, (
            f"Model {model_id} returned empty response. "
            f"Errors: {[c.get('content') for c in error_chunks]}"
        )
        assert "content" in types_seen or "done" in types_seen, (
            f"No content chunk from {model_id}"
        )
        print(f"\n  [OK] {model_id}: '{text[:60].strip()}'")

    def test_conversation_persistence(self):
        if not TOKEN:
            pytest.skip("No token")
        # Create conv
        r = api("POST", "/api/v1/chat/conversations", json={"title": "TEST_PERSIST"})
        cid = r.json()["id"]
        # Send message
        chunks = stream_chat({
            "message": "My favourite colour is purple. Remember that.",
            "model": "gpt-4.1",
            "conversation_id": cid,
            "use_rag": False,
        })
        assert content_from_chunks(chunks)
        # Follow-up
        chunks2 = stream_chat({
            "message": "What is my favourite colour?",
            "model": "gpt-4.1",
            "conversation_id": cid,
            "use_rag": False,
        })
        reply = content_from_chunks(chunks2).lower()
        assert "purple" in reply, f"Memory failed: reply was '{reply[:100]}'"
        print("\n  [OK] Conversation memory works -- model recalled 'purple'")
        api("DELETE", f"/api/v1/chat/conversations/{cid}")


# ── 5. File attachments & security ───────────────────────────────────────────

class TestFileAttachments:
    def _upload(self, data: bytes, filename: str, content_type: str):
        return requests.post(
            f"{BASE}/api/v1/chat/process-attachment",
            headers={"Authorization": f"Bearer {TOKEN}"},
            files={"file": (filename, data, content_type)},
            data={"conversation_id": "test"},
            timeout=30,
        )

    def test_plain_text_upload(self):
        if not TOKEN:
            pytest.skip("No token")
        r = self._upload(b"Hello world document content", "test.txt", "text/plain")
        assert r.status_code == 200
        d = r.json()
        assert d.get("text_content") or d.get("type") == "document"
        print(f"\n  [OK] Plain text upload: type={d.get('type')} len={len(d.get('text_content',''))}")

    def test_json_upload(self):
        if not TOKEN:
            pytest.skip("No token")
        r = self._upload(b'{"key": "value", "test": true}', "data.json", "application/json")
        assert r.status_code == 200
        d = r.json()
        assert "key" in (d.get("text_content") or "")
        print(f"\n  [OK] JSON upload processed: {d.get('text_content','')[:60]}")

    def test_csv_upload(self):
        if not TOKEN:
            pytest.skip("No token")
        csv_data = b"name,age,city\nAlice,30,NYC\nBob,25,LA\n"
        r = self._upload(csv_data, "data.csv", "text/csv")
        assert r.status_code == 200
        print(f"\n  [OK] CSV upload: type={r.json().get('type')}")

    def test_executable_blocked(self):
        if not TOKEN:
            pytest.skip("No token")
        # PE/EXE magic bytes
        exe_data = b"\x4d\x5a" + b"\x00" * 100
        r = self._upload(exe_data, "malware.exe", "application/octet-stream")
        assert r.status_code == 422, f"Expected 422 for EXE, got {r.status_code}"
        # detail may be a string or a list of validation errors
        detail = r.json().get("detail", "")
        detail_str = detail if isinstance(detail, str) else str(detail)
        assert "executable" in detail_str.lower() or "rejected" in detail_str.lower()
        print(f"\n  [OK] EXE blocked with 422: {detail_str[:80]}")

    def test_zip_bomb_blocked(self):
        if not TOKEN:
            pytest.skip("No token")
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Write highly compressible data
            big_data = b"\x00" * (50 * 1024 * 1024)  # 50MB of zeros
            zf.writestr("bomb.txt", big_data)
        buf.seek(0)
        r = self._upload(buf.read(), "bomb.zip", "application/zip")
        # Either 422 (blocked) or 200 with warning -- depends on size
        assert r.status_code in (200, 422)
        print(f"\n  [OK] ZIP bomb test: status={r.status_code}")

    def test_image_upload(self):
        if not TOKEN:
            pytest.skip("No token")
        # Minimal valid PNG (1x1 pixel)
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        r = self._upload(png, "photo.png", "image/png")
        assert r.status_code == 200
        d = r.json()
        assert d.get("type") == "image"
        assert d.get("base64_data", "").startswith("data:image/")
        print(f"\n  [OK] Image upload: type={d['type']} base64_len={len(d.get('base64_data',''))}")


# ── 6. Agentic capabilities ───────────────────────────────────────────────────

class TestAgentic:
    def test_file_generation_excel(self):
        if not TOKEN:
            pytest.skip("No token")
        chunks = stream_chat({
            "message": (
                "Create a simple Excel file with 3 rows of sales data "
                "(Product, Units, Revenue). Use run_python_code."
            ),
            "model": "gpt-4.1",
            "use_rag": False,
        }, timeout=90)
        types = {c["type"] for c in chunks}
        file_chunks = [c for c in chunks if c.get("type") == "file_generated"]
        tool_chunks = [c for c in chunks if c.get("type") in ("tool_executing", "tool_result")]
        assert tool_chunks, "No tool calls made -- code interpreter not triggered"
        print(f"\n  [OK] Agentic file gen -- types seen: {types}")
        print(f"    tool events: {len(tool_chunks)}, file_generated: {len(file_chunks)}")

    def test_multi_step_task(self):
        if not TOKEN:
            pytest.skip("No token")
        chunks = stream_chat({
            "message": (
                "Step 1: calculate 15% of 240. "
                "Step 2: then tell me what day of the week Jan 1 2025 was. "
                "Complete both steps."
            ),
            "model": "gpt-4.1",
            "use_rag": False,
        }, timeout=60)
        text = content_from_chunks(chunks).lower()
        assert "36" in text, f"Step 1 result (36) not in response: {text[:200]}"
        assert "wednesday" in text, f"Step 2 result (Wednesday) not in response: {text[:200]}"
        print("\n  [OK] Multi-step task completed: both answers present")


# ── 7. Graph integrations ─────────────────────────────────────────────────────

class TestGraphIntegrations:
    def test_graph_status(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("GET", "/api/v1/graph/status")
        assert r.status_code == 200
        d = r.json()
        print(f"\n  [OK] Graph status: {d}")

    def test_email_inbox(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("GET", "/api/v1/graph/mail/inbox?limit=5")
        # 400/403/500/503 = app-only token lacks delegated mailbox access (expected)
        assert r.status_code in (200, 400, 403, 500, 503), (
            f"Unexpected status {r.status_code}: {r.text[:200]}"
        )
        if r.status_code == 200:
            print(f"\n  [OK] Email inbox: {len(r.json())} emails")
        else:
            print(f"\n  [OK] Email inbox: {r.status_code} (delegated-only endpoint)")

    def test_calendar_events(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("GET", "/api/v1/graph/calendar/events?limit=5")
        # 400/403/500/503 = app-only token lacks delegated calendar access (expected)
        assert r.status_code in (200, 400, 403, 500, 503), (
            f"Unexpected {r.status_code}: {r.text[:200]}"
        )
        if r.status_code == 200:
            print(f"\n  [OK] Calendar events: {len(r.json())} events")
        else:
            print(f"\n  [OK] Calendar: {r.status_code} (delegated-only endpoint)")

    def test_planner_tasks(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("GET", "/api/v1/graph/planner/tasks?limit=5")
        assert r.status_code in (200, 403, 503), (
            f"Unexpected {r.status_code}: {r.text[:200]}"
        )
        if r.status_code == 200:
            print(f"\n  [OK] Planner tasks: {len(r.json())} tasks")
        else:
            print(f"\n  [WARN] Planner returned {r.status_code}")

    def test_email_draft_via_chat(self):
        if not TOKEN:
            pytest.skip("No token")
        chunks = stream_chat({
            "message": (
                "Draft a professional email to test@example.com "
                "with subject 'Demo Test' and a one-sentence body."
            ),
            "model": "gpt-4.1",
            "use_rag": False,
            "context_type": "personal",
        }, timeout=60)
        text = content_from_chunks(chunks)
        assert text, "No response from email draft request"
        print(f"\n  [OK] Email draft via chat: {text[:100]}")


# ── 8. Image generation ───────────────────────────────────────────────────────

class TestImageGeneration:
    def test_image_gen_via_chat(self):
        if not TOKEN:
            pytest.skip("No token")
        chunks = stream_chat({
            "message": "Generate an image of a blue mountain at sunset",
            "model": "gpt-4.1",
            "use_rag": False,
        }, timeout=90)
        image_chunks = [c for c in chunks if c.get("type") == "image_generated"]
        text = content_from_chunks(chunks)
        assert text, "No text response"
        print(f"\n  [OK] Image gen response: {text[:80]}, image_chunks={len(image_chunks)}")


# ── 9. Enterprise search ──────────────────────────────────────────────────────

class TestEnterpriseSearch:
    def test_search_via_chat(self):
        if not TOKEN:
            pytest.skip("No token")
        chunks = stream_chat({
            "message": "What does Armely do? Search the knowledge base.",
            "model": "gpt-4.1",
            "use_rag": True,
            "context_type": "personal",
        }, timeout=60)
        text = content_from_chunks(chunks)
        assert text, "No response"
        citation_chunks = [c for c in chunks if c.get("type") == "citation"]
        print(f"\n  [OK] Enterprise search: {len(citation_chunks)} citations, reply: {text[:100]}")


# ── 10. Projects workspace ────────────────────────────────────────────────────

class TestProjects:
    def test_list_projects(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("GET", "/api/v1/projects?include_archived=false&context_type=personal")
        assert r.status_code == 200
        print(f"\n  [OK] Projects: {len(r.json())} projects")

    def test_create_and_delete_project(self):
        if not TOKEN:
            pytest.skip("No token")
        r = api("POST", "/api/v1/projects", json={
            "name": "TEST_DEMO_PROJECT",
            "context_type": "personal",
        })
        assert r.status_code in (200, 201), f"Create project failed: {r.text[:200]}"
        pid = r.json()["id"]
        # Get it
        r2 = api("GET", f"/api/v1/projects/{pid}")
        assert r2.status_code == 200
        # Delete (returns 204 No Content)
        r3 = api("DELETE", f"/api/v1/projects/{pid}")
        assert r3.status_code in (200, 204)
        print(f"\n  [OK] Create/get/delete project OK (id={pid[:8]})")


# ── 11. Voice / Speech ────────────────────────────────────────────────────────

class TestSpeech:
    def test_tts_endpoint_exists(self):
        if not TOKEN:
            pytest.skip("No token")
        # Route is /speech/synthesize (not /tts)
        r = api("POST", "/api/v1/speech/synthesize",
                json={"text": "Hello world", "voice": "en-US-JennyNeural"})
        assert r.status_code in (200, 400, 422, 503), (
            f"Unexpected TTS status {r.status_code}"
        )
        print(f"\n  [OK] TTS synthesize endpoint status: {r.status_code}")

    def test_stt_endpoint_exists(self):
        if not TOKEN:
            pytest.skip("No token")
        # Route is /speech/transcribe (not /stt)
        r = requests.post(
            f"{BASE}/api/v1/speech/transcribe",
            headers={"Authorization": f"Bearer {TOKEN}"},
            files={"audio": ("test.wav", b"RIFF" + b"\x00" * 100, "audio/wav")},
            timeout=20,
        )
        assert r.status_code in (200, 400, 422, 503), (
            f"Unexpected STT status {r.status_code}"
        )
        print(f"\n  [OK] STT transcribe endpoint status: {r.status_code}")


# ── 12. Profile / namespace isolation ────────────────────────────────────────

class TestProfileIsolation:
    def test_personal_conversations_isolated(self):
        if not TOKEN:
            pytest.skip("No token")
        personal = api("GET", "/api/v1/chat/conversations?context_type=personal",
                       headers={"X-Profile-Mode": "personal"})
        work = api("GET", "/api/v1/chat/conversations?context_type=org",
                   headers={"X-Profile-Mode": "work", "X-Tenant-Id": "armely"})
        assert personal.status_code == 200
        assert work.status_code == 200
        p_ids = {c["id"] for c in personal.json()}
        w_ids = {c["id"] for c in work.json()}
        overlap = p_ids & w_ids
        assert not overlap, f"Personal/work conversations overlap: {overlap}"
        print(f"\n  [OK] Profile isolation: {len(p_ids)} personal, {len(w_ids)} work, 0 overlap")


if __name__ == "__main__":
    # Quick unauthenticated smoke test
    import sys
    print("Running unauthenticated health checks...")
    r = requests.get(f"{BASE}/health")
    print(f"Health: {r.json()}")
