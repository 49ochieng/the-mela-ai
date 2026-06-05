-- ============================================================================
-- Mela AI - Azure SQL Database Schema Migration
-- ============================================================================
-- Target:      Azure SQL Database (SQL Server compatible)
-- Project:     Mela AI - Enterprise AI Assistant
-- Description: Initial schema creation for all core tables, indexes,
--              foreign key constraints, and seed data.
--
-- Usage:       Execute this script against an Azure SQL Database instance.
--              Ensure the target database already exists before running.
--
-- Notes:
--   - All primary keys use NVARCHAR(255) to support UUID/GUID string values
--     generated at the application layer (SQLAlchemy models).
--   - NVARCHAR(MAX) columns storing JSON are annotated; Azure SQL does not
--     enforce a JSON type but supports JSON functions on NVARCHAR(MAX).
--   - DATETIME2 is used instead of DATETIME for higher precision and range.
--   - BIT columns represent boolean values (1 = true, 0 = false).
--   - Foreign keys use ON DELETE SET NULL unless otherwise noted.
--   - The script is idempotent: tables are created only if they do not exist.
-- ============================================================================


-- ============================================================================
-- 1. USERS
-- ============================================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'users')
BEGIN
    CREATE TABLE [dbo].[users] (
        [id]                NVARCHAR(255)   NOT NULL,
        [azure_id]          NVARCHAR(255)   NOT NULL,
        [email]             NVARCHAR(255)   NOT NULL,
        [name]              NVARCHAR(255)   NULL,
        [department]        NVARCHAR(255)   NULL,
        [job_title]         NVARCHAR(255)   NULL,
        [role]              NVARCHAR(50)    NOT NULL    DEFAULT 'user',
        [preferred_model]   NVARCHAR(100)   NOT NULL    DEFAULT 'gpt-4o',
        [daily_token_limit] INT             NOT NULL    DEFAULT 100000,
        [tokens_used_today] INT             NOT NULL    DEFAULT 0,
        [is_active]         BIT             NOT NULL    DEFAULT 1,
        [last_login]        DATETIME2       NULL,
        [created_at]        DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),
        [updated_at]        DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),

        CONSTRAINT [PK_users] PRIMARY KEY CLUSTERED ([id]),
        CONSTRAINT [UQ_users_azure_id] UNIQUE ([azure_id]),
        CONSTRAINT [CK_users_role] CHECK ([role] IN ('admin', 'user', 'viewer'))
    );
END;
GO


-- ============================================================================
-- 2. CONVERSATIONS
-- ============================================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'conversations')
BEGIN
    CREATE TABLE [dbo].[conversations] (
        [id]            NVARCHAR(255)   NOT NULL,
        [user_id]       NVARCHAR(255)   NULL,
        [title]         NVARCHAR(500)   NULL,
        [model]         NVARCHAR(100)   NULL,
        [system_prompt] NVARCHAR(MAX)   NULL,
        [is_archived]   BIT             NOT NULL    DEFAULT 0,
        [message_count] INT             NOT NULL    DEFAULT 0,
        [created_at]    DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),
        [updated_at]    DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),

        CONSTRAINT [PK_conversations] PRIMARY KEY CLUSTERED ([id]),
        CONSTRAINT [FK_conversations_user] FOREIGN KEY ([user_id])
            REFERENCES [dbo].[users] ([id]) ON DELETE SET NULL
    );
END;
GO


-- ============================================================================
-- 3. MESSAGES
-- ============================================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'messages')
BEGIN
    CREATE TABLE [dbo].[messages] (
        [id]                NVARCHAR(255)   NOT NULL,
        [conversation_id]   NVARCHAR(255)   NULL,
        [role]              NVARCHAR(50)    NOT NULL,
        [content]           NVARCHAR(MAX)   NULL,
        [tokens_used]       INT             NOT NULL    DEFAULT 0,
        [model]             NVARCHAR(100)   NULL,
        [tool_calls]        NVARCHAR(MAX)   NULL,       -- JSON
        [tool_results]      NVARCHAR(MAX)   NULL,       -- JSON
        [citations]         NVARCHAR(MAX)   NULL,       -- JSON
        [created_at]        DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),

        CONSTRAINT [PK_messages] PRIMARY KEY CLUSTERED ([id]),
        CONSTRAINT [FK_messages_conversation] FOREIGN KEY ([conversation_id])
            REFERENCES [dbo].[conversations] ([id]) ON DELETE SET NULL,
        CONSTRAINT [CK_messages_role] CHECK ([role] IN ('user', 'assistant', 'system', 'tool'))
    );
