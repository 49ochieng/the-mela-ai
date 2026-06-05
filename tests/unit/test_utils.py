"""
Mela AI - Unit Tests for Configuration and Utility Modules
"""

import logging
import pytest
from unittest.mock import patch, MagicMock


class TestConfigSettings:
    """Verify that application settings load with correct defaults."""

    def test_settings_has_app_name(self):
        from app.core.config import settings

        assert settings.APP_NAME == "Mela AI"

    def test_settings_default_environment(self):
        from app.core.config import settings

        assert settings.APP_ENV in ("development", "production", "staging")

    def test_settings_debug_flag_is_bool(self):
        from app.core.config import settings

        assert isinstance(settings.DEBUG, bool)

    def test_settings_api_prefix(self):
        from app.core.config import settings

        assert settings.API_PREFIX == "/api/v1"

    def test_settings_jwt_defaults(self):
        from app.core.config import settings

        assert settings.JWT_ALGORITHM == "HS256"
        assert isinstance(settings.ACCESS_TOKEN_EXPIRE_MINUTES, int)
        assert settings.ACCESS_TOKEN_EXPIRE_MINUTES > 0

    def test_settings_rag_defaults(self):
        from app.core.config import settings

        assert settings.RAG_CHUNK_SIZE > 0
        assert settings.RAG_CHUNK_OVERLAP >= 0
        assert settings.RAG_TOP_K >= 1
        assert 0.0 <= settings.RAG_SIMILARITY_THRESHOLD <= 1.0

    def test_settings_rate_limit_defaults(self):
        from app.core.config import settings

        assert settings.RATE_LIMIT_REQUESTS > 0
        assert settings.RATE_LIMIT_WINDOW > 0
        assert settings.DEFAULT_DAILY_TOKEN_LIMIT > 0
        assert settings.ADMIN_DAILY_TOKEN_LIMIT >= settings.DEFAULT_DAILY_TOKEN_LIMIT

    def test_settings_cors_origins_is_list(self):
        from app.core.config import settings

        assert isinstance(settings.CORS_ORIGINS, list)

    def test_settings_feature_flags_are_booleans(self):
        from app.core.config import settings

        assert isinstance(settings.ENABLE_VOICE, bool)
        assert isinstance(settings.ENABLE_FILE_UPLOAD, bool)
        assert isinstance(settings.ENABLE_AGENTS, bool)
        assert isinstance(settings.ENABLE_SHAREPOINT_SYNC, bool)

    def test_settings_database_url_property(self):
        """When DATABASE_URL is not set the property should build an MSSQL URL."""
        from app.core.config import settings

        db_url = settings.database_url
        assert isinstance(db_url, str)
        # If DATABASE_URL is provided it should be returned as-is;
        # otherwise the constructed string contains the mssql scheme.
        if not settings.DATABASE_URL:
            assert "mssql" in db_url


class TestAuditLogger:
    """Verify the AuditLogger utility."""

    def test_log_action_success(self, caplog):
        from app.core.logging import AuditLogger

        logger = AuditLogger()
        with caplog.at_level(logging.INFO, logger="audit"):
            logger.log_action(
                user_id="u-1",
                action="create",
                resource="document",
                details={"doc_id": "d-1"},
                success=True,
            )

        assert any("AUDIT:" in record.message for record in caplog.records)

    def test_log_action_failure(self, caplog):
        from app.core.logging import AuditLogger

        logger = AuditLogger()
        with caplog.at_level(logging.WARNING, logger="audit"):
            logger.log_action(
                user_id="u-2",
                action="delete",
                resource="conversation",
                success=False,
            )

        assert any("AUDIT_FAILED:" in record.message for record in caplog.records)

    def test_log_action_includes_user_and_action(self, caplog):
        from app.core.logging import AuditLogger

        logger = AuditLogger()
        with caplog.at_level(logging.INFO, logger="audit"):
            logger.log_action(
                user_id="user-xyz",
                action="login",
                resource="auth",
            )

        combined = " ".join(r.message for r in caplog.records)
        assert "user-xyz" in combined
        assert "login" in combined

    def test_log_action_default_details_is_empty_dict(self, caplog):
        from app.core.logging import AuditLogger

        logger = AuditLogger()
        with caplog.at_level(logging.INFO, logger="audit"):
            logger.log_action(
                user_id="u-3",
                action="view",
                resource="dashboard",
            )

        # The log message should contain an empty dict representation
        combined = " ".join(r.message for r in caplog.records)
        assert "{}" in combined

    def test_audit_logger_singleton_exists(self):
        from app.core.logging import audit_logger

        assert audit_logger is not None
        assert hasattr(audit_logger, "log_action")
