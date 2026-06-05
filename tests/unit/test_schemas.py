"""
Mela AI - Unit Tests for Pydantic Schema Validation
"""

import pytest
from pydantic import ValidationError

from app.schemas.auth import UserInfo
from app.schemas.chat import ChatRequest
from app.schemas.documents import DocumentUpload, SearchRequest


# ---------------------------------------------------------------------------
# UserInfo schema
# ---------------------------------------------------------------------------

class TestUserInfoSchema:
    """Validate the UserInfo schema used throughout authentication."""

    def test_valid_user_info(self):
        user = UserInfo(
            id="abc-123",
            email="jane@armely.com",
            name="Jane Doe",
        )
        assert user.id == "abc-123"
        assert user.email == "jane@armely.com"
        assert user.name == "Jane Doe"

    def test_user_info_optional_fields_default(self):
        user = UserInfo(id="1", email="a@b.com", name="A")
        assert user.given_name is None
        assert user.family_name is None
        assert user.roles == []
        assert user.department is None
        assert user.job_title is None
        assert user.tenant_id is None

    def test_user_info_with_all_fields(self):
        user = UserInfo(
            id="u-1",
            email="full@armely.com",
            name="Full User",
            given_name="Full",
            family_name="User",
            roles=["Admin", "user"],
            department="Engineering",
            job_title="Lead",
            tenant_id="t-1",
        )
        assert "Admin" in user.roles
        assert user.department == "Engineering"

    def test_user_info_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            UserInfo(email="no-id@armely.com", name="Missing ID")

    def test_user_info_missing_email_raises(self):
        with pytest.raises(ValidationError):
            UserInfo(id="x", name="No Email")

    def test_user_info_missing_name_raises(self):
        with pytest.raises(ValidationError):
            UserInfo(id="x", email="x@x.com")


# ---------------------------------------------------------------------------
# ChatRequest schema
# ---------------------------------------------------------------------------

class TestChatRequestSchema:
    """Validate the ChatRequest schema used by the chat endpoint."""

    def test_minimal_chat_request(self):
        req = ChatRequest(message="Hello")
        assert req.message == "Hello"
        assert req.conversation_id is None
        assert req.model == "gpt-4o"
        assert req.use_rag is True
        assert req.stream is True

    def test_chat_request_with_all_fields(self):
        req = ChatRequest(
            message="Summarize the report",
            conversation_id="conv-1",
            model="gpt-4o-mini",
            attachments=["doc-1", "doc-2"],
            use_rag=False,
            stream=False,
            system_prompt="Be concise.",
        )
        assert req.conversation_id == "conv-1"
        assert req.model == "gpt-4o-mini"
        assert len(req.attachments) == 2
        assert req.use_rag is False
        assert req.stream is False
        assert req.system_prompt == "Be concise."

    def test_chat_request_empty_message_allowed(self):
        """An empty string is still a valid string for the message field."""
        req = ChatRequest(message="")
        assert req.message == ""

    def test_chat_request_missing_message_raises(self):
        with pytest.raises(ValidationError):
            ChatRequest()

    def test_chat_request_defaults_model(self):
        req = ChatRequest(message="test")
        assert req.model == "gpt-4o"


# ---------------------------------------------------------------------------
# DocumentUpload schema
# ---------------------------------------------------------------------------

class TestDocumentUploadSchema:
    """Validate the DocumentUpload schema."""

    def test_default_document_upload(self):
        doc = DocumentUpload()
        assert doc.title is None
        assert doc.add_to_knowledge_base is True

    def test_document_upload_with_title(self):
        doc = DocumentUpload(title="My Report", add_to_knowledge_base=False)
        assert doc.title == "My Report"
        assert doc.add_to_knowledge_base is False

    def test_document_upload_explicit_knowledge_base_true(self):
        doc = DocumentUpload(add_to_knowledge_base=True)
        assert doc.add_to_knowledge_base is True


# ---------------------------------------------------------------------------
# SearchRequest schema
# ---------------------------------------------------------------------------

class TestSearchRequestSchema:
    """Validate the SearchRequest schema including top_k bounds."""

    def test_minimal_search_request(self):
        req = SearchRequest(query="budget report")
        assert req.query == "budget report"
        assert req.top_k == 5
        assert req.filters is None

    def test_search_request_custom_top_k(self):
        req = SearchRequest(query="test", top_k=10)
        assert req.top_k == 10

    def test_search_request_top_k_lower_bound(self):
        """top_k must be >= 1."""
        with pytest.raises(ValidationError):
            SearchRequest(query="test", top_k=0)

    def test_search_request_top_k_upper_bound(self):
        """top_k must be <= 20."""
        with pytest.raises(ValidationError):
            SearchRequest(query="test", top_k=21)

    def test_search_request_top_k_at_min(self):
        req = SearchRequest(query="test", top_k=1)
        assert req.top_k == 1

    def test_search_request_top_k_at_max(self):
        req = SearchRequest(query="test", top_k=20)
        assert req.top_k == 20

    def test_search_request_top_k_negative_raises(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="test", top_k=-1)

    def test_search_request_with_filters(self):
        req = SearchRequest(
            query="finance",
            top_k=3,
            filters={"source": "sharepoint", "file_type": "pdf"},
        )
        assert req.filters["source"] == "sharepoint"
        assert req.filters["file_type"] == "pdf"

    def test_search_request_missing_query_raises(self):
        with pytest.raises(ValidationError):
            SearchRequest(top_k=5)