END;
GO


-- ============================================================================
-- 4. DOCUMENTS
-- ============================================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'documents')
BEGIN
    CREATE TABLE [dbo].[documents] (
        [id]            NVARCHAR(255)   NOT NULL,
        [title]         NVARCHAR(500)   NULL,
        [filename]      NVARCHAR(500)   NULL,
        [file_type]     NVARCHAR(50)    NULL,
        [file_size]     BIGINT          NULL,
        [source]        NVARCHAR(50)    NULL,
        [source_url]    NVARCHAR(2000)  NULL,
        [blob_path]     NVARCHAR(2000)  NULL,
        [chunk_count]   INT             NOT NULL    DEFAULT 0,
        [is_indexed]    BIT             NOT NULL    DEFAULT 0,
        [is_active]     BIT             NOT NULL    DEFAULT 1,
        [metadata]      NVARCHAR(MAX)   NULL,       -- JSON
        [uploaded_by]   NVARCHAR(255)   NULL,
        [created_at]    DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),
        [updated_at]    DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),

        CONSTRAINT [PK_documents] PRIMARY KEY CLUSTERED ([id]),
        CONSTRAINT [FK_documents_uploaded_by] FOREIGN KEY ([uploaded_by])
            REFERENCES [dbo].[users] ([id]) ON DELETE SET NULL,
        CONSTRAINT [CK_documents_source] CHECK ([source] IN ('upload', 'sharepoint', 'web'))
    );
END;
GO


-- ============================================================================
-- 5. DOCUMENT_CHUNKS
-- ============================================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'document_chunks')
BEGIN
    CREATE TABLE [dbo].[document_chunks] (
        [id]            NVARCHAR(255)   NOT NULL,
        [document_id]   NVARCHAR(255)   NOT NULL,
        [chunk_index]   INT             NOT NULL,
        [content]       NVARCHAR(MAX)   NULL,
        [token_count]   INT             NULL,
        [embedding_id]  NVARCHAR(255)   NULL,
        [created_at]    DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),

        CONSTRAINT [PK_document_chunks] PRIMARY KEY CLUSTERED ([id]),
        CONSTRAINT [FK_document_chunks_document] FOREIGN KEY ([document_id])
            REFERENCES [dbo].[documents] ([id]) ON DELETE CASCADE
    );
END;
GO


-- ============================================================================
-- 6. AUDIT_LOGS
-- ============================================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'audit_logs')
BEGIN
    CREATE TABLE [dbo].[audit_logs] (
        [id]            NVARCHAR(255)   NOT NULL,
        [user_id]       NVARCHAR(255)   NULL,
        [action]        NVARCHAR(100)   NULL,
        [resource_type] NVARCHAR(100)   NULL,
        [resource_id]   NVARCHAR(255)   NULL,
        [details]       NVARCHAR(MAX)   NULL,       -- JSON
        [ip_address]    NVARCHAR(50)    NULL,
        [user_agent]    NVARCHAR(500)   NULL,
        [success]       BIT             NOT NULL    DEFAULT 1,
        [error_message] NVARCHAR(MAX)   NULL,
        [created_at]    DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),

        CONSTRAINT [PK_audit_logs] PRIMARY KEY CLUSTERED ([id]),
        CONSTRAINT [FK_audit_logs_user] FOREIGN KEY ([user_id])
            REFERENCES [dbo].[users] ([id]) ON DELETE SET NULL
    );
END;
GO


