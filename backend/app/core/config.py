"""
Mela AI - Configuration Settings
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List, Optional, Any
from functools import lru_cache

# config.py lives at <repo>/backend/app/core/config.py → parents[3] = <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "Mela AI"
    APP_ENV: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Azure
    AZURE_SUBSCRIPTION_ID: str = ""
    AZURE_RESOURCE_GROUP: str = ""
    AZURE_LOCATION: str = "eastus"

    # Authentication (Entra ID / Azure AD)
    # These drive the user-login token validation in security.py.
    # AZURE_TENANT_ID / AZURE_CLIENT_ID are the primary names; the ENTRA_* aliases
    # are accepted for connector config — both resolve via effective_tenant_id.
    AZURE_TENANT_ID: str = ""
    AZURE_CLIENT_ID: str = ""
    AZURE_CLIENT_SECRET: str = ""
    ENTRA_REDIRECT_URI: str = "http://localhost:3005/auth/callback"

    # Dev-login credentials (development mode only — override via env, never hardcode)
    DEV_USERNAME: str = "dev"
    DEV_PASSWORD: str = "dev"
    # Set to False to disable the /auth/dev-login endpoint entirely.
    # Once real admin auth works, flip this off in all non-local envs.
    ENABLE_DEV_LOGIN: bool = False

    # Bootstrap admins — comma-separated Entra emails that are auto-elevated to
    # UserRole.ADMIN on first login. Checked server-side against the validated
    # JWT preferred_username claim. Audit-logged the first time elevation occurs.
    # Example: BOOTSTRAP_ADMIN_EMAILS=edgar.mcochieng@armely.com,alice@armely.com
    BOOTSTRAP_ADMIN_EMAILS: str = ""

    # Bootstrap admin OIDs — comma-separated Entra object IDs (oid claim).
    # Use when the user's email isn't reliably available in the access token.
    # Example: BOOTSTRAP_ADMIN_OIDS=42392e35-40e4-45b6-b079-cf8a2d1dd89e
    BOOTSTRAP_ADMIN_OIDS: str = ""

    @property
    def bootstrap_admin_email_list(self) -> list[str]:
        """Lower-cased list of bootstrap admin emails."""
        return [e.strip().lower() for e in self.BOOTSTRAP_ADMIN_EMAILS.split(",") if e.strip()]

    @property
    def bootstrap_admin_oid_list(self) -> list[str]:
        """Lower-cased list of bootstrap admin Entra OIDs."""
        return [o.strip().lower() for o in self.BOOTSTRAP_ADMIN_OIDS.split(",") if o.strip()]

    # Azure AI Foundry (unified endpoint shared by all LLM deployments)
    AI_FOUNDRY_ENDPOINT: str = ""
    AI_FOUNDRY_API_KEY: str = ""
    AI_FOUNDRY_API_VERSION: str = "2024-05-01-preview"

    # Azure OpenAI (may point to AI Foundry or a dedicated resource)
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-05-01-preview"
    AZURE_OPENAI_CHAT_DEPLOYMENT: str = "gpt-4.1"
    AZURE_OPENAI_FAST_DEPLOYMENT: str = "Kimi-K2.5"
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT: str = "text-embedding-3-small"
    AZURE_OPENAI_VISION_DEPLOYMENT: str = "gpt-4.1"

    # Named AI Foundry deployments
    DEPLOYMENT_KIMI_K25: str = "Kimi-K2.5"
    DEPLOYMENT_MISTRAL_LARGE_3: str = "Mistral-Large-3"
    DEPLOYMENT_GPT52_CHAT: str = "gpt-5.2-chat"
    DEPLOYMENT_GPT41: str = "gpt-4.1"
    # Upgraded from text-embedding-3-small (1536) → 3072 dims, better quality
    DEPLOYMENT_EMBEDDING: str = "text-embedding-3-large"
    DEPLOYMENT_GROK3_MINI: str = "grok-3-mini"
    DEPLOYMENT_LLAMA4_MAVERICK: str = "Llama-4-Maverick-17B-128E-Instruct-FP8"

    # GPT-4o (separate resource)
    GPT4O_ENDPOINT: str = ""
    GPT4O_API_KEY: str = ""
    GPT4O_DEPLOYMENT: str = "gpt-4o"

    # Google Gemini — free-tier model via Google AI Studio key
    GOOGLE_AI_API_KEY: str = ""
    GEMINI_ENABLED: bool = True
    GEMINI_MAX_TOKENS: int = 4096

    # Anthropic Claude — key stored in env only, never in code
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_ENABLED: bool = True
    # Rate limit: max requests per 60-second window per user
    ANTHROPIC_RPM_LIMIT: int = 20
    # Keep output tokens modest to control spend; raise if needed
    ANTHROPIC_MAX_TOKENS_SONNET: int = 2048
    ANTHROPIC_MAX_TOKENS_HAIKU: int = 1024
    # Daily question cap per user (0 = unlimited). Default unlimited so prod
    # deployments don't silently rate-limit Claude without explicit configuration.
    CLAUDE_DAILY_QUESTION_LIMIT: int = 0
    # Show usage warning when this many questions remain in the window
    CLAUDE_WARN_AT_REMAINING: int = 2

    @property
    def effective_openai_endpoint(self) -> str:
        """Returns the best-available OpenAI endpoint."""
        return self.AZURE_OPENAI_ENDPOINT or self.AI_FOUNDRY_ENDPOINT

    @property
    def effective_openai_api_key(self) -> str:
        """Returns the best-available OpenAI API key."""
        return self.AZURE_OPENAI_API_KEY or self.AI_FOUNDRY_API_KEY

    # Azure AI Search
    AZURE_SEARCH_ENDPOINT: str = ""
    AZURE_SEARCH_API_KEY: str = ""
    AZURE_SEARCH_ADMIN_KEY: str = ""   # admin key (write)
    AZURE_SEARCH_QUERY_KEY: str = ""   # query key (read-only)
    AZURE_SEARCH_INDEX_NAME: str = "fileshare-documents"
    AZURE_SEARCH_VECTOR_INDEX_NAME: str = "fileshare-vector-documents"
    AZURE_SEARCH_CACHE_INDEX_NAME: str = "mela-query-cache"
    # Phase 4: dedicated index for the orchestration brain's Knowledge
    # Base.  Blank → KnowledgeStore falls back to its SQL keyword search;
    # set to e.g. "mela-kb-entries" to enable hybrid vector search.
    AZURE_SEARCH_KB_INDEX: str = ""

    # ── Login / Auth app registration (NEW — user sign-in only) ──
    # Create a SEPARATE app registration in Entra for user authentication.
    # This is what MSAL on the frontend authenticates against, and what the
    # backend validates Bearer tokens against.
    # Platform type: Single-page application (SPA)
    # Redirect URIs: http://localhost:3005 and https://armely-ai-web.azurewebsites.net/
    # Expose an API scope: api://<ENTRA_AUTH_CLIENT_ID>/access_as_user
    # Delegated permissions: openid, profile, email, User.Read
    ENTRA_AUTH_CLIENT_ID: str = ""     # login-dedicated app registration client ID
    ENTRA_AUTH_CLIENT_SECRET: str = ""  # only needed if OBO flow is used server-side

    # ── Enterprise Connectors (Entra / Graph) ──────────────────
    # ENTRA_* are aliases that fall back to AZURE_* — these drive the
    # app-only (client-credentials) Graph token used by SharePoint/OneDrive connectors.
    # Keep separate from the login registration above.
    ENTRA_TENANT_ID: str = ""          # falls back to AZURE_TENANT_ID via property
    ENTRA_CLIENT_ID: str = ""          # falls back to AZURE_CLIENT_ID via property
    ENTRA_CLIENT_SECRET: str = ""      # falls back to AZURE_CLIENT_SECRET via property
    GRAPH_AUTHORITY: str = ""          # derived from effective_tenant_id if blank

    # Phase 5 (CR-2): On-Behalf-Of flow for LLM-callable Graph tools.
    # When ON, send_email / schedule_meeting / etc. acquire a delegated
    # token via msal.acquire_token_on_behalf_of so Microsoft 365 audit
    # logs attribute the action to the real user (not the service
    # principal). Requires the enterprise app to have delegated Graph
    # permissions granted with admin consent. Defaults to OFF for safe
    # rollout — flip per-environment once Entra config is in place.
    USE_OBO_FOR_GRAPH: bool = False

    # Phase 6 (M-5): Antivirus scan on file uploads.
    # ``AV_SCAN_BACKEND`` selects ``disabled`` (default), ``clamav``
    # (streams via clamd INSTREAM at ``CLAMAV_HOST:CLAMAV_PORT``), or
    # ``defender`` (trusts Microsoft Defender for Storage blob tag).
    # ``AV_SCAN_FAIL_CLOSED`` should be ON in production: unknown /
    # scan-pending verdicts then reject the upload.
    AV_SCAN_ENABLED: bool = False
    AV_SCAN_BACKEND: str = "disabled"
    AV_SCAN_FAIL_CLOSED: bool = False
    AV_SCAN_TIMEOUT_S: int = 30
    AV_SCAN_MAX_BYTES: int = 25 * 1024 * 1024
    CLAMAV_HOST: str = ""
    CLAMAV_PORT: int = 3310

    # ── Audit-gap remediation flags (Sprint 1–4) ─────────────────────────────
    # All default OFF / 0 so behaviour is unchanged until a tenant opts in.
    #
    # M-4: per-user daily upload quota in MB. 0 = unlimited.
    DAILY_UPLOAD_QUOTA_MB: int = 0
    # M-6: tighter per-route rate limit for /chat/completions (requests/min).
    # 0 = use global RATE_LIMIT_REQUESTS instead.
    CHAT_RATE_LIMIT_PER_MIN: int = 0
    # M-3: /admin/me bot-poll protection (requests/min). 60 ≈ once per second.
    ADMIN_ME_RATE_LIMIT_PER_MIN: int = 60
    # GDPR: soft-delete pattern. When ON, list/read queries filter out
    # rows where deleted_at IS NOT NULL; delete endpoints set deleted_at
    # instead of cascade-deleting.
    ENABLE_SOFT_DELETE: bool = False
    # GDPR: scheduled hard-delete of soft-deleted rows after N days. 0 = no sweep.
    RETENTION_DAYS_CONVERSATIONS: int = 0
    RETENTION_DAYS_DOCUMENTS: int = 0
    # GDPR: expose /user/export (DSAR) and /user/erase (RTBE) endpoints.
    ENABLE_GDPR_ENDPOINTS: bool = False
    # Role gates: when ON, tool_executor checks EnabledTool.allowed_roles
    # before dispatching each tool call. Returns permission_denied if the
    # caller's role isn't allowed for that tool.
    ENFORCE_TOOL_ROLE_GATES: bool = False
    # Sensitivity-label enforcement: when ON, query_pipeline drops chunks
    # whose sensitivity_level exceeds the caller's role ceiling.
    ENFORCE_SENSITIVITY_LABELS: bool = False
    # Infra: when ON, code_interpreter dispatches to the gVisor sidecar at
    # CODE_RUNNER_URL instead of forking a local subprocess.
    USE_GVISOR_RUNTIME: bool = False
    CODE_RUNNER_URL: str = ""
    CODE_RUNNER_API_KEY: str = ""

    # SharePoint sites (comma-separated URLs)
    SHAREPOINT_SITES: str = ""
    ONEDRIVE_ROOT: str = ""

    # Org website crawler
    ORG_WEBSITE_ALLOWLIST: str = ""    # comma-separated domains
    ORG_WEBSITE_CRAWL_DEPTH: int = 3

    # Public web search
    WEB_SEARCH_ENABLED: bool = False
    WEB_SEARCH_ALLOWLIST: str = ""

    # Connector feature flags
    CONNECTOR_SHAREPOINT_ENABLED: bool = True
    CONNECTOR_ONEDRIVE_ENABLED: bool = True
    CONNECTOR_EMAIL_ENABLED: bool = False
    CONNECTOR_PLANNER_ENABLED: bool = True
    CONNECTOR_ORG_WEBSITE_ENABLED: bool = True
    CONNECTOR_PUBLIC_WEB_ENABLED: bool = False

    # Sync schedule (cron)
    SYNC_DELTA_CRON: str = "0 */4 * * *"
    SYNC_FULL_CRON: str = "0 2 * * 0"

    # ── Orchestration Brain — registered worker apps ───────────────
    # Mela coordinates multiple independent worker apps via the
    # orchestration brain (app/orchestration/).  Each worker has its own
    # base URL and credential.  The orchestration layer reads these at
    # boot to seed the worker registry; if a URL is blank, that worker
    # is simply not registered (Mela degrades gracefully).
    TASK_RADAR_BASE_URL: str = ""
    TASK_RADAR_MCP_API_KEY: str = ""
    # Inbound API key the worker uses on /api/v1/ingest/* callbacks.
    # Stored on the manifest's auth_config["inbound_api_key"]; Phase 2
    # reads it from this env var.  In production source from Key Vault:
    #   TASK_RADAR_INBOUND_API_KEY=@Microsoft.KeyVault(SecretUri=...)
    TASK_RADAR_INBOUND_API_KEY: str = ""

    # Public base URL workers POST their callbacks to.  Stamped onto
    # WorkerManifest.report_back_url at startup; if blank, seed_workers
    # leaves report_back_url unset and async results just won't auto-route
    # back (workers can still POST manually if they know the URL).
    MELA_INGESTION_BASE_URL: str = ""

    # ── Meeting Assistant worker (Phase 4) ──────────────────────────
    # Second registered worker.  Same pattern as Task Radar — blank URL
    # skips registration, manifest is seeded with status="unconfigured".
    MEETING_ASSISTANT_BASE_URL: str = ""
    MEETING_ASSISTANT_MCP_API_KEY: str = ""
    # Production: source from Key Vault, e.g.:
    #   MEETING_ASSISTANT_INBOUND_API_KEY=@Microsoft.KeyVault(SecretUri=...)
    MEETING_ASSISTANT_INBOUND_API_KEY: str = ""

    # ── Knowledge Base expiry policy (Phase 4) ──────────────────────
    # Default TTL for KB entries.  Per-type overrides live in
    # ``app/orchestration/knowledge.py`` (KB_EXPIRY_DAYS_BY_TYPE).
    KB_DEFAULT_EXPIRY_DAYS: int = 30

    # ── Per-tenant worker access (Phase 5C) ─────────────────────────
    # When True (default), every tenant can invoke every registered
    # worker — the worker_tenant_access table is never consulted, and
    # tool synthesis / Router.route behave identically to Phase 4.
    # Set to False in deployments where each tenant must be explicitly
    # granted access to specific workers.  Granting / revoking is done
    # via /api/v1/orchestration/access (admin only).
    WORKER_ACCESS_DEFAULT_ALLOW: bool = True

    # ── Embed surface (Phase 6B) ────────────────────────────────────
    # Comma-separated list of origins permitted to frame Mela's
    # /embed routes.  Blank → SAMEORIGIN only.  Examples:
    #   MELA_EMBED_ALLOWED_ORIGINS=https://taskradar.armely.com,https://meet.armely.com
    MELA_EMBED_ALLOWED_ORIGINS: str = ""

    # ── Worker self-registration (Phase 6C) ─────────────────────────
    # Shared secret a worker presents on POST /api/v1/orchestration/register.
    # Blank → self-registration is disabled (endpoint returns 503).
    # Existing deployments are unaffected unless they opt in.
    MELA_WORKER_REGISTRATION_KEY: str = ""

    @property
    def embed_allowed_origin_list(self) -> list[str]:
        """Parsed comma-separated allowed-origin list."""
        return [
            o.strip() for o in self.MELA_EMBED_ALLOWED_ORIGINS.split(",")
            if o.strip()
        ]

    # ── Derived / unified properties ───────────────────────────

    @property
    def effective_tenant_id(self) -> str:
        """Single Entra tenant — AZURE_TENANT_ID takes precedence, falls back to ENTRA_TENANT_ID."""
        return self.AZURE_TENANT_ID or self.ENTRA_TENANT_ID

    @property
    def effective_client_id(self) -> str:
        """Data/connector app registration client ID (used by graph_client.py for app-only tokens)."""
        return self.AZURE_CLIENT_ID or self.ENTRA_CLIENT_ID

    @property
    def effective_client_secret(self) -> str:
        """Data/connector app registration secret."""
        return self.AZURE_CLIENT_SECRET or self.ENTRA_CLIENT_SECRET

    @property
    def auth_client_id(self) -> str:
        """Login-dedicated app registration client ID.

        This is what security.py validates user Bearer tokens against.
        Falls back to effective_client_id so existing single-registration
        deployments continue to work without setting ENTRA_AUTH_CLIENT_ID.
        """
        return self.ENTRA_AUTH_CLIENT_ID or self.effective_client_id

    @property
    def auth_client_secret(self) -> str:
        """Login app registration secret (only needed for OBO flow)."""
        return self.ENTRA_AUTH_CLIENT_SECRET or self.effective_client_secret

    @property
    def graph_authority(self) -> str:
        return self.GRAPH_AUTHORITY or f"https://login.microsoftonline.com/{self.effective_tenant_id}"

    @property
    def effective_search_admin_key(self) -> str:
        return self.AZURE_SEARCH_ADMIN_KEY or self.AZURE_SEARCH_API_KEY

    @property
    def effective_search_query_key(self) -> str:
        return self.AZURE_SEARCH_QUERY_KEY or self.AZURE_SEARCH_API_KEY

    @property
    def sharepoint_site_list(self) -> list:
        return [s.strip() for s in self.SHAREPOINT_SITES.split(",") if s.strip()]

    @property
    def org_website_domains(self) -> list:
        return [d.strip() for d in self.ORG_WEBSITE_ALLOWLIST.split(",") if d.strip()]

    # Azure SQL
    AZURE_SQL_SERVER: str = ""
    AZURE_SQL_DATABASE: str = ""
    AZURE_SQL_USERNAME: str = ""
    AZURE_SQL_PASSWORD: str = ""
    DATABASE_URL: Optional[str] = None

    # Azure Storage
    AZURE_STORAGE_ACCOUNT_NAME: str = ""
    AZURE_STORAGE_ACCOUNT_KEY: str = ""
    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_STORAGE_CONTAINER_DOCUMENTS: str = "armelymela"
    AZURE_STORAGE_CONTAINER_UPLOADS: str = "armelymela"
    AZURE_STORAGE_CONTAINER_AGENT_MEMORY: str = "armelymela"

    # Azure Speech
    AZURE_SPEECH_KEY: str = ""
    AZURE_SPEECH_REGION: str = "eastus"
    AZURE_SPEECH_ENDPOINT: str = ""
    AZURE_SPEECH_LANGUAGE: str = "en-US"

    # Azure DALL-E (Image Generation) — legacy fallback
    AZURE_DALLE_ENDPOINT: str = ""
    AZURE_DALLE_API_KEY: str = ""
    AZURE_DALLE_API_VERSION: str = "2024-02-01"
    AZURE_DALLE_DEPLOYMENT: str = "dall-e-3"

    # FLUX image generation (primary provider)
    # Deployed on Azure Cognitive Services via AI Foundry
    FLUX_ENDPOINT: str = ""
    FLUX_API_KEY: str = ""
    FLUX_DEPLOYMENT: str = "FLUX.1-Kontext-pro"
    FLUX_API_VERSION: str = "2024-05-01-preview"
    # Comma-separated priority order: first configured provider wins
    IMAGE_PROVIDER_ORDER: str = "flux,dalle"

    # Azure Document Intelligence (Form Recognizer)
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT: str = ""
    AZURE_DOCUMENT_INTELLIGENCE_KEY: str = ""

    # Azure Translator
    AZURE_TRANSLATOR_KEY: str = ""
    AZURE_TRANSLATOR_ENDPOINT: str = "https://api.cognitive.microsofttranslator.com/"
    AZURE_TRANSLATOR_DOCUMENT_ENDPOINT: str = ""
    AZURE_TRANSLATOR_REGION: str = "eastus"

    # Azure Cosmos DB
    AZURE_COSMOS_ENDPOINT: str = ""
    AZURE_COSMOS_KEY: str = ""
    AZURE_COSMOS_DATABASE: str = "mela-ai"
    AZURE_COSMOS_CONTAINER: str = "conversations"

    # Azure Key Vault
    AZURE_KEY_VAULT_NAME: str = ""
    AZURE_KEY_VAULT_URL: str = ""

    # Application Insights
    APPLICATIONINSIGHTS_CONNECTION_STRING: str = ""

    # Microsoft Graph
    GRAPH_API_ENDPOINT: str = "https://graph.microsoft.com/v1.0"
    GRAPH_SCOPES: str = (
        "User.Read,Mail.Read,Mail.Send,Calendars.ReadWrite,"
        "Tasks.ReadWrite,Group.Read.All"
    )
    # Fallback sender UPN for app-only email (e.g. admin@contoso.com). Set via env.
    GRAPH_SENDER_EMAIL: str = ""
    # Default Planner plan ID for task creation when user doesn't specify one.
    # Find via: GET /groups/{groupId}/planner/plans in Graph Explorer.
    GRAPH_DEFAULT_PLANNER_PLAN_ID: str = ""

    # API Settings
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PREFIX: str = "/api/v1"
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

    @field_validator('CORS_ORIGINS', mode='before')
    @classmethod
    def parse_cors_origins(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith('['):
                import json
                return json.loads(v)
            return [x.strip() for x in v.split(',') if x.strip()]
        return v

    @field_validator('ALERT_RECIPIENTS', 'ALERT_CHANNELS', mode='before')
    @classmethod
    def _parse_string_list(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith('['):
                import json
                return json.loads(v)
            return [x.strip() for x in v.replace(';', ',').split(',') if x.strip()]
        return v

    # JWT — must be set via environment; no default in production
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── Redis (optional; enables shared state across replicas) ──
    # When REDIS_URL is empty, app.core.redis_client returns None and every
    # call site falls back to its in-process behaviour (no functional
    # regression).  Set REDIS_URL=redis://... or rediss://... in prod.
    REDIS_URL: str = ""
    REDIS_KEY_PREFIX: str = "mela:"
    # Maximum number of connections in the async connection pool.
    REDIS_MAX_CONNECTIONS: int = 50
    # Socket-level timeout in seconds for individual commands.
    REDIS_SOCKET_TIMEOUT: float = 3.0
    # Timeout in seconds when establishing a new connection.
    REDIS_CONNECT_TIMEOUT: float = 3.0

    # RAG Settings
    RAG_CHUNK_SIZE: int = 1000
    RAG_CHUNK_OVERLAP: int = 200
    RAG_TOP_K: int = 5
    RAG_SIMILARITY_THRESHOLD: float = 0.7

    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60
    DEFAULT_DAILY_TOKEN_LIMIT: int = 100000
    ADMIN_DAILY_TOKEN_LIMIT: int = 500000

    # ── Ops Alerting (ACS Email + Teams Adaptive Card + AI triage) ──────────
    # Recipient list — JSON array or comma-separated. Always includes
    # edgar.mcochieng@armely.com (enforced in alert_service).
    ALERT_RECIPIENTS: List[str] = ["edgar.mcochieng@armely.com"]
    # Channels to attempt. Subset of {"email", "teams"}.
    ALERT_CHANNELS: List[str] = ["email", "teams"]
    # Azure Communication Services
    ACS_CONNECTION_STRING: str = ""
    ACS_SENDER_ADDRESS: str = "DoNotReply@armely.com"
    # Microsoft Teams incoming webhook URL
    TEAMS_WEBHOOK_URL: str = ""
    # Suppression / retry
    ALERT_COOLDOWN_SECONDS: int = 300
    ALERT_MAX_RETRIES: int = 3
    ALERT_RETRY_BACKOFF_BASE: float = 2.0
    # AI triage gating
    ALERT_CONFIDENCE_THRESHOLD: float = 0.6

    # Feature Flags
    ENABLE_VOICE: bool = True
    ENABLE_FILE_UPLOAD: bool = True
    ENABLE_AGENTS: bool = True
    ENABLE_SHAREPOINT_SYNC: bool = True
    ENABLE_TRANSLATION: bool = True
    ENABLE_IMAGE_GENERATION: bool = True
    ENABLE_DOCUMENT_INTELLIGENCE: bool = True
    ENABLE_PRIVATE_CHAT: bool = True

    # SharePoint
    SHAREPOINT_SITE_ID: str = ""
    SHAREPOINT_DRIVE_ID: str = ""

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"mssql+aioodbc://{self.AZURE_SQL_USERNAME}:{self.AZURE_SQL_PASSWORD}"
            f"@{self.AZURE_SQL_SERVER}/{self.AZURE_SQL_DATABASE}"
            f"?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
        )

    # Log Analytics
    AZURE_LOG_ANALYTICS_WORKSPACE_ID: str = ""

    # RAG
    ENABLE_RAG: bool = True

    class Config:
        # Load precedence (first match wins):
        #   env/.env.local  → local development (your machine, SQLite)
        #   env/.env.dev    → Azure dev deployment (fill in and keep gitignored)
        #   .env            → fallback for simple setups / CI
        # In Azure App Service, environment variables are injected directly by the
        # platform — no .env file is needed or read.
        # pydantic-settings v2: LATER files have HIGHER priority.
        # Load order: .env (base) → env/.env.dev (Azure dev) → backend/.env → env/.env.local (local wins)
        # Paths are absolute so uvicorn can be launched from any working directory.
        env_file = [
            str(_REPO_ROOT / ".env"),
            str(_REPO_ROOT / "env" / ".env.dev"),
            str(_REPO_ROOT / "backend" / ".env"),
            str(_REPO_ROOT / "env" / ".env.local"),
        ]
        case_sensitive = True
        extra = "ignore"


def _normalize_list_env_vars() -> None:
    """Convert CSV / bare-string values for known List[str] env vars to JSON
    arrays so pydantic-settings v2 can decode them. Idempotent.
    Affected: ALERT_RECIPIENTS, ALERT_CHANNELS, CORS_ORIGINS.
    """
    import json as _json
    for name in ("ALERT_RECIPIENTS", "ALERT_CHANNELS", "CORS_ORIGINS"):
        raw = os.environ.get(name)
        if not raw:
            continue
        s = raw.strip()
        if s.startswith("["):
            continue  # already JSON
        parts = [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]
        os.environ[name] = _json.dumps(parts)


_normalize_list_env_vars()


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
