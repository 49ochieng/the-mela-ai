"""Application configuration loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    frontend_url: str  # required — set FRONTEND_URL in env
    backend_url: str   # required — set BACKEND_URL in env

    # Security
    secret_key: str    # required — set SECRET_KEY in env
    jwt_secret: str    # required — set JWT_SECRET in env
    # Comma-separated list of older JWT secrets still accepted for verification
    # (signing always uses jwt_secret). Used for zero-downtime key rotation:
    # after promoting a new primary, leave the old key here for ≥ access token
    # lifetime so existing sessions don't 401 mid-flight.
    jwt_secrets_secondary: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    token_encryption_key: str = ""  # Fernet key (32-byte url-safe base64). Auto-generated in dev if blank.
    # Comma-separated list of older Fernet keys still accepted for decryption.
    # Same rotation pattern as jwt_secrets_secondary; combined with FernetTokenStore.rotate(),
    # callers can re-encrypt references in place during a maintenance window.
    token_encryption_keys_secondary: str = ""

    # Session cookie
    cookie_name: str = "mtr_session"
    cookie_secure: bool = False  # True in production (HTTPS)
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str = ""  # leave blank for host-only cookie (recommended for localhost)
    # When true (production default), prefix the cookie with __Host- per
    # https://datatracker.ietf.org/doc/html/draft-ietf-httpbis-rfc6265bis#name-the-host-prefix
    # which forces Secure, Path=/, and disallows Domain. Browsers will reject
    # cookies that violate any of these.
    cookie_host_prefix: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./taskradar.db"

    # Microsoft Entra
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    # If true (or no client secret), use PKCE-only PublicClientApplication.
    # Set to true when the Azure App Registration is configured as a public client
    # (mobile/desktop, or "Allow public client flows" = Yes).
    azure_public_client: bool = False
    microsoft_redirect_uri: str  # required — set MICROSOFT_REDIRECT_URI in env
    graph_scopes: str = (
        "openid profile offline_access User.Read Mail.Read Files.ReadWrite "
        "Tasks.ReadWrite Group.Read.All "
        "Team.ReadBasic.All Channel.ReadBasic.All ChannelMessage.Read.All "
        "Chat.Read ChatMessage.Read"
    )

    # Azure OpenAI
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment_gpt52: str = "gpt-5.2-chat"
    azure_openai_api_version: str = "2024-05-01-preview"

    # Storage
    azure_blob_connection_string: str = ""
    azure_blob_container: str = "taskradar-attachments"
    local_storage_path: str = "./storage"

    # Queue
    queue_provider: Literal["memory", "servicebus"] = "memory"
    azure_service_bus_connection_string: str = ""
    azure_service_bus_queue: str = "taskradar-scans"

    # Key Vault
    key_vault_url: str = ""

    # Defense-in-depth toggles. Default ON; tests flip them off explicitly.
    rate_limit_enabled: bool = True
    csrf_enabled: bool = True

    # MCP
    mcp_server_url: str = ""
    mcp_api_key: str = ""

    # Mela AI HTTP layer (separate key so Mela AI can call without
    # the MCP transport — falls back to mcp_api_key when blank).
    mela_api_key: str = ""

    # Feature flags
    enable_teams_scan: bool = False
    enable_excel_sync: bool = True
    enable_planner_sync: bool = True
    enable_mcp_server: bool = True
    enable_realtime_webhooks: bool = False

    # Observability
    applicationinsights_connection_string: str = ""

    @property
    def graph_scope_list(self) -> list[str]:
        return [s for s in self.graph_scopes.split() if s]

    @property
    def effective_cookie_name(self) -> str:
        """Return the cookie name with ``__Host-`` prepended when host-prefix
        is enabled and the requirements are satisfied."""
        if self.cookie_host_prefix and self.cookie_secure and not self.cookie_domain:
            return f"__Host-{self.cookie_name}"
        return self.cookie_name


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