-- ============================================================================
-- 7. MODEL_USAGE
-- ============================================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'model_usage')
BEGIN
    CREATE TABLE [dbo].[model_usage] (
        [id]                NVARCHAR(255)   NOT NULL,
        [user_id]           NVARCHAR(255)   NULL,
        [conversation_id]   NVARCHAR(255)   NULL,
        [model]             NVARCHAR(100)   NULL,
        [prompt_tokens]     INT             NULL,
        [completion_tokens] INT             NULL,
        [total_tokens]      INT             NULL,
        [created_at]        DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),

        CONSTRAINT [PK_model_usage] PRIMARY KEY CLUSTERED ([id]),
        CONSTRAINT [FK_model_usage_user] FOREIGN KEY ([user_id])
            REFERENCES [dbo].[users] ([id]) ON DELETE SET NULL,
        CONSTRAINT [FK_model_usage_conversation] FOREIGN KEY ([conversation_id])
            REFERENCES [dbo].[conversations] ([id]) ON DELETE SET NULL
    );
END;
GO


-- ============================================================================
-- 8. SYSTEM_SETTINGS
-- ============================================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'system_settings')
BEGIN
    CREATE TABLE [dbo].[system_settings] (
        [key]           NVARCHAR(255)   NOT NULL,
        [value]         NVARCHAR(MAX)   NULL,
        [description]   NVARCHAR(500)   NULL,
        [updated_by]    NVARCHAR(255)   NULL,
        [updated_at]    DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),

        CONSTRAINT [PK_system_settings] PRIMARY KEY CLUSTERED ([key]),
        CONSTRAINT [FK_system_settings_updated_by] FOREIGN KEY ([updated_by])
            REFERENCES [dbo].[users] ([id]) ON DELETE SET NULL
    );
END;
GO


-- ============================================================================
-- 9. ENABLED_TOOLS
-- ============================================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'enabled_tools')
BEGIN
    CREATE TABLE [dbo].[enabled_tools] (
        [id]                    NVARCHAR(255)   NOT NULL,
        [tool_name]             NVARCHAR(100)   NOT NULL,
        [display_name]          NVARCHAR(255)   NULL,
        [description]           NVARCHAR(500)   NULL,
        [is_enabled]            BIT             NOT NULL    DEFAULT 1,
        [requires_confirmation] BIT             NOT NULL    DEFAULT 1,
        [allowed_roles]         NVARCHAR(MAX)   NULL,       -- JSON array
        [configuration]         NVARCHAR(MAX)   NULL,       -- JSON
        [created_at]            DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),
        [updated_at]            DATETIME2       NOT NULL    DEFAULT GETUTCDATE(),

        CONSTRAINT [PK_enabled_tools] PRIMARY KEY CLUSTERED ([id]),
        CONSTRAINT [UQ_enabled_tools_tool_name] UNIQUE ([tool_name])
    );
END;
GO


