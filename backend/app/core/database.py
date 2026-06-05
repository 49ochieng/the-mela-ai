"""
Mela AI - Database Configuration
"""

import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
from typing import AsyncGenerator

from app.core.config import settings

logger = logging.getLogger(__name__)

# Naming convention for constraints
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    """Base class for all database models."""
    metadata = metadata


# Use SQLite for local development when DATABASE_URL is not set,
# is empty, or explicitly points to SQLite
_raw_db_url = settings.DATABASE_URL or ""
USE_SQLITE = (
    settings.APP_ENV == "development" and not _raw_db_url
) or "sqlite" in _raw_db_url.lower()

if USE_SQLITE:
    # Use SQLite with aiosqlite
    SQLITE_URL = _raw_db_url if _raw_db_url else "sqlite+aiosqlite:///./mela_dev.db"
    # NullPool: each checkout gets its own connection so background ingestion
    # tasks don't block chat requests.  WAL mode (set per-connect below) lets
    # SQLite handle one concurrent writer + many concurrent readers safely.
    from sqlalchemy.pool import NullPool
    engine = create_async_engine(
        SQLITE_URL,
        echo=settings.DEBUG,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    # Set WAL mode + 30s busy timeout on every new SQLite connection so that
    # background sync tasks and chat requests don't deadlock each other.
    # synchronous=NORMAL gives a good durability/speed tradeoff.
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _conn_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-32000")  # 32 MB page cache
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

    logger.info(f"Using SQLite database: {SQLITE_URL}")
else:
    # Create async engine for production (SQL Server)
    # MARS_Connection=yes prevents "Connection is busy with results for another
    # command" errors when pool_pre_ping reuses a connection that still has
    # un-consumed result sets (e.g. from a concurrent background ingestion task).
    _db_url = settings.database_url
    if "mssql" in _db_url and "MARS_Connection" not in _db_url:
        _db_url += "&MARS_Connection=yes"
    engine = create_async_engine(
        _db_url,
        echo=settings.DEBUG,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,       # fail fast if all connections busy
        pool_recycle=1800,     # recycle stale connections after 30 min
    )

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# Flag to track if db is available
db_available = True


async def init_db():
    """Initialize database tables (for SQLite in dev mode)."""
    global db_available
    if USE_SQLITE:
        try:
            from sqlalchemy import text
            # Enable WAL mode first to allow concurrent reads during writes
            async with engine.connect() as conn:
                await conn.execute(text("PRAGMA journal_mode=WAL"))
                await conn.execute(text("PRAGMA busy_timeout=30000"))
                await conn.commit()
            # Step 1: create all tables that don't exist yet (uses a single tx)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all, checkfirst=True)

            # Step 2: column migrations — EACH in its OWN transaction/connection.
            # Rationale: SQLite puts a connection into a "needs rollback" state
            # after the first error inside a transaction.  If all migrations share
            # one conn, the first "duplicate column" error contaminates every
            # subsequent statement, silently skipping later (critical) migrations.
            migrations = [
                    # ── Legacy conversation columns ─────────────────────────
                    "ALTER TABLE conversations ADD COLUMN is_private BOOLEAN NOT NULL DEFAULT 0",
                    "ALTER TABLE conversations ADD COLUMN private_expires_at DATETIME",
                    "ALTER TABLE conversations ADD COLUMN project_id VARCHAR(36)",
                    # ── Profile / context separation ────────────────────────
                    "ALTER TABLE conversations ADD COLUMN context_type VARCHAR(20) NOT NULL DEFAULT 'personal'",
                    "ALTER TABLE conversations ADD COLUMN workspace_id VARCHAR(36)",
                    "ALTER TABLE projects ADD COLUMN context_type VARCHAR(20) NOT NULL DEFAULT 'personal'",
                    "ALTER TABLE projects ADD COLUMN workspace_id VARCHAR(36)",
                    # ── Audit log enhancements ──────────────────────────────
                    "ALTER TABLE audit_logs ADD COLUMN event_type VARCHAR(100)",
                    "ALTER TABLE audit_logs ADD COLUMN workspace_id VARCHAR(36)",
                    # ── project_files table (legacy pre-existing DBs) ───────
                    (
                        "CREATE TABLE IF NOT EXISTS project_files ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE, "
                        "filename VARCHAR(500) NOT NULL, "
                        "file_type VARCHAR(100) NOT NULL, "
                        "file_size INTEGER NOT NULL, "
                        "content_text TEXT, "
                        "uploaded_by VARCHAR(36) NOT NULL REFERENCES users(id), "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_project_files_project_id ON project_files(project_id)",
                    # ── Collaboration: project_members ──────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS project_members ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE, "
                        "user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                        "role VARCHAR(10) NOT NULL DEFAULT 'viewer', "
                        "added_by VARCHAR(36) NOT NULL REFERENCES users(id), "
                        "added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "UNIQUE(project_id, user_id))"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_project_members_project_id ON project_members(project_id)",
                    "CREATE INDEX IF NOT EXISTS ix_project_members_user_id ON project_members(user_id)",
                    # ── Collaboration: chat_members ─────────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS chat_members ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "conversation_id VARCHAR(36) NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, "
                        "user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                        "role VARCHAR(10) NOT NULL DEFAULT 'viewer', "
                        "added_by VARCHAR(36) NOT NULL REFERENCES users(id), "
                        "added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "UNIQUE(conversation_id, user_id))"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_chat_members_conversation_id ON chat_members(conversation_id)",
                    "CREATE INDEX IF NOT EXISTS ix_chat_members_user_id ON chat_members(user_id)",
                    # ── Invites ─────────────────────────────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS invites ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "resource_type VARCHAR(20) NOT NULL, "
                        "resource_id VARCHAR(36) NOT NULL, "
                        "inviter_user_id VARCHAR(36) NOT NULL REFERENCES users(id), "
                        "invitee_email VARCHAR(255) NOT NULL, "
                        "invitee_user_id VARCHAR(36) REFERENCES users(id), "
                        "role VARCHAR(10) NOT NULL DEFAULT 'viewer', "
                        "status VARCHAR(10) NOT NULL DEFAULT 'pending', "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "expires_at DATETIME)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_invites_resource ON invites(resource_type, resource_id)",
                    "CREATE INDEX IF NOT EXISTS ix_invites_invitee_email ON invites(invitee_email)",
                    # ── Share links ─────────────────────────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS share_links ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "resource_type VARCHAR(20) NOT NULL, "
                        "resource_id VARCHAR(36) NOT NULL, "
                        "created_by VARCHAR(36) NOT NULL REFERENCES users(id), "
                        "permission_scope VARCHAR(10) NOT NULL DEFAULT 'viewer', "
                        "expires_at DATETIME, "
                        "revoked_at DATETIME, "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_share_links_resource ON share_links(resource_type, resource_id)",
                    # ── Profile isolation: profile_mode + tenant_id ─────────
                    # conversations
                    "ALTER TABLE conversations ADD COLUMN profile_mode VARCHAR(20) NOT NULL DEFAULT 'personal'",
                    "ALTER TABLE conversations ADD COLUMN tenant_id VARCHAR(36)",
                    # Back-fill profile_mode from context_type ('org' → 'work')
                    "UPDATE conversations SET profile_mode = CASE WHEN context_type = 'org' THEN 'work' ELSE 'personal' END WHERE profile_mode = 'personal' AND context_type IS NOT NULL",
                    "CREATE INDEX IF NOT EXISTS ix_conversations_profile ON conversations(user_id, profile_mode, tenant_id, updated_at)",
                    # messages
                    "ALTER TABLE messages ADD COLUMN profile_mode VARCHAR(20) NOT NULL DEFAULT 'personal'",
                    "ALTER TABLE messages ADD COLUMN tenant_id VARCHAR(36)",
                    # Back-fill messages from their parent conversation
                    "UPDATE messages SET profile_mode = (SELECT profile_mode FROM conversations WHERE conversations.id = messages.conversation_id) WHERE 1=1",
                    "UPDATE messages SET tenant_id = (SELECT tenant_id FROM conversations WHERE conversations.id = messages.conversation_id) WHERE 1=1",
                    # projects
                    "ALTER TABLE projects ADD COLUMN profile_mode VARCHAR(20) NOT NULL DEFAULT 'personal'",
                    "ALTER TABLE projects ADD COLUMN tenant_id VARCHAR(36)",
                    "UPDATE projects SET profile_mode = CASE WHEN context_type = 'org' THEN 'work' ELSE 'personal' END WHERE profile_mode = 'personal' AND context_type IS NOT NULL",
                    "CREATE INDEX IF NOT EXISTS ix_projects_profile ON projects(user_id, profile_mode, tenant_id, updated_at)",
                    # ── Generated file audit log ─────────────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS generated_file_logs ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "message_id VARCHAR(36) NOT NULL REFERENCES messages(id) ON DELETE CASCADE, "
                        "conversation_id VARCHAR(36) NOT NULL, "
                        "user_id VARCHAR(36) NOT NULL, "
                        "filename VARCHAR(500) NOT NULL, "
                        "mime_type VARCHAR(200) NOT NULL, "
                        "file_size INTEGER NOT NULL, "
                        "source_inputs JSON, "
                        "output_type VARCHAR(50) NOT NULL DEFAULT 'other', "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_gen_file_logs_message_id ON generated_file_logs(message_id)",
                    "CREATE INDEX IF NOT EXISTS ix_gen_file_logs_user_id ON generated_file_logs(user_id)",
                    "CREATE INDEX IF NOT EXISTS ix_gen_file_logs_created_at ON generated_file_logs(created_at)",
                    # ── Generated file data storage ──────────────────────────
                    "ALTER TABLE generated_file_logs ADD COLUMN file_data TEXT",
                    # ── Claude daily usage tracking ──────────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS claude_usage ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "user_id VARCHAR(36) NOT NULL, "
                        "window_date VARCHAR(10) NOT NULL, "
                        "question_count INTEGER NOT NULL DEFAULT 0, "
                        "token_count INTEGER NOT NULL DEFAULT 0, "
                        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "UNIQUE(user_id, window_date))"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_claude_usage_user_date ON claude_usage(user_id, window_date)",
                    # ── Model ranking preferences ────────────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS model_rankings ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "model_id VARCHAR(100) NOT NULL UNIQUE, "
                        "display_name VARCHAR(100) NOT NULL, "
                        "provider VARCHAR(50) NOT NULL DEFAULT 'azure_openai', "
                        "rank INTEGER NOT NULL DEFAULT 0, "
                        "is_enabled INTEGER NOT NULL DEFAULT 1, "
                        "is_default INTEGER NOT NULL DEFAULT 0, "
                        "max_tokens INTEGER, "
                        "notes TEXT, "
                        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "updated_by VARCHAR(36))"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_model_rankings_rank ON model_rankings(rank)",
                    # ── Model ranking: cost_multiplier column (idempotent) ───
                    # Mirrors alembic 003_model_cost_multiplier — dev SQLite never
                    # runs alembic, so the column must be added here too.  The
                    # try/except in the migration loop makes this safe to re-run.
                    "ALTER TABLE model_rankings ADD COLUMN cost_multiplier REAL NOT NULL DEFAULT 1.0",
                    "CREATE INDEX IF NOT EXISTS ix_model_rankings_provider ON model_rankings(provider)",
                    # ── System instructions ──────────────────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS system_instructions ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "name VARCHAR(200) NOT NULL, "
                        "content TEXT NOT NULL, "
                        "scope VARCHAR(20) NOT NULL DEFAULT 'user', "
                        "priority INTEGER NOT NULL DEFAULT 100, "
                        "is_enabled INTEGER NOT NULL DEFAULT 1, "
                        "created_by VARCHAR(36) NOT NULL, "
                        "user_id VARCHAR(36), "
                        "tenant_id VARCHAR(36), "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_instructions_scope ON system_instructions(scope, priority)",
                    "CREATE INDEX IF NOT EXISTS ix_instructions_user ON system_instructions(user_id)",
                    # ── Skills ───────────────────────────────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS skills ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "name VARCHAR(200) NOT NULL, "
                        "description TEXT, "
                        "category VARCHAR(50) NOT NULL DEFAULT 'general', "
                        "trigger_keywords TEXT, "
                        "instruction_block TEXT NOT NULL, "
                        "model_preference VARCHAR(100), "
                        "is_enabled INTEGER NOT NULL DEFAULT 1, "
                        "is_builtin INTEGER NOT NULL DEFAULT 0, "
                        "rank INTEGER NOT NULL DEFAULT 100, "
                        "visibility VARCHAR(20) NOT NULL DEFAULT 'global', "
                        "created_by VARCHAR(36) NOT NULL, "
                        "user_id VARCHAR(36), "
                        "tenant_id VARCHAR(36), "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_skills_category ON skills(category)",
                    "CREATE INDEX IF NOT EXISTS ix_skills_rank ON skills(rank)",
                    # ── Data-repair: fix conversations wrongly stored as personal ─
                    # Root cause: early versions silently downgraded work→personal
                    # when X-Tenant-Id was missing.  Any conversation whose
                    # context_type was 'org' (set by the frontend) but whose
                    # profile_mode was left as 'personal' (due to the fallback)
                    # is corrected here.  tenant_id is set to the dev sentinel so
                    # these records surface in the Work namespace immediately.
                    (
                        "UPDATE conversations "
                        "SET profile_mode='work', tenant_id='dev-tenant-001' "
                        "WHERE context_type='org' AND profile_mode='personal'"
                    ),
                    (
                        "UPDATE projects "
                        "SET profile_mode='work', tenant_id='dev-tenant-001' "
                        "WHERE context_type='org' AND profile_mode='personal'"
                    ),
                    # Back-fill messages from their repaired parent conversations
                    (
                        "UPDATE messages "
                        "SET profile_mode='work', tenant_id='dev-tenant-001' "
                        "WHERE conversation_id IN ("
                        "  SELECT id FROM conversations "
                        "  WHERE profile_mode='work' AND tenant_id='dev-tenant-001'"
                        ") AND profile_mode='personal'"
                    ),
                    # ── CRITICAL FIX: backfill migration at line ~172 set      ──
                    # profile_mode='work' for org conversations but never set    ──
                    # tenant_id.  The list query uses WHERE tenant_id=sentinel,  ──
                    # so any work conversation with tenant_id=NULL is invisible.  ──
                    # This migration fills the gap for all three tables.         ──
                    "UPDATE conversations SET tenant_id='dev-tenant-001' WHERE profile_mode='work' AND tenant_id IS NULL",
                    "UPDATE projects SET tenant_id='dev-tenant-001' WHERE profile_mode='work' AND tenant_id IS NULL",
                    "UPDATE messages SET tenant_id='dev-tenant-001' WHERE profile_mode='work' AND tenant_id IS NULL",
                    # ── Normalize context_type: backend was storing 'work' but  ──
                    # the frontend sidebar filter expects 'org' (legacy alias).   ──
                    # Normalize all 'work' context_type values to 'org' so the   ──
                    # sidebar shows them correctly without backend/frontend        ──
                    # coupling on this legacy field.                              ──
                    "UPDATE conversations SET context_type='org' WHERE profile_mode='work' AND context_type='work'",
                    "UPDATE projects SET context_type='org' WHERE profile_mode='work' AND context_type='work'",
                    # ── Bootstrap admin tracking ─────────────────────────────
                    "ALTER TABLE users ADD COLUMN bootstrap_elevated_at DATETIME",
                    # ── Manual admin promotion notification ───────────────────
                    "ALTER TABLE users ADD COLUMN promoted_at DATETIME",
                    "ALTER TABLE users ADD COLUMN promotion_banner_shown INTEGER NOT NULL DEFAULT 0",
                    # ── Workflow automation ───────────────────────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS workflows ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "name VARCHAR(200) NOT NULL, "
                        "description TEXT, "
                        "trigger_type VARCHAR(50) NOT NULL DEFAULT 'manual', "
                        "trigger_config JSON, "
                        "actions JSON, "
                        "status VARCHAR(20) NOT NULL DEFAULT 'draft', "
                        "visibility VARCHAR(20) NOT NULL DEFAULT 'user', "
                        "created_by VARCHAR(36) NOT NULL REFERENCES users(id), "
                        "user_id VARCHAR(36), "
                        "tenant_id VARCHAR(36), "
                        "run_count INTEGER NOT NULL DEFAULT 0, "
                        "last_run_at DATETIME, "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_workflows_created_by ON workflows(created_by)",
                    "CREATE INDEX IF NOT EXISTS ix_workflows_status ON workflows(status)",
                    (
                        "CREATE TABLE IF NOT EXISTS workflow_runs ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "workflow_id VARCHAR(36) NOT NULL REFERENCES workflows(id) ON DELETE CASCADE, "
                        "triggered_by VARCHAR(36), "
                        "trigger_type VARCHAR(50) NOT NULL DEFAULT 'manual', "
                        "status VARCHAR(20) NOT NULL DEFAULT 'pending', "
                        "input_data JSON, "
                        "output_data JSON, "
                        "error_message TEXT, "
                        "steps_completed INTEGER NOT NULL DEFAULT 0, "
                        "steps_total INTEGER NOT NULL DEFAULT 0, "
                        "started_at DATETIME, "
                        "finished_at DATETIME, "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_workflow_runs_workflow_id ON workflow_runs(workflow_id)",
                    "CREATE INDEX IF NOT EXISTS ix_workflow_runs_created_at ON workflow_runs(created_at)",
                    # Phase 5B: trace correlation column on workflow_runs.
                    "ALTER TABLE workflow_runs ADD COLUMN orchestration_trace_ids JSON",
                    # ── Enterprise control-plane: error log ──────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS error_logs ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "user_id VARCHAR(36), "
                        "user_email VARCHAR(255), "
                        "tenant_id VARCHAR(36), "
                        "method VARCHAR(10) NOT NULL DEFAULT '', "
                        "route VARCHAR(500) NOT NULL DEFAULT '', "
                        "status_code INTEGER NOT NULL DEFAULT 500, "
                        "error_type VARCHAR(200) NOT NULL DEFAULT 'Exception', "
                        "message TEXT NOT NULL DEFAULT '', "
                        "stack_trace TEXT, "
                        "severity VARCHAR(20) NOT NULL DEFAULT 'error', "
                        "request_id VARCHAR(100), "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_error_logs_user_id ON error_logs(user_id)",
                    "CREATE INDEX IF NOT EXISTS ix_error_logs_tenant_id ON error_logs(tenant_id)",
                    "CREATE INDEX IF NOT EXISTS ix_error_logs_severity ON error_logs(severity)",
                    "CREATE INDEX IF NOT EXISTS ix_error_logs_created_at ON error_logs(created_at)",
                    # ── Enterprise control-plane: model quota policies ────────
                    (
                        "CREATE TABLE IF NOT EXISTS model_quota_policies ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "model_id VARCHAR(100) NOT NULL UNIQUE, "
                        "display_name VARCHAR(200), "
                        "provider VARCHAR(100) NOT NULL DEFAULT 'azure_openai', "
                        "is_enabled INTEGER NOT NULL DEFAULT 1, "
                        "cost_rate_per_1k_tokens REAL NOT NULL DEFAULT 0.002, "
                        "daily_token_limit INTEGER, "
                        "daily_request_limit INTEGER, "
                        "updated_by VARCHAR(36), "
                        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_model_quota_model_id ON model_quota_policies(model_id)",
                    # hr_workflow_runs — onboarding/offboarding full run history
                    """
                    CREATE TABLE IF NOT EXISTS hr_workflow_runs (
                        id VARCHAR(36) PRIMARY KEY,
                        workflow_type VARCHAR(20) NOT NULL,
                        actor_user_id VARCHAR(36) NOT NULL,
                        actor_email VARCHAR(255) NOT NULL,
                        target_email VARCHAR(255) NOT NULL,
                        target_upn VARCHAR(255),
                        target_entra_id VARCHAR(36),
                        target_display_name VARCHAR(255),
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        step_results_json TEXT NOT NULL DEFAULT '[]',
                        status VARCHAR(30) NOT NULL DEFAULT 'running',
                        error_summary TEXT,
                        approval_reference VARCHAR(255),
                        audit_log_id VARCHAR(36),
                        started_at DATETIME NOT NULL,
                        completed_at DATETIME
                    )
                    """,
                    "CREATE INDEX IF NOT EXISTS ix_hr_workflow_runs_type ON hr_workflow_runs(workflow_type)",
                    "CREATE INDEX IF NOT EXISTS ix_hr_workflow_runs_actor ON hr_workflow_runs(actor_user_id)",
                    "CREATE INDEX IF NOT EXISTS ix_hr_workflow_runs_target ON hr_workflow_runs(target_email)",
                    "CREATE INDEX IF NOT EXISTS ix_hr_workflow_runs_started ON hr_workflow_runs(started_at)",
                    # ── Orchestration Brain: worker registry ─────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS worker_registry ("
                        "id VARCHAR(64) NOT NULL PRIMARY KEY, "
                        "display_name VARCHAR(200) NOT NULL, "
                        "version VARCHAR(40) NOT NULL, "
                        "protocol VARCHAR(20) NOT NULL, "
                        "base_url VARCHAR(500) NOT NULL, "
                        "health_check_url VARCHAR(500) NOT NULL, "
                        "status VARCHAR(20) NOT NULL DEFAULT 'unknown', "
                        "manifest JSON NOT NULL, "
                        "last_health_check DATETIME, "
                        "registered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_worker_registry_status ON worker_registry(status)",
                    # ── Orchestration Brain: trace + task + event tables ─────
                    (
                        "CREATE TABLE IF NOT EXISTS orchestration_traces ("
                        "trace_id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "goal_id VARCHAR(36) NOT NULL, "
                        "user_id VARCHAR(36) NOT NULL, "
                        "tenant_id VARCHAR(36), "
                        "profile_mode VARCHAR(20) NOT NULL DEFAULT 'personal', "
                        "status VARCHAR(20) NOT NULL DEFAULT 'pending', "
                        "plan_json JSON NOT NULL, "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "completed_at DATETIME)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_orch_traces_user ON orchestration_traces(user_id, created_at)",
                    "CREATE INDEX IF NOT EXISTS ix_orch_traces_status ON orchestration_traces(status)",
                    "CREATE INDEX IF NOT EXISTS ix_orch_traces_tenant ON orchestration_traces(tenant_id, created_at)",
                    (
                        "CREATE TABLE IF NOT EXISTS orchestration_tasks ("
                        "task_id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "trace_id VARCHAR(36) NOT NULL REFERENCES orchestration_traces(trace_id) ON DELETE CASCADE, "
                        "worker_id VARCHAR(64) NOT NULL, "
                        "capability VARCHAR(128) NOT NULL, "
                        "execution_mode VARCHAR(10) NOT NULL DEFAULT 'sync', "
                        "status VARCHAR(20) NOT NULL DEFAULT 'pending', "
                        "params_json JSON NOT NULL, "
                        "summary TEXT, "
                        "data_pointer VARCHAR(500), "
                        "error_code VARCHAR(64), "
                        "error_message TEXT, "
                        "latency_ms INTEGER NOT NULL DEFAULT 0, "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "completed_at DATETIME)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_orch_tasks_trace ON orchestration_tasks(trace_id)",
                    "CREATE INDEX IF NOT EXISTS ix_orch_tasks_worker_capability ON orchestration_tasks(worker_id, capability)",
                    "CREATE INDEX IF NOT EXISTS ix_orch_tasks_status ON orchestration_tasks(status)",
                    (
                        "CREATE TABLE IF NOT EXISTS worker_events ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "worker_id VARCHAR(64) NOT NULL, "
                        "event_type VARCHAR(128) NOT NULL, "
                        "payload_json JSON NOT NULL, "
                        "user_id VARCHAR(36), "
                        "tenant_id VARCHAR(36), "
                        "received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "processed_at DATETIME)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_worker_events_worker ON worker_events(worker_id, received_at)",
                    "CREATE INDEX IF NOT EXISTS ix_worker_events_type ON worker_events(event_type)",
                    "CREATE INDEX IF NOT EXISTS ix_worker_events_user ON worker_events(user_id, received_at)",
                    # ── Orchestration Brain: Knowledge Base ──────────────────
                    (
                        "CREATE TABLE IF NOT EXISTS knowledge_entries ("
                        "entry_id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "tenant_id VARCHAR(36), "
                        "user_id VARCHAR(36) NOT NULL, "
                        "profile_mode VARCHAR(20) NOT NULL DEFAULT 'personal', "
                        "source_worker_id VARCHAR(64), "
                        "trace_id VARCHAR(36) REFERENCES orchestration_traces(trace_id) ON DELETE SET NULL, "
                        "entry_type VARCHAR(40) NOT NULL, "
                        "title VARCHAR(500) NOT NULL, "
                        "summary VARCHAR(500) NOT NULL, "
                        "data_pointer VARCHAR(500), "
                        "tags JSON, "
                        "embedding_vector TEXT, "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "expires_at DATETIME)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_kb_owner ON knowledge_entries(tenant_id, user_id, created_at)",
                    "CREATE INDEX IF NOT EXISTS ix_kb_user ON knowledge_entries(user_id, created_at)",
                    "CREATE INDEX IF NOT EXISTS ix_kb_type ON knowledge_entries(entry_type)",
                    "CREATE INDEX IF NOT EXISTS ix_kb_trace ON knowledge_entries(trace_id)",
                    "CREATE INDEX IF NOT EXISTS ix_kb_worker ON knowledge_entries(source_worker_id, created_at)",
                    # Phase 5C: per-tenant worker access grants (soft-delete).
                    (
                        "CREATE TABLE IF NOT EXISTS worker_tenant_access ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "worker_id VARCHAR(64) NOT NULL REFERENCES worker_registry(id) ON DELETE CASCADE, "
                        "tenant_id VARCHAR(36) NOT NULL, "
                        "granted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "granted_by VARCHAR(36) NOT NULL, "
                        "revoked_at DATETIME)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_worker_tenant_access_lookup ON worker_tenant_access(worker_id, tenant_id, revoked_at)",
                    "CREATE INDEX IF NOT EXISTS ix_worker_tenant_access_tenant ON worker_tenant_access(tenant_id)",
                    # Phase 6A: external MCP clients (api keys bcrypt-hashed).
                    (
                        "CREATE TABLE IF NOT EXISTS mcp_clients ("
                        "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                        "client_name VARCHAR(200) NOT NULL, "
                        "api_key_hash VARCHAR(200) NOT NULL, "
                        "tenant_id VARCHAR(36), "
                        "scopes JSON NOT NULL, "
                        "created_by VARCHAR(36), "
                        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                        "revoked_at DATETIME, "
                        "last_used_at DATETIME)"
                    ),
                    "CREATE INDEX IF NOT EXISTS ix_mcp_clients_revoked ON mcp_clients(revoked_at)",
                    "CREATE INDEX IF NOT EXISTS ix_mcp_clients_tenant ON mcp_clients(tenant_id)",
                    # ── GDPR Sprint 2: soft-delete columns ─────────────────────
                    # All gated by ENABLE_SOFT_DELETE at query time; columns are
                    # additive and safe even when the flag is off.
                    "ALTER TABLE users ADD COLUMN deleted_at DATETIME",
                    "ALTER TABLE conversations ADD COLUMN deleted_at DATETIME",
                    "ALTER TABLE messages ADD COLUMN deleted_at DATETIME",
                    "ALTER TABLE documents ADD COLUMN deleted_at DATETIME",
                    "ALTER TABLE projects ADD COLUMN deleted_at DATETIME",
                    "CREATE INDEX IF NOT EXISTS ix_users_deleted_at ON users(deleted_at)",
                    "CREATE INDEX IF NOT EXISTS ix_conversations_deleted_at ON conversations(deleted_at)",
                    "CREATE INDEX IF NOT EXISTS ix_messages_deleted_at ON messages(deleted_at)",
                    "CREATE INDEX IF NOT EXISTS ix_documents_deleted_at ON documents(deleted_at)",
                    "CREATE INDEX IF NOT EXISTS ix_projects_deleted_at ON projects(deleted_at)",
            ]
            for ddl in migrations:
                try:
                    # Fresh connection per statement — immune to prior tx failures
                    async with engine.begin() as _conn:
                        await _conn.execute(text(ddl))
                except Exception:
                    pass  # Column/index already exists — safe to ignore

            logger.info("SQLite database initialized")
            db_available = True
        except Exception as e:
            logger.error(f"Failed to initialize SQLite: {e}")
            db_available = False


class MockSession:
    """Mock session that does nothing - for when db is unavailable."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def add(self, obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass

    async def execute(self, *args, **kwargs):
        class MockResult:
            def scalar_one_or_none(self):
                return None
            def scalar(self):
                return None
            def scalars(self):
                return MockScalars()
            def first(self):
                return None
        return MockResult()

    async def delete(self, obj):
        pass

    async def refresh(self, obj):
        pass  # No-op: object stays in its current in-memory state

    async def scalar(self, *args, **kwargs):
        return None


class MockScalars:
    def all(self):
        return []
    def first(self):
        return None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database sessions."""
    global db_available
    allow_mock_fallback = settings.APP_ENV == "development" or settings.DEBUG

    if not db_available:
        # Try to recover — re-attempt the real DB rather than staying on MockSession forever.
        # IMPORTANT: we yield exactly once and never yield in an exception handler to avoid
        # RuntimeError "generator didn't stop after athrow()" on Python 3.10+.
        session_opened = False
        try:
            async with async_session_maker() as session:
                session_opened = True
                try:
                    yield session
                    await session.commit()
                    db_available = True  # DB is back
                except GeneratorExit:
                    raise
                except Exception:
                    await session.rollback()
                    raise
        except GeneratorExit:
            raise
        except Exception as e:
            if not session_opened:
                if allow_mock_fallback:
                    logger.warning("Database still unavailable (dev fallback): %s", e)
                    yield MockSession()
                else:
                    logger.error("Database unavailable in production mode: %s", e)
                    raise RuntimeError("Database unavailable") from e
        return

    # Main path: DB available.
    # We yield exactly once. If an exception arrives via athrow() AFTER the yield,
    # we must NOT yield again — just let it propagate.
    _yielded = False
    try:
        async with async_session_maker() as session:
            try:
                _yielded = True
                yield session
                await session.commit()
            except GeneratorExit:
                raise
            except Exception:
                await session.rollback()
                raise
    except GeneratorExit:
        raise
    except Exception as e:
        if not _yielded:
            # Connection setup failed before yielding — safe to fall back.
            db_available = False
            if allow_mock_fallback:
                logger.warning("Database unavailable (pre-yield dev fallback): %s", e)
                yield MockSession()
            else:
                logger.error("Database unavailable in production mode: %s", e)
                raise RuntimeError("Database unavailable") from e
        else:
            # Exception arrived after yield — just log, can't yield again.
            logger.warning("Database error after session yield: %s", e)
            db_available = False