-- ============================================================================
-- INDEXES
-- ============================================================================
-- Users
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_users_azure_id')
    CREATE NONCLUSTERED INDEX [IX_users_azure_id] ON [dbo].[users] ([azure_id]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_users_email')
    CREATE NONCLUSTERED INDEX [IX_users_email] ON [dbo].[users] ([email]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_users_created_at')
    CREATE NONCLUSTERED INDEX [IX_users_created_at] ON [dbo].[users] ([created_at]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_users_is_active')
    CREATE NONCLUSTERED INDEX [IX_users_is_active] ON [dbo].[users] ([is_active]);
GO

-- Conversations
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_conversations_user_id')
    CREATE NONCLUSTERED INDEX [IX_conversations_user_id] ON [dbo].[conversations] ([user_id]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_conversations_created_at')
    CREATE NONCLUSTERED INDEX [IX_conversations_created_at] ON [dbo].[conversations] ([created_at]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_conversations_user_id_created_at')
    CREATE NONCLUSTERED INDEX [IX_conversations_user_id_created_at]
        ON [dbo].[conversations] ([user_id], [created_at] DESC);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_conversations_is_archived')
    CREATE NONCLUSTERED INDEX [IX_conversations_is_archived]
        ON [dbo].[conversations] ([is_archived]) INCLUDE ([user_id], [title], [created_at]);
GO

-- Messages
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_messages_conversation_id')
    CREATE NONCLUSTERED INDEX [IX_messages_conversation_id] ON [dbo].[messages] ([conversation_id]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_messages_created_at')
    CREATE NONCLUSTERED INDEX [IX_messages_created_at] ON [dbo].[messages] ([created_at]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_messages_conversation_id_created_at')
    CREATE NONCLUSTERED INDEX [IX_messages_conversation_id_created_at]
        ON [dbo].[messages] ([conversation_id], [created_at] ASC);
GO

-- Documents
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_documents_uploaded_by')
    CREATE NONCLUSTERED INDEX [IX_documents_uploaded_by] ON [dbo].[documents] ([uploaded_by]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_documents_created_at')
    CREATE NONCLUSTERED INDEX [IX_documents_created_at] ON [dbo].[documents] ([created_at]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_documents_is_indexed')
    CREATE NONCLUSTERED INDEX [IX_documents_is_indexed]
        ON [dbo].[documents] ([is_indexed]) INCLUDE ([id], [title], [file_type]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_documents_source')
    CREATE NONCLUSTERED INDEX [IX_documents_source] ON [dbo].[documents] ([source]);
GO

-- Document Chunks
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_document_chunks_document_id')
    CREATE NONCLUSTERED INDEX [IX_document_chunks_document_id]
        ON [dbo].[document_chunks] ([document_id]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_document_chunks_document_id_chunk_index')
    CREATE NONCLUSTERED INDEX [IX_document_chunks_document_id_chunk_index]
        ON [dbo].[document_chunks] ([document_id], [chunk_index] ASC);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_document_chunks_embedding_id')
    CREATE NONCLUSTERED INDEX [IX_document_chunks_embedding_id]
        ON [dbo].[document_chunks] ([embedding_id]);
GO

-- Audit Logs
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_audit_logs_user_id')
    CREATE NONCLUSTERED INDEX [IX_audit_logs_user_id] ON [dbo].[audit_logs] ([user_id]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_audit_logs_created_at')
    CREATE NONCLUSTERED INDEX [IX_audit_logs_created_at] ON [dbo].[audit_logs] ([created_at] DESC);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_audit_logs_action')
    CREATE NONCLUSTERED INDEX [IX_audit_logs_action] ON [dbo].[audit_logs] ([action]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_audit_logs_resource_type_resource_id')
    CREATE NONCLUSTERED INDEX [IX_audit_logs_resource_type_resource_id]
        ON [dbo].[audit_logs] ([resource_type], [resource_id]);
GO

-- Model Usage
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_model_usage_user_id')
    CREATE NONCLUSTERED INDEX [IX_model_usage_user_id] ON [dbo].[model_usage] ([user_id]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_model_usage_conversation_id')
    CREATE NONCLUSTERED INDEX [IX_model_usage_conversation_id] ON [dbo].[model_usage] ([conversation_id]);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_model_usage_created_at')
    CREATE NONCLUSTERED INDEX [IX_model_usage_created_at] ON [dbo].[model_usage] ([created_at] DESC);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_model_usage_user_id_created_at')
    CREATE NONCLUSTERED INDEX [IX_model_usage_user_id_created_at]
        ON [dbo].[model_usage] ([user_id], [created_at] DESC);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_model_usage_model')
    CREATE NONCLUSTERED INDEX [IX_model_usage_model] ON [dbo].[model_usage] ([model]);
GO


-- ============================================================================
-- SEED DATA: ENABLED_TOOLS
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM [dbo].[enabled_tools] WHERE [tool_name] = 'email_tool')
BEGIN
    INSERT INTO [dbo].[enabled_tools]
        ([id], [tool_name], [display_name], [description], [is_enabled], [requires_confirmation], [allowed_roles], [configuration])
    VALUES
        (NEWID(), 'email_tool', 'Email', 'Send and read emails via Microsoft Graph API', 1, 1,
         '["admin","user"]',
         '{"graph_endpoint":"https://graph.microsoft.com/v1.0/me/messages","max_recipients":10,"allowed_domains":[]}');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[enabled_tools] WHERE [tool_name] = 'calendar_tool')
BEGIN
    INSERT INTO [dbo].[enabled_tools]
        ([id], [tool_name], [display_name], [description], [is_enabled], [requires_confirmation], [allowed_roles], [configuration])
    VALUES
        (NEWID(), 'calendar_tool', 'Calendar', 'Manage calendar events via Microsoft Graph API', 1, 1,
         '["admin","user"]',
         '{"graph_endpoint":"https://graph.microsoft.com/v1.0/me/calendar","default_duration_minutes":30,"max_attendees":50}');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[enabled_tools] WHERE [tool_name] = 'teams_tool')
BEGIN
    INSERT INTO [dbo].[enabled_tools]
        ([id], [tool_name], [display_name], [description], [is_enabled], [requires_confirmation], [allowed_roles], [configuration])
    VALUES
        (NEWID(), 'teams_tool', 'Teams', 'Send messages and manage Teams channels via Microsoft Graph API', 1, 1,
         '["admin","user"]',
         '{"graph_endpoint":"https://graph.microsoft.com/v1.0/me/chats","max_message_length":4000}');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[enabled_tools] WHERE [tool_name] = 'planner_tool')
BEGIN
    INSERT INTO [dbo].[enabled_tools]
        ([id], [tool_name], [display_name], [description], [is_enabled], [requires_confirmation], [allowed_roles], [configuration])
    VALUES
        (NEWID(), 'planner_tool', 'Planner', 'Create and manage tasks in Microsoft Planner', 1, 1,
         '["admin","user"]',
         '{"graph_endpoint":"https://graph.microsoft.com/v1.0/me/planner/tasks","default_bucket":null}');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[enabled_tools] WHERE [tool_name] = 'sharepoint_tool')
BEGIN
    INSERT INTO [dbo].[enabled_tools]
        ([id], [tool_name], [display_name], [description], [is_enabled], [requires_confirmation], [allowed_roles], [configuration])
    VALUES
        (NEWID(), 'sharepoint_tool', 'SharePoint', 'Search and retrieve documents from SharePoint sites', 1, 0,
         '["admin","user","viewer"]',
         '{"graph_endpoint":"https://graph.microsoft.com/v1.0/sites","max_results":50,"allowed_sites":[]}');
END;
GO


-- ============================================================================
-- SEED DATA: SYSTEM_SETTINGS
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'default_model')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('default_model', 'gpt-4o', 'Default AI model for new conversations');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'max_tokens')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('max_tokens', '4096', 'Maximum tokens per completion response');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'rag_enabled')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('rag_enabled', 'true', 'Enable Retrieval-Augmented Generation for document-backed answers');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'rag_chunk_size')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('rag_chunk_size', '512', 'Number of tokens per document chunk for RAG indexing');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'rag_chunk_overlap')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('rag_chunk_overlap', '50', 'Number of overlapping tokens between adjacent document chunks');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'rag_top_k')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('rag_top_k', '5', 'Number of top matching chunks to include in RAG context');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'daily_token_limit_default')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('daily_token_limit_default', '100000', 'Default daily token limit for new users');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'max_file_size_mb')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('max_file_size_mb', '50', 'Maximum upload file size in megabytes');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'allowed_file_types')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('allowed_file_types', '["pdf","docx","xlsx","pptx","txt","csv","md","json"]',
            'JSON array of permitted file extensions for document upload');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'system_prompt')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('system_prompt',
            'You are Mela AI, an enterprise AI assistant. You help users with tasks using available tools and organizational knowledge. Be concise, accurate, and professional.',
            'Default system prompt prepended to all conversations');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'session_timeout_minutes')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('session_timeout_minutes', '60', 'Idle session timeout in minutes before requiring re-authentication');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'audit_retention_days')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('audit_retention_days', '90', 'Number of days to retain audit log entries before archival');
END;
GO

IF NOT EXISTS (SELECT 1 FROM [dbo].[system_settings] WHERE [key] = 'maintenance_mode')
BEGIN
    INSERT INTO [dbo].[system_settings] ([key], [value], [description])
    VALUES ('maintenance_mode', 'false', 'When true, the application displays a maintenance page and blocks new requests');
END;
GO


-- ============================================================================
-- END OF MIGRATION
-- ============================================================================
PRINT 'Mela AI database schema migration completed successfully.';
GO
