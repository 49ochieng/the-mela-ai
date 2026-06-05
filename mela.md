# Mela AI — Master Implementation Guide

> Enterprise technical guide, rebuild blueprint, onboarding manual, architecture
> reference, deployment handbook, and reusable AI coding skill for recreating
> Mela‑AI–style assistants for enterprise clients.
>
> **Source of truth:** This document is grounded entirely in the current
> repository at `c:\copilot\mela-ai`. Anything not directly evidenced in code is
> labeled **Not confirmed in the current codebase** or **Recommended production
> addition**.
>
> **Audience:** new engineers, security reviewers, architecture reviewers,
> deployment engineers, client implementation teams, AI coding agents.
>
> **Last reviewed against repo:** 2026‑05‑12.

---

## Table of contents

1. [What Mela AI is](#1-what-mela-ai-is)
2. [High‑level architecture](#2-highlevel-architecture)
3. [Repository layout](#3-repository-layout)
4. [Frontend (Next.js 14)](#4-frontend-nextjs-14)
5. [Backend (FastAPI)](#5-backend-fastapi)
6. [Authentication and identity](#6-authentication-and-identity)
7. [Multi‑tenancy and tenant isolation](#7-multitenancy-and-tenant-isolation)
8. [AI orchestration and agents](#8-ai-orchestration-and-agents)
9. [Model routing and providers](#9-model-routing-and-providers)
10. [RAG, Search, and the knowledge subsystem](#10-rag-search-and-the-knowledge-subsystem)
11. [Memory model (3‑layer)](#11-memory-model-3layer)
12. [Tools, connectors, and Microsoft Graph](#12-tools-connectors-and-microsoft-graph)
13. [Code interpreter and file generation](#13-code-interpreter-and-file-generation)
14. [Streaming protocol (SSE / NDJSON)](#14-streaming-protocol-sse--ndjson)
15. [Data model and migrations](#15-data-model-and-migrations)
16. [Configuration and environment variables](#16-configuration-and-environment-variables)
17. [Deployment, infrastructure, and CI/CD](#17-deployment-infrastructure-and-cicd)
18. [Observability and logging](#18-observability-and-logging)
19. [Testing strategy](#19-testing-strategy)
20. [Security review (OWASP‑style)](#20-security-review-owaspstyle)
21. [How to rebuild Mela AI from scratch](#21-how-to-rebuild-mela-ai-from-scratch)
22. [How to extend it (new tool, connector, model, tab)](#22-how-to-extend-it-new-tool-connector-model-tab)
23. [How to rebrand and white‑label for clients](#23-how-to-rebrand-and-whitelabel-for-clients)
24. [Productionisation checklist](#24-productionisation-checklist)
25. [Glossary](#25-glossary)

---

## 1. What Mela AI is

Mela AI is an enterprise AI assistant that combines:

- **Conversational chat** with multi‑provider LLMs (Azure OpenAI, Anthropic
  Claude, Google Gemini) — see [backend/app/services/openai_service.py](backend/app/services/openai_service.py),
  [backend/app/services/anthropic_service.py](backend/app/services/anthropic_service.py),
  [backend/app/services/gemini_service.py](backend/app/services/gemini_service.py).
- **Retrieval‑augmented generation** over enterprise SharePoint, OneDrive,
  organisational websites, and user‑curated uploads (Agent Memory) — see
  [backend/app/services/search/](backend/app/services/search/) and
  [backend/app/services/connectors/](backend/app/services/connectors/).
- **Agentic tool use** — Microsoft Graph (mail, calendar, Planner), web
  search, image generation (DALL‑E), speech (TTS/STT), and a Python code
  interpreter — see [backend/app/agents/tool_executor.py](backend/app/agents/tool_executor.py).
- **Worker orchestration** — a planner/router/executor that delegates work to
  registered MCP/REST/webhook workers (e.g. Task Radar, Meeting Assistant) —
  see [backend/app/orchestration/](backend/app/orchestration/).
- **A first‑class admin surface** — tenant admin tab, enterprise admin
  console with Users / Tenants / Models / Errors / Invoices / Settings /
  Audit / Onboarding / Offboarding panels — see
  [frontend/src/app/admin/page.tsx](frontend/src/app/admin/page.tsx) and
  [frontend/src/components/settings/AdminTab.tsx](frontend/src/components/settings/AdminTab.tsx).
- **An embeddable widget** — `<mela-chat>` web component for third‑party
  sites — see [frontend/src/embed/mela-chat.ts](frontend/src/embed/mela-chat.ts).

**Production deployment (current):**

- Frontend: `https://armely-ai-web.azurewebsites.net`
- Backend: `https://armely-ai-api.azurewebsites.net` (`/health`)

Source: [README.md](README.md).

---

## 2. High‑level architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Browser / Embed host                                                    │
│   ├─ Next.js 14 SPA (port 3005 dev / 3000 prod)                          │
│   │   • MSAL.js (Entra ID) → Bearer access token                         │
│   │   • Zustand store (dual personal/work namespaces)                    │
│   │   • SSE/NDJSON streaming consumer                                    │
│   └─ <mela-chat> web component (iframe + postMessage)                    │
└─────────────────┬────────────────────────────────────────────────────────┘
                  │ HTTPS · Authorization: Bearer · X-Profile-Mode · X-Tenant-Id
┌─────────────────▼────────────────────────────────────────────────────────┐
│  FastAPI backend (port 8000) — app/main.py                               │
│   Middleware: CORS → RequestLogging → RateLimit → EmbedFrame             │
│   Routers (25): /auth /chat /documents /admin /files /speech ...         │
│                                                                          │
│   chat_service ──► model_router ──► openai_service / anthropic / gemini  │
│        │                                                                 │
│        ├──► tool_executor ──► graph_service · code_interpreter · etc.    │
│        ├──► enterprise_query (search/query_pipeline.py) ──► Azure Search │
│        ├──► agent_memory_service ──► blob_storage · ingestion            │
│        ├──► memory_service (3-layer)                                     │
│        └──► orchestration.{planner, router, executor}                    │
│                                                                          │
│   Background tasks (asyncio): SharePoint/OneDrive delta sync,            │
│   ACL refresh, KB expiry, model rankings seed, worker registry seed.     │
└──────┬──────────────────┬─────────────────┬──────────────┬──────────────┘
       │                  │                 │              │
       ▼                  ▼                 ▼              ▼
   Azure SQL          Azure AI         Azure OpenAI    Microsoft Graph
   (or SQLite          Search          / Foundry       (SharePoint,
    in dev)           (HNSW            / Anthropic /    OneDrive,
                       3072‑d)         Gemini /         Mail, Calendar,
                                       DALL‑E /         Planner, Groups)
                                       Speech)
       │
       ▼
   Azure Blob Storage (uploads, generated files)  ·  Azure Key Vault (secrets)
       │
       ▼
   Registered workers (MCP / REST / webhook): Task Radar, Meeting Assistant ...
```

**Hosting:** Two Azure App Services (Linux): `armely-ai-api` (Python 3.12,
FastAPI) and `armely-ai-web` (Node 20, Next.js standalone). Provisioned by
[infra/main.bicep](infra/main.bicep) via `azd`. Resource group:
`EdgarO_RG_MCPP_WU2`. Key Vault: `kv-mela-mcpp`.

Source: [README.md](README.md), [.github/workflows/cd.yml](.github/workflows/cd.yml),
[infra/main.bicep](infra/main.bicep).

---

## 3. Repository layout

```
azure.yaml                  azd service & hook map (backend, frontend)
docker-compose.yml          Local stack (backend:8000, frontend:3000)
prod_startup.sh             App Service backend bootstrap (deps + alembic)
README.md / SECURITY.md     Operator + security docs
ORCHESTRATION.md            Worker orchestration design notes
generate_doc.py             Misc doc helper
test_*.py / comprehensive_e2e_test.py / prod_test_suite.py  E2E harnesses

backend/                    FastAPI app
  Dockerfile                python:3.11-slim multi-stage, non-root
  startup.sh                Mirror of prod_startup.sh
  alembic.ini · alembic/    Migrations (3 revisions today)
  requirements*.txt
  pytest.ini · ruff.toml
  app/
    main.py                 lifespan, middleware, background tasks
    api/router.py + endpoints/   25 routers
    agents/tool_executor.py 60+ OpenAI tool defs + dispatch
    core/                   config, security, database, middleware,
                            authorization, sessions, profile_context, logging
    mcp/server.py + tools.py  MCP HTTP dispatcher
    models/models.py        43 ORM models
    orchestration/          planner, router, executor, registry, breaker,
                            knowledge, store, auth, access, health, adapters/
    schemas/                Pydantic v2 request/response models
    services/
      chat_service.py       message flow, RAG injection, streaming
      openai_service.py     Azure OpenAI / AI Foundry + retry/fallback
      anthropic_service.py  Claude with per-user RPM
      gemini_service.py     Gemini 2.0 Flash
      model_router.py       silent cross-provider failover
      search/               index_manager · query_pipeline · ingestion · chunker
      connectors/           sharepoint · onedrive · org_website ·
                            user_web_connector · graph_client
      agent_memory_service.py
      blob_storage.py · file_security.py
      code_interpreter_service.py
      document_service.py · document_intelligence_service.py
      memory_service.py · obo_service.py · graph_service.py
      budget_service.py · template_service.py
      speech_service.py · dalle_service.py · translation_service.py
      org_context_service.py
  tests/                    pytest, conftest with StaticPool patterns

frontend/                   Next.js 14 (app router, standalone output)
  Dockerfile                node:20-alpine multi-stage, non-root
  next.config.js · tailwind.config.ts · tsconfig.json
  package.json              dev port 3005; build:embed for web component
  src/
    app/                    layout, page, /chat, /chat/[id], /projects,
                            /settings, /admin, /embed, error.tsx
    components/
      chat/                 ChatMessage, ChatInput, ChatSidebar,
                            VoiceChatOverlay, WorkerEventBar, ProfileSwitcher,
                            ProjectMemoryPanel, ShareModal ...
      settings/             SettingsModal + 14 tabs (Agent Memory,
                            Connectors, Knowledge, Models, Usage, Privacy,
                            Instructions, Skills, Admin, Monitoring,
                            Workflows, Workers, MCP Clients, Appearance)
      admin/                10 lazy-loaded panels
      embed/                EmbedChatInterface
      providers/            MsalProvider, ThemeProvider, ClientLayout
      ui/                   Radix-based primitives
    lib/                    api.ts (ApiClient), graph.ts, store.ts (Zustand),
                            msal/config.ts, utils.ts
    embed/mela-chat.ts      Custom element <mela-chat>

infra/                      Production Bicep stack (RG-scoped, used by azd + CD)
  main.bicep · main.parameters.json
  modules/                  app-service · key-vault · monitoring · storage
                            · role-assignments

infrastructure/bicep/       Alternative subscription-scoped stack (legacy/parallel)

scripts/
  azd-up.{sh,ps1}           one-command provision + deploy
  preflight.{sh,ps1}        detect existing resources -> useExisting* flags
  deploy.sh                 generic env-driven orchestrator
  scan-resources.{sh,ps1}   scan repo for required Azure services
  naming.json               deterministic resource naming
  resource-inventory.json   output of scan-resources

database/schema.sql         Idempotent T-SQL baseline (Azure SQL)

docs/                       azure-setup.md, resources-required.md,
                            validation-checklist.md, mela-orchestration-onepager.html

.github/workflows/          ci.yml · cd.yml · security.yml
```

---

## 4. Frontend (Next.js 14)

### 4.1 App Router routes

| Route | File | Purpose |
| ----- | ---- | ------- |
| `/` | [src/app/page.tsx](frontend/src/app/page.tsx) | Landing + login (MSAL popup), redirects to `/chat` when authenticated |
| `/chat` | [src/app/chat/page.tsx](frontend/src/app/chat/page.tsx) | Default chat (latest or new) |
| `/chat/[id]` | [src/app/chat/[id]/page.tsx](frontend/src/app/chat/[id]/page.tsx) | Conversation by UUID; main chat shell |
| `/projects/[projectId]` | [src/app/projects/[projectId]/page.tsx](frontend/src/app/projects/[projectId]/page.tsx) | Project workspace (files, conversations, members) |
| `/settings` | [src/app/settings/page.tsx](frontend/src/app/settings/page.tsx) | Legacy; modern settings live in modal launched from chat |
| `/embed` | [src/app/embed/page.tsx](frontend/src/app/embed/page.tsx) | Iframe shell for `<mela-chat>` web component |
| `/admin` | [src/app/admin/page.tsx](frontend/src/app/admin/page.tsx) | Enterprise admin console with 10 lazy panels |
| `error.tsx` / `global-error.tsx` / `not-found.tsx` | — | Error boundaries |

### 4.2 Authentication

- **MSAL config:** [src/lib/msal/config.ts](frontend/src/lib/msal/config.ts) —
  `loginRequest` (User.Read + profile, no API scope to avoid multi‑resource
  failures), `backendTokenRequest` (`api://<client-id>/access_as_user`),
  Graph scopes for delegated mail/calendar/tasks.
- **Provider:** [src/components/providers/MsalProvider.tsx](frontend/src/components/providers/MsalProvider.tsx) —
  singleton MSAL instance (`_msalInstance`, `_initPromise`) so React 18 Strict
  Mode + Next SSR hydration cannot double‑init.
- **Token acquisition:** `ApiClient.getAccessToken()` in
  [src/lib/api.ts](frontend/src/lib/api.ts) — silent → popup fallback;
  dev mode reads `mela_dev_token` from localStorage.
- **Outbound headers:** every request carries `Authorization: Bearer`,
  `X-Profile-Mode` (`work`|`personal`), `X-Tenant-Id` (work only),
  `X-User-Timezone` (IANA from `Intl`).

### 4.3 State management — Zustand with dual namespaces

[src/lib/store.ts](frontend/src/lib/store.ts) defines `useChatStore` with a
**dual‑namespace** design (`namespaces.personal` and `namespaces.work`). The
active namespace is mirrored to root‑level fields (conversations, projects,
sharedConversations) so components can read without knowing about the active
profile. Switching profiles snapshots the current namespace and restores the
other; mid‑stream switches cannot corrupt either namespace.

Persisted via `persist()` middleware (`name: 'chat-store'`): conversations,
projects, preferences, `activeProfile`, `tenantId`. **Not** persisted:
`messages` (session‑only), `streamingContent`, `workerEvents`, `userFeatures`.

`userFeatures` is polled every 5 minutes from `GET /user/features` to refresh
role and feature flags.

### 4.4 Streaming consumer

`async *streamChat(request, signal)` in
[src/lib/api.ts](frontend/src/lib/api.ts) calls
`POST /api/v1/chat/completions` with `stream: true`, reads NDJSON via
`ReadableStream.getReader()`, splits on newlines, parses each `data: {...}`
line, and yields typed `ChatChunk` objects. The store loop in `sendMessage()`
handles each chunk type (`content`, `tool_executing`, `tool_call`,
`tool_result`, `thinking`, `model_switched`, `claude_usage`,
`claude_limit_reached`, `email_draft`, `citation`, `file_generated`,
`image_generated`, `done`, `error`, `worker_event`). Voice mode pre‑synthesises
the first complete sentence (≥15 chars before punctuation, or >80 char
fallback) for zero‑gap audio playback.

### 4.5 Settings modal tabs

[src/components/settings/SettingsModal.tsx](frontend/src/components/settings/SettingsModal.tsx)
gates tabs by user role:

- **Personal:** Appearance · Models · Usage · Privacy
- **Intelligence:** Knowledge · Agent Memory · Connectors · Instructions ·
  Skills · Workflows
- **Admin (admin only):** Admin · Workers · MCP Clients · Monitoring

The Agent Memory tab ([AgentMemoryTab.tsx](frontend/src/components/settings/AgentMemoryTab.tsx))
polls every 4 s while items are non‑terminal (`pending`/`parsing`/
`crawling`/`embedding`); the Add Knowledge modal supports upload + website
today, with SharePoint/OneDrive/Azure Search/Dataverse/D365/Salesforce/
ServiceNow/SQL marked **Coming soon**.

### 4.6 Admin console (10 panels)

[src/app/admin/page.tsx](frontend/src/app/admin/page.tsx) lazy‑loads:
Overview, Users, Tenants, Models, Errors, Invoices, Settings, Audit,
Onboarding, Offboarding. **Caveat:** depth of business logic varies by
panel; treat any panel that requires SQL/Stripe/etc. as a thin UI shell
unless you've verified the backing endpoint.

### 4.7 Embed widget

[src/embed/mela-chat.ts](frontend/src/embed/mela-chat.ts) defines a
`<mela-chat>` custom element (attributes: `token`, `height`, `width`, `theme`,
`base-url`). Built with esbuild (`npm run build:embed`) into
`public/embed.bundle.js` (IIFE, minified). Iframe renders
[EmbedChatInterface](frontend/src/components/embed/EmbedChatInterface.tsx),
which strips sidebar, settings, voice, profile switcher, and admin links;
backend gates the embed token via `EmbedFrameMiddleware` and per‑embed
config endpoint.

### 4.8 Build configuration

- [next.config.js](frontend/next.config.js) — `output: 'standalone'`,
  branding env vars (`NEXT_PUBLIC_APP_NAME`, `NEXT_PUBLIC_ORG_NAME`),
  security headers (`X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options:
  nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`), allowed
  remote image hosts (`graph.microsoft.com`, `*.sharepoint.com`).
- [tailwind.config.ts](frontend/tailwind.config.ts) — primary palette built
  on `#2f5597` (Armely blue); dark mode via `class`.
- [Dockerfile](frontend/Dockerfile) — node:20‑alpine multi‑stage, non‑root
  `nextjs:nodejs` (uid 1001), health‑check on `/`, `node server.js`.
- `dev` runs on port **3005** (matches the registered MSAL redirect URI).

---

## 5. Backend (FastAPI)

### 5.1 Application entrypoint — `app/main.py`

The lifespan handler ([backend/app/main.py](backend/app/main.py)) performs:

1. **Secret validation** — fails fast if `JWT_SECRET_KEY` is missing in prod
   or shorter than 32 chars; in dev, generates a random key.
2. **Database init** — async engine for SQLite (dev) or SQL Server (prod)
   via [app/core/database.py](backend/app/core/database.py) with `NullPool`.
3. **Azure AI Search index ensure** — non‑fatal if Search not configured.
4. **Background tasks** (all `asyncio.create_task`):
   - Ingestion worker for SharePoint/OneDrive/org‑website connectors.
   - Initial SharePoint delta sync per configured site.
   - 30‑minute OneDrive refresh loop for known users.
   - 24‑hour SharePoint ACL refresh queue.
   - Private chat auto‑deletion past expiry.
   - 6‑hour session memory cleanup.
   - 6‑hour knowledge base expiry sweep.
   - Idempotent model rankings seed.
   - Worker registry seed.

### 5.2 Middleware stack (outer → inner)

Configured in [backend/app/main.py](backend/app/main.py) lines ~309–316
and [app/core/middleware.py](backend/app/core/middleware.py):

1. `CORSMiddleware` — `allow_credentials=True`, origins from
   `settings.CORS_ORIGINS`.
2. `RequestLoggingMiddleware` — logs warnings only (≥WARNING or >500 ms);
   silent on `/health`, `/`, `/docs`, `/redoc`, `/openapi.json`,
   `/api/v1/ingest/*`, `/mcp/*`.
3. `RateLimitMiddleware` — sliding‑window deque per client (auth‑hash or
   IP); defaults `RATE_LIMIT_REQUESTS=100`, `RATE_LIMIT_WINDOW=60`; emits
   `429` with `retry_after`.
4. `EmbedFrameMiddleware` — sets `Content-Security-Policy: frame-ancestors`
   for `/embed/*` only, sourced from `MELA_EMBED_ALLOWED_ORIGINS`.

### 5.3 API routers — 25 in total

Registered in [app/api/router.py](backend/app/api/router.py) under prefix
`/api/v1` (configurable via `API_PREFIX`):

| Prefix | Purpose |
| ------ | ------- |
| `/auth` | login, dev‑login, token refresh |
| `/chat` | conversation completions, streaming |
| `/documents` | RAG document CRUD |
| `/admin` | users, stats, audit logs, tools |
| `/files` | upload/download |
| `/speech` | TTS / STT |
| `/translation` | content translation |
| `/images` | DALL‑E generation |
| `/document-intelligence` | Azure DI (form/table extraction) |
| `/user` | preferences, model selection, features polling |
| `/connectors` | enterprise connector config |
| `/projects` | multi‑user project CRUD |
| (root) collaboration | share links, invites |
| `/settings` | model rankings, access policies |
| `/workflows` | workflow CRUD + triggers |
| `/graph` | Microsoft Graph proxy (mail, calendar, Planner, Teams) |
| `/notifications` | user notifications |
| `/budgets` | per‑user token budgets |
| `/memories` | session memory CRUD |
| `/agent-memory` | user‑curated knowledge |
| `/orchestration` | worker task execution + planning |
| `/api/v1/ingest/*` | **worker callbacks** — `X-Worker-Id` + `X-Worker-Api-Key`, NOT user JWT |
| `/embed` | public embed widget endpoint |

### 5.4 ChatService (backbone)

[app/services/chat_service.py](backend/app/services/chat_service.py) is the
orchestrator for every chat request. Key responsibilities:

- `get_or_create_conversation()` — supports both DB and mock (in‑memory)
  modes; normalises legacy `context_type` → `profile_mode`.
- **Profile‑specific system prompts:**
  - **Personal** (~L48): no org data, no enterprise connectors, file uploads
    only; no Graph tools.
  - **Work** (~L145): full enterprise grounding; explicit citation rules;
    SharePoint/OneDrive/org‑website RAG; full Graph tool access.
- **System‑prompt augmentation blocks** injected in this order:
  1. `[LONG_TERM_MEMORY]` — Layer‑1 user prefs from `memory_service`.
  2. `[SESSION_MEMORY]` — Layer‑2 conversation summary.
  3. `[AGENT_MEMORY]` — top hits from
     `enterprise_query.search(..., source_types=['agent_memory'], top_k=6)`,
     filtered by `session_disabled[conv_id]`.
  4. `[TEMPLATE_SCHEMA]` — when top agent‑memory hit has `tag='template'`,
     calls `template_service.render_prompt_block`.
  5. `[DATA_CARD]` — for top‑2 tabular hits (CSV/XLSX), profile from
     `template_schema_json["profile"]`, capped at 30 columns/sheet.
- Calls `model_router` for the LLM round‑trip; emits a one‑shot
  `StreamChunk(type="router_resolved", data={provider, model})` for the UI.
- Persists message + tool_calls + tool_results + citations to DB; writes
  `tokens_used_today` and `ModelUsage` rows.

### 5.5 Tool execution

[app/agents/tool_executor.py](backend/app/agents/tool_executor.py) holds the
**60+ OpenAI function definitions** and dispatches them. Categories:

- **Mail (Graph):** `get_inbox`, `send_email`, `create_draft_email`,
  `search_emails`, `reply_to_email`.
- **Calendar (Graph):** `get_calendar`, `schedule_meeting`,
  `check_availability`.
- **Planner (Graph):** `list_planner_tasks`, `create_task`.
- **Search:** `search_graph`, `search_documents`.
- **Generation:** `run_python_code` (code interpreter), `generate_image`
  (DALL‑E), `create_document`.
- **Templates:** `apply_template` — case‑insensitive title match against the
  user's Agent Memory `template` items, scope visibility check.

**Personal vs work split:** Graph tools are filtered out when
`profile_mode='personal'`. Lazy imports prevent circular deps.

### 5.6 MCP server — `app/mcp`

[app/mcp/server.py](backend/app/mcp/server.py) exposes:

- `GET /tools` — discovery, returns OpenAI‑compatible function defs filtered
  by client scope.
- `POST /` — single‑entry dispatcher keyed on `tool` field (mirrors Task
  Radar wire shape).

Tools (`mcp_chat`, `mcp_search_kb`, `mcp_create_task`,
`mcp_get_worker_health`, `mcp_ingest_kb_entry`) are thin wrappers around
existing services. Auth via `X-Api-Key` header validated by
`require_mcp_client()`; per‑tool scope check via `assert_tool_scope()`. The
MCP client credential model is `MCPClient` with hashed `api_key` and JSON
`scopes`.

---

## 6. Authentication and identity

### 6.1 Production flow (Microsoft Entra ID)

1. Browser calls `instance.loginPopup(loginRequest)` (User.Read + profile,
   *not* the API scope — avoids multi‑resource consent failure).
2. On success, MSAL stores tokens in browser cache; `ApiClient` calls
   `acquireTokenSilent(backendTokenRequest)` to get an access token for
   `api://<client-id>/access_as_user`.
3. Backend validates the token in `app/core/security.py`:
   - Fetches JWKS from
     `https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys`,
     cached 1 hour.
   - Matches the token's `kid` header against the JWKS array.
   - Accepts both bare‑GUID and `api://`‑prefixed audiences (v1 and v2
     tokens) — see lines 99–116.
   - **Tenant isolation** via `tid` claim: rejects unless `tid ==
     AZURE_TENANT_ID` (or unless multi‑tenant endpoint
     `common`/`organizations`/`consumers` is configured) — lines 119–139.
   - Builds `UserInfo` ([app/schemas/auth.py](backend/app/schemas/auth.py))
     with `id` (oid), `email`, `name`, `given_name`, `family_name`,
     `roles`, `groups`, `department`, `job_title`, `tenant_id`.
4. `get_current_user()` is the FastAPI dependency every protected endpoint
   uses; `get_current_admin_user()` adds the admin gate.

### 6.2 Dev login

- Endpoint: `POST /auth/dev-login` in
  [app/api/endpoints/auth.py](backend/app/api/endpoints/auth.py) (~L287).
- **Guard:** requires `ENABLE_DEV_LOGIN=true` AND (`APP_ENV=='development'`
  OR `DEBUG=true`). When `ENABLE_DEV_LOGIN=false`, the endpoint returns 404.
- Issues a 24‑hour internal JWT signed with `JWT_SECRET_KEY`, marked
  `is_dev=true`. `get_current_user()` calls
  `verify_internal_token()` *before* Entra validation; if it succeeds, the
  user is authenticated with hard‑coded roles `["Admin", "user"]`.
- Identity: synthetic `dev-user-001` unless `BOOTSTRAP_ADMIN_EMAILS` is
  set, in which case the first email is used.
- **Risk:** if `JWT_SECRET_KEY` leaks while `ENABLE_DEV_LOGIN=true`, an
  attacker can forge admin tokens. Mitigation: keep dev login off in prod
  (the CD pipeline sets it accordingly when the trigger is `push:main`).

### 6.3 Bootstrap admin elevation

`BOOTSTRAP_ADMIN_EMAILS` and `BOOTSTRAP_ADMIN_OIDS` are comma‑separated
allow‑lists. On admin endpoints (`/admin/me`, `/auth/login` callback), if
the current user matches and is not yet `UserRole.ADMIN`, the row is
elevated and an `AuditLog` entry (`action="bootstrap_admin_elevation"`)
is written. `bootstrap_elevated_at` and `promoted_at` columns track this
on the `User` model.

### 6.4 ACL identity caveat (dev mode)

The fix recorded in [agent_memory_acl_dual_id.md](memory:repo) applies:
in dev mode, `UserInfo.id` (DB primary key) and `User.azure_id` (Entra
OID) differ. `agent_memory_service._build_acl(personal)` therefore
writes both IDs into `acl_users` so retrieval works regardless of which
identifier the chat path uses.

---

## 7. Multi‑tenancy and tenant isolation

### 7.1 Tenant context propagation

- **Source of truth:** the `tid` claim from the validated JWT, copied to
  `UserInfo.tenant_id` in [app/core/security.py](backend/app/core/security.py)
  (~L276). In dev mode, falls back to `"dev-tenant"` or
  `AZURE_TENANT_ID`.
- **Profile boundary:** `profile_mode='work'` requires `tenant_id`;
  `profile_mode='personal'` must have no `tenant_id`. Enforced in
  `chat_service` (~L463–470).
- **Database:** `Conversation`, `Message`, `Project`, `ProjectMemory`,
  `KnowledgeEntry`, and `AgentMemoryItem` all carry `tenant_id`.
- **Frontend:** `X-Tenant-Id` header sent on every API call when in work
  mode (Zustand store + `_profileHeaders()`).

### 7.2 Search ACL pattern

Documents indexed in Azure AI Search carry:

- `acl_users: String[]` — Azure AD OIDs.
- `acl_groups: String[]` — Azure AD group OIDs.
- `workspace_id: String` — `tenant_id` for org content, `f"user:{user.id}"`
  for personal Agent Memory.
- `context_type: String` — `"org"` | `"personal"`.

The OData filter built by
[`_build_acl_filter()`](backend/app/services/search/query_pipeline.py) is:

```odata
(acl_users/any(u: u eq '<sanitized_user>'))
OR (acl_groups/any(g: g eq '<group_1>'))
OR (acl_groups/any(g: g eq '<group_2>'))
OR ((not acl_users/any()) and (not acl_groups/any()))
```

Empty ACL means **intentionally workspace‑public** — connectors are
**fail‑closed** and refuse to index a document if permission fetch fails
(see SharePoint connector lines 264–269). `workspace_id` is always
combined into the filter to prevent cross‑tenant leakage.

### 7.3 ACL building per scope

| Scope | `acl_users` | `acl_groups` | `workspace_id` |
| ----- | ----------- | ------------ | -------------- |
| `personal` | `[user.azure_id, str(user.id)]` (deduped) | `[]` | `f"user:{user.id}"` |
| `workspace` | `[]` | `[tenant_id]` | `tenant_id` |
| `tenant` | `[]` | `[]` | `tenant_id` |

Source: [agent_memory_service.py](backend/app/services/agent_memory_service.py)
`_build_acl`, `_workspace_id`.

### 7.4 Known gaps (must remediate before multi‑tenant prod)

The security review (§20) flagged that several admin queries do **not**
yet filter by `tenant_id` (e.g. `GET /admin/stats`,
`GET /admin/analytics` in [admin.py](backend/app/api/endpoints/admin.py)
~L199–245). The codebase is currently safe under single‑tenant
deployment because Entra `tid` validation gates the door, but
multi‑tenant SaaS deployment **requires** scoping these queries.

Likewise `org_context_service._cache` in
[org_context_service.py](backend/app/services/org_context_service.py)
~L25–63 is a global dict keyed only by `user_id`. Re‑key to
`f"{tenant_id}|{user_id}"` before hosting more than one tenant per
process. Same for the search cache hash in
[query_pipeline.py](backend/app/services/search/query_pipeline.py) ~L122.

---

## 8. AI orchestration and agents

### 8.1 In‑process orchestration (`app/orchestration/`)

| File | Purpose |
| ---- | ------- |
| `types.py` | `MelaTask`, `MelaResult`, `Capability`, `Protocol`, `WorkerManifest`, `ExecutionPlan` |
| `registry.py` | Worker manifest CRUD, 60 s in‑process read cache |
| `router.py` | Validates a task's capability exists; checks circuit breaker before dispatch |
| `executor.py` | Runs task batches via `asyncio.gather(..., return_exceptions=True)`; per‑session lock to serialise commits |
| `breaker.py` | Per‑worker circuit breaker (CLOSED → OPEN → HALF_OPEN) |
| `auth.py` | `X-Worker-Id` + `X-Worker-Api-Key` HMAC validation for inbound worker callbacks |
| `access.py` | Phase‑5C default‑deny tenant ↔ worker access grants (gated by `WORKER_ACCESS_DEFAULT_ALLOW`) |
| `knowledge.py` | Keyword search over `KnowledgeEntry`; |
| `knowledge_search.py` | Vector‑augmented KB search via Azure AI Search (`AZURE_SEARCH_KB_INDEX`) |
| `store.py` | Pending‑task registry for callback‑style async workers |
| `health.py` | Periodic HTTP health probe with exponential backoff |
| `adapters/` | Per‑protocol adapters: MCP, REST, gRPC, webhook |

**Contract:** the planner trusts router/executor never to raise; every
failure surfaces as a `MelaResult(success=False, error=...)`.

### 8.2 Worker workflow

1. A worker registers via `POST /api/v1/orchestration/register` with the
   shared secret `MELA_WORKER_REGISTRATION_KEY`.
2. The manifest declares `protocol`, `base_url`, `health_check_url`,
   `auth_scheme`, and `capabilities`.
3. On chat tool invocation, the planner builds an `ExecutionPlan`; the
   router validates capabilities; the executor dispatches via the right
   adapter.
4. Async workers POST results back to `/api/v1/ingest/*` with their
   `X-Worker-Id` + `X-Worker-Api-Key`; the registry validates timing‑safe
   (`hmac.compare_digest`).
5. Completed results may auto‑summarise into `KnowledgeEntry` rows
   (`title`, `summary` ≤500 chars, `entry_type`, `source_worker_id`,
   `trace_id`, `data_pointer`, `expires_at` from `KB_DEFAULT_EXPIRY_DAYS`).

Each plan persists to `OrchestrationTrace` (one per goal) with N
`OrchestrationTask` rows.

---

## 9. Model routing and providers

### 9.1 Providers (today)

| Provider | Service file | Key models | Notes |
| -------- | ------------ | ---------- | ----- |
| Azure OpenAI / Foundry | [openai_service.py](backend/app/services/openai_service.py) | `gpt-5.2-chat` (default), `gpt-4.1`, `gpt-4o`, `kimi-k2.5`, `mistral-large-3`, `grok-3-mini`, `llama-4-maverick` | Function calling, vision (gpt‑5.2/4.1/4o), 128K context |
| Anthropic | [anthropic_service.py](backend/app/services/anthropic_service.py) | `claude-sonnet-4-6`, `claude-haiku-4-5` | Per‑user sliding RPM (default 20), `CLAUDE_DAILY_QUESTION_LIMIT` |
| Google | [gemini_service.py](backend/app/services/gemini_service.py) | `gemini-2.0-flash` | Free tier; user/final‑turn requirement enforced |

Embedding model: `text-embedding-3-large` (3072 dim) via Azure OpenAI —
constant `EMBED_DIMS = 3072` in
[index_manager.py](backend/app/services/search/index_manager.py).

### 9.2 Model router silent failover

[model_router.py](backend/app/services/model_router.py):

1. Try the requested model's native provider.
2. If it fails **before any content streams**, fall back to the Azure
   OpenAI backbone chain: `gpt-5.2-chat → gpt-4.1 → gpt-4o → grok-3-mini`.
3. Secondary: Anthropic Haiku if configured.
4. Tertiary: Gemini Flash if configured.

**Budget downgrade:** if budget service reports ≥90% used, force
`gpt-4o-mini`; ≥70% caps to tier‑1 models.

The router emits exactly one `StreamChunk(type="router_resolved", data={
provider, model})` per response so the UI shows the actual model used.

### 9.3 Retry and rate limits

- OpenAI: 2 retries, 0.2 s → 0.4 s backoff (decorator `_with_retry`).
- Anthropic: per‑user sliding deque (`ANTHROPIC_RPM_LIMIT`, default 20);
  exceeded → user notice but no chat failure.
- Embeddings: same retry decorator; failures yield empty vector and
  fall back to keyword‑only search at ingest/query time.

---

## 10. RAG, Search, and the knowledge subsystem

### 10.1 Index schema (Azure AI Search)

[index_manager.py](backend/app/services/search/index_manager.py) defines
two indexes:

**`fileshare-vector-documents`** (configurable via `AZURE_SEARCH_VECTOR_INDEX_NAME`):

| Field | Type | Filt. | Search. | Purpose |
| ----- | ---- | ----- | ------- | ------- |
| `id` | String (key) | ✓ | | chunk id |
| `workspace_id` | String | ✓ | | tenant or `user:{id}` |
| `context_type` | String | ✓ | | `org` / `personal` |
| `source_type` | String | ✓ | | `sharepoint`/`onedrive`/`web`/`upload`/`agent_memory` |
| `source_id` | String | ✓ | | opaque source id (used for delete/disable) |
| `title` | String | | ✓ | |
| `content` | String | | ✓ | chunk text + metadata header |
| `url`, `path` | String | | | retrievable for citation |
| `file_type` | String | ✓ | | |
| `last_modified`, `created_at` | DateTimeOffset | ✓ | | |
| `chunk_id`, `chunk_index` | String / Int32 | ✓ | | |
| `citation_json` | String | | | pre‑built citation dict |
| `acl_users`, `acl_groups` | String[] | ✓ | | OIDs only |
| `sensitivity_label` | String | ✓ | | Purview/AIP label |
| `memory_scope` | String | ✓ | | `personal`/`workspace`/`tenant` |
| `tag` | String | ✓ | | `knowledge`/`template`/`brand`/`policy`/`demo` |
| `agent_memory_item_id` | String | ✓ | | FK back to `AgentMemoryItem` for delete/disable |
| `content_vector` | Single[3072] | | ✓ (vector) | HNSW profile |

Vector profile: HNSW algorithm (`hnsw-profile`).

**`mela-query-cache`** (`AZURE_SEARCH_CACHE_INDEX_NAME`): SHA256[:32] hash of
`{user_id}:{context_type}:{query}:{workspace_id}:{source_types}` → cached
results JSON, `expires_at` (1 h TTL), `hit_count`. **Caveat (security
review §20):** today's hash key omits `tenant_id`; add it to harden
multi‑tenant cache isolation.

### 10.2 Hybrid search pipeline

`enterprise_query` singleton in
[query_pipeline.py](backend/app/services/search/query_pipeline.py) (line
~579 historically — verify in your branch). Flow:

1. Hash query + check cache.
2. Embed query via Azure OpenAI; validate dimension; on failure fall back
   to keyword‑only.
3. Build OData ACL filter (§7.2) plus workspace/context scoping.
4. Hybrid vector + BM25 search; if vector unavailable, keyword only.
5. Cache results 1 h.
6. Build citation block via `SourceRecord.to_citation_dict()` (lines 30–64)
   — fields include `source_type`, `chunk_id`, `chunk_text`, `file_name`,
   `web_url`, `location_hint`, `site_url`, `drive_id`, `item_id`, `etag`.

When `source_types == {"agent_memory"}`, the Graph Live Search fallback
is **skipped** (see [agent_memory_acl_dual_id.md](memory:repo)) so chat
injection's 8 s wait_for budget isn't blown by a 15 s Graph timeout.

### 10.3 Ingestion pipeline

[ingestion.py](backend/app/services/search/ingestion.py):

1. `chunker.chunk_document()` — token‑aware (`tiktoken cl100k_base`),
   sentence‑boundary preferred (`. `, `.\n`, `! `, `? `, `\n\n`),
   default **1000 tokens with 150 token overlap**, Markdown heading
   tracked across chunks. Chunks > `_EMBED_TOKEN_LIMIT = 8000` are
   truncated with a warning.
2. Each chunk has metadata header injected:
   `"Document: {title} | Source: {source_type} | Path: {clean_path} |
   Section: {heading}"`.
3. Chunk id: `sha256(f"{doc_id}:{chunk_index}").hexdigest()[:32]`.
4. Batch embed via `openai_service.create_embeddings(texts)`.
5. Upsert into the Azure Search index. If embedding failed, the chunk is
   indexed without a vector (keyword still works); admins can re‑index
   later.

### 10.4 Connectors

| Connector | File | ACL strategy | Limits |
| --------- | ---- | ------------ | ------ |
| **SharePoint** | [connectors/sharepoint.py](backend/app/services/connectors/sharepoint.py) | Per‑item permission fetch via Graph; extracts `grantedToV2.user.id` and `.group.id` (Entra OIDs only — never SharePoint local IDs); fail‑closed | 50 MB max; 14 indexable extensions; delta tokens persisted to disk (`/tmp/sp_delta_tokens.json` or `/home/site/wwwroot/...`) |
| **OneDrive** | [connectors/onedrive.py](backend/app/services/connectors/onedrive.py) | `acl_users=[user_id]` (single owner) | App‑only token; same file/extension limits |
| **Org website** | [connectors/org_website.py](backend/app/services/connectors/org_website.py) | Empty ACL (workspace‑public) | `ORG_WEBSITE_ALLOWLIST` domain gate; `ORG_WEBSITE_CRAWL_DEPTH` (default 3); robots.txt honoured |
| **User web (Agent Memory)** | [connectors/user_web_connector.py](backend/app/services/connectors/user_web_connector.py) | Empty ACL (intentionally workspace‑public) | SSRF guard `is_safe_public_url()` blocks loopback, RFC1918, link‑local, multicast, reserved, IPv4‑mapped IPv6 (resolves DNS, checks **all** A records, fail‑closed); robots.txt; sitemap; max 50 pages × 2 MiB × depth 2; per‑user daily quota (default 1000 pages, in‑process counter — **swap for Redis/DB before scaling**) |

All connectors deduplicate via `source_id = sha256("{prefix}:{site}:{item}").hexdigest()[:40]`
and fail‑closed if ACL extraction fails.

### 10.5 Document parsing

[document_service.py](backend/app/services/document_service.py) handles
~40 MIME types locally (DOCX/XLSX/PPTX, ODP/ODS/ODT, PDF, CSV, JSON,
HTML, markdown, code). Optional escalation to
[document_intelligence_service.py](backend/app/services/document_intelligence_service.py)
(Azure AI DI: `prebuilt-read`, `-layout`, `-document`, `-invoice`,
`-receipt`).

**Tabular intelligence** (per [agent_memory_intelligence.md](memory:repo)):
`_extract_csv` / `_extract_xlsx` build a DATA CARD via pandas and persist
the profile to `AgentMemoryItem.template_schema_json =
{"kind":"data_card","profile":...}`. ChatService injects `[DATA_CARD]`
for the top 2 tabular hits (max 30 cols/sheet). The XLSX path uses
openpyxl `iter_rows` (not `pd.read_excel`) because the venv has openpyxl
3.1.2 < required 3.1.5; numeric coercion uses try/except around
`errors='raise'` because `errors='ignore'` was removed in pandas 3.x.

### 10.6 Agent Memory state machine

[agent_memory_service.py](backend/app/services/agent_memory_service.py):

```
pending ──► parsing  ──┐
        └► crawling  ──┴► embedding ──► ready
                                    └─► failed (any exception → safe_process)
```

Background tasks tracked in `_bg_tasks: set[Task]` so the loop's weak
reference doesn't GC them mid‑flight; `_safe_process()` opens a fresh
session and marks `failed` on any escape. `db_available` is a
**module‑level bool**, not a function — call sites must read, not call
(see [agent_memory_indexing_fix.md](memory:repo)).

REST surface in [api/endpoints/agent_memory.py](backend/app/api/endpoints/agent_memory.py)
(prefix `/agent-memory`): upload, web add, list/get/delete, reindex,
session toggle, templates.

### 10.7 Blob storage and file security

- [blob_storage.py](backend/app/services/blob_storage.py): Azure Blob if
  `AZURE_STORAGE_CONNECTION_STRING` set, else local fallback under
  `BLOB_FALLBACK_ROOT` (default `data/blob_fallback/`). Local read/delete
  use `urllib.parse.urlparse + unquote` so legacy `file://` URLs with
  `%20` still resolve.
- [file_security.py](backend/app/services/file_security.py) `scan_file()`
  enforces (in order): max 25 MB, blocks dangerous magic bytes (PE, ELF,
  Mach‑O, `#!`), MIME ↔ magic match, ZIP bomb (200:1 ratio, 200 MB
  uncompressed). `scan_text()` runs ~40 prompt‑injection regex patterns
  for jailbreak phrases, role overrides, `[INST]`, `<|system|>`, "DAN
  mode", etc.

---

## 11. Memory model (3‑layer)

| Layer | Storage | Lifetime | Source |
| ----- | ------- | -------- | ------ |
| **Layer 1 — Long‑term** | `user_memories` table | Persistent until user deletes | `memory_service` extracts preferences/facts from chat |
| **Layer 2 — Session** | `session_memories` table (UNIQUE per conversation) | 6 h sweep (configurable) | Rolling summary + key_facts + goals + entities |
| **Layer 3 — Agent Memory** | `agent_memory_items` table + Azure Search | Until user deletes; per‑session disable supported | User‑uploaded files, crawled websites, templates |

Migrations:

- [001_memory_system.py](backend/alembic/versions/001_memory_system.py)
  creates `user_memories` + `session_memories`.
- [002_agent_memory.py](backend/alembic/versions/002_agent_memory.py)
  creates `agent_memory_items` (22 cols, status enum, unique constraint
  on `(user_id, content_hash)`).

Chat injection order in `chat_service`: `[LONG_TERM_MEMORY]` →
`[SESSION_MEMORY]` → `[AGENT_MEMORY]` → `[TEMPLATE_SCHEMA]` →
`[DATA_CARD]`.

---

## 12. Tools, connectors, and Microsoft Graph

### 12.1 Tool dispatch

`tool_executor` exposes 60+ tools as OpenAI function definitions; the
LLM emits `tool_calls`, the executor runs the matching service, and
results are streamed back as `tool_result` chunks before continuing.

### 12.2 Microsoft Graph integration

- [obo_service.py](backend/app/services/obo_service.py) — MSAL
  `ConfidentialClientApplication`; **app‑only** client‑credentials flow
  with scope `https://graph.microsoft.com/.default`. Token cached 55 min
  in‑process. Warmed at startup.
- The historical OBO (On‑Behalf‑Of) function is now an alias to app‑only
  (`get_graph_token_obo() == get_graph_token_app_only()`).
- [graph_client.py](backend/app/services/connectors/graph_client.py) —
  thin HTTP wrapper. Optional `delegated_token` override is accepted
  but not used in production today.
- **Required Graph permissions** (admin‑consented):
  `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite`, `Tasks.ReadWrite`,
  `Group.Read.All`, `Sites.Read.All`, `Files.Read.All`.

**Implication:** every tool call runs as the enterprise app, not the
user. User‑scoped filtering must happen in Mela's logic
(`UserInfo.id` checks, ACL filters), not via Graph.
**Recommended production addition:** for sensitive operations
(send_email on behalf of user, calendar writes), a real OBO exchange
should be added so audit trails in Microsoft 365 attribute actions to
the user, not the app.

### 12.3 Other tool services

| Tool | File | Notes |
| ---- | ---- | ----- |
| Speech (TTS/STT) | [speech_service.py](backend/app/services/speech_service.py) | Citation text cleanup before synthesis |
| DALL‑E | [dalle_service.py](backend/app/services/dalle_service.py) | Separate Azure resource (`AZURE_DALLE_*`) |
| Translation | [translation_service.py](backend/app/services/translation_service.py) | Azure Translator |
| Document Intelligence | [document_intelligence_service.py](backend/app/services/document_intelligence_service.py) | Optional richer OCR |

---

## 13. Code interpreter and file generation

[code_interpreter_service.py](backend/app/services/code_interpreter_service.py)
runs user/LLM Python in a sandboxed subprocess.

**Sandbox controls:**

- Disallowed imports (regex): `subprocess`, `socket`, `requests`,
  `urllib`, `ftplib`.
- Strips environment variables matching `AZURE_`, `DATABASE_`, etc.
  before exec.
- 60 s timeout, 100 KB stdout cap.
- Whitelisted output extensions only (xlsx, docx, pdf, csv, png, jpg,
  txt, md, json, …).
- Pre‑loaded libs in sandbox: pandas, numpy, matplotlib, fpdf2,
  python‑docx, openpyxl.

**Windows path fix** (per [code_interpreter_fix.md](memory:repo)): the
`_CODE_WRAPPER` template no longer uses a raw‑string prefix and uses
forward slashes (`work_dir.replace("\\", "/")`) so `os.chdir()` doesn't
silently fail on `C:\\\\Users\\\\...` paths.

**Tool integration:** `run_python_code` accepts `memory_item_ids[]`;
`_load_memory_files` fetches blobs via `blob_store.download` and
pre‑loads them as input files in the sandbox working directory.

**Recommended production additions:** per‑user concurrent‑execution
semaphore (today none), per‑user daily quota, Linux user namespace or
Firecracker for true isolation (today: trusted process boundary only).

---

## 14. Streaming protocol (SSE / NDJSON)

`POST /api/v1/chat/completions` with `stream: true` returns NDJSON
(`data: {...}\n` per line; `data: [DONE]` terminator) consumed by
`ApiClient.streamChat()`. Each line is a `StreamChunk`:

| `type` | Payload |
| ------ | ------- |
| `content` | streamed assistant text delta |
| `tool_executing` | `{tool_name, args_preview}` for UI badge ("📧 Reading inbox…") |
| `tool_call` | full function call (internal) |
| `tool_result` | tool output |
| `thinking` | reasoning placeholder for thinking models |
| `model_switched` | router silently changed model |
| `router_resolved` | `{provider, model}` — emitted once |
| `claude_usage` | tokens consumed |
| `claude_limit_reached` | budget exceeded |
| `citation` | RAG citation `{title, url, snippet, source_type}` |
| `file_generated` | `{filename, blob_url, file_type}` |
| `image_generated` | `{url, prompt, model}` |
| `email_draft` | `{to, subject, body}` for AI‑drafted email |
| `worker_event` | `{worker_id, task_id, status, ...}` for live banners |
| `usage` | final token counts |
| `done` | `{conversation_id, model, provider}` |
| `error` | `{message, code}` |

Frontend store handles every type and updates UI accordingly; cancel
via `AbortSignal`.

---

## 15. Data model and migrations

### 15.1 Tables (43 ORM models in `app/models/models.py`)

Highlights — each row is a table; full list in source:

- **Identity:** `User`, `UserSession`, `MCPClient`, `AuditLog`.
- **Conversations:** `Conversation` (with `profile_mode`, `tenant_id`,
  `is_private`, `private_expires_at`, legacy `context_type`,
  `workspace_id`, `project_id`), `Message`, `ChatMember`,
  `GeneratedFileLog`.
- **Projects:** `Project`, `ProjectMember`, `ProjectMemory`.
- **Knowledge:** `Document`, `DocumentChunk`, `AgentMemoryItem`,
  `KnowledgeEntry`.
- **Memory:** `user_memories`, `session_memories` (managed by Alembic
  migration `001`).
- **Customisation:** `Skill`, `SystemInstruction`.
- **Models / quotas:** `ModelUsage`, `ModelRanking` (with
  `cost_multiplier`), `UserBudget`.
- **Orchestration:** `WorkerRegistryEntry`, `OrchestrationTrace`,
  `OrchestrationTask`.

### 15.2 Migrations (Alembic)

| Revision | What it does |
| -------- | ------------ |
| `001_memory_system` | Baseline: `user_memories`, `session_memories` (with profile/tenant cols, indexes, expiry) |
| `002_agent_memory` | Adds `agent_memory_items` (22 cols, status enum, unique `(user_id, content_hash)`) |
| `003_model_cost_multiplier` | Adds `cost_multiplier` to `model_rankings` and back‑fills defaults (e.g. claude‑opus 15.0, claude‑sonnet 5.0, gpt‑5.2 7.5, gpt‑4.1 3.0, haiku 1.0) |

### 15.3 Idempotent SQL baseline

[database/schema.sql](database/schema.sql) is an `IF NOT EXISTS`
T‑SQL baseline for Azure SQL covering 9 core tables (users,
conversations, messages, documents, document_chunks, audit_logs,
model_usage, system_settings, enabled_tools). It exists alongside
SQLAlchemy `create_all()` so a fresh DB can be bootstrapped without
Alembic; `prod_startup.sh` uses `alembic stamp 002_agent_memory` if
schema exists but `alembic_version` is empty, then `alembic upgrade
head`.

---

## 16. Configuration and environment variables

### 16.1 Backend — `app/core/config.py` (Pydantic `BaseSettings`)

Loaded from env files (`env/.env.local`, `env/.env.dev`) and process env.
Selected critical groups:

**Identity / SSO**

```
AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET   # app-only Graph
ENTRA_AUTH_CLIENT_ID, ENTRA_AUTH_CLIENT_SECRET          # SPA login
JWT_SECRET_KEY, JWT_ALGORITHM=HS256, ACCESS_TOKEN_EXPIRE_MINUTES=60
DEV_USERNAME, DEV_PASSWORD, ENABLE_DEV_LOGIN
BOOTSTRAP_ADMIN_EMAILS, BOOTSTRAP_ADMIN_OIDS
```

**LLM providers**

```
AI_FOUNDRY_ENDPOINT, AI_FOUNDRY_API_KEY, AI_FOUNDRY_API_VERSION
AZURE_OPENAI_*  (endpoint, key, version, deployments)
DEPLOYMENT_GPT52_CHAT, DEPLOYMENT_GPT41, DEPLOYMENT_KIMI_K25,
DEPLOYMENT_MISTRAL_LARGE_3, DEPLOYMENT_GROK3_MINI,
DEPLOYMENT_LLAMA4_MAVERICK, DEPLOYMENT_EMBEDDING (text-embedding-3-large)
GPT4O_ENDPOINT, GPT4O_API_KEY, GPT4O_DEPLOYMENT
ANTHROPIC_API_KEY, ANTHROPIC_ENABLED, ANTHROPIC_RPM_LIMIT=20,
ANTHROPIC_MAX_TOKENS_SONNET, ANTHROPIC_MAX_TOKENS_HAIKU,
CLAUDE_DAILY_QUESTION_LIMIT=0, CLAUDE_WARN_AT_REMAINING=2
GOOGLE_AI_API_KEY, GEMINI_ENABLED, GEMINI_MAX_TOKENS=4096
```

**Search / RAG**

```
AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_ADMIN_KEY, AZURE_SEARCH_QUERY_KEY
AZURE_SEARCH_INDEX_NAME=fileshare-documents
AZURE_SEARCH_VECTOR_INDEX_NAME=fileshare-vector-documents
AZURE_SEARCH_CACHE_INDEX_NAME=mela-query-cache
AZURE_SEARCH_KB_INDEX                       # blank → no vector KB
RAG_CHUNK_SIZE=1000, RAG_CHUNK_OVERLAP=200, RAG_TOP_K=5,
RAG_SIMILARITY_THRESHOLD=0.7
```

**Connectors**

```
SHAREPOINT_SITES, ONEDRIVE_ROOT
ORG_WEBSITE_ALLOWLIST, ORG_WEBSITE_CRAWL_DEPTH=3
WEB_SEARCH_ENABLED, WEB_SEARCH_ALLOWLIST
CONNECTOR_{SHAREPOINT,ONEDRIVE,EMAIL,PLANNER,ORG_WEBSITE,PUBLIC_WEB}_ENABLED
SYNC_DELTA_CRON="0 */4 * * *", SYNC_FULL_CRON="0 2 * * 0"
GRAPH_API_ENDPOINT, GRAPH_SCOPES, GRAPH_SENDER_EMAIL,
GRAPH_DEFAULT_PLANNER_PLAN_ID
```

**Storage / Vault / Telemetry**

```
AZURE_STORAGE_CONNECTION_STRING (or BLOB_FALLBACK_ROOT)
AZURE_KEY_VAULT_NAME, AZURE_KEY_VAULT_URL
APPLICATIONINSIGHTS_CONNECTION_STRING
DATABASE_URL                                 # Azure SQL connection string
```

**Orchestration**

```
TASK_RADAR_BASE_URL, TASK_RADAR_MCP_API_KEY, TASK_RADAR_INBOUND_API_KEY
MEETING_ASSISTANT_BASE_URL, MEETING_ASSISTANT_MCP_API_KEY,
MEETING_ASSISTANT_INBOUND_API_KEY
MELA_INGESTION_BASE_URL                      # public callback URL
KB_DEFAULT_EXPIRY_DAYS=30
WORKER_ACCESS_DEFAULT_ALLOW=true
MELA_EMBED_ALLOWED_ORIGINS                   # CSP frame-ancestors
MELA_WORKER_REGISTRATION_KEY                 # shared secret
```

**API / limits / CORS**

```
API_HOST=0.0.0.0, API_PORT=8000, API_PREFIX=/api/v1
CORS_ORIGINS=[...]
RATE_LIMIT_REQUESTS=100, RATE_LIMIT_WINDOW=60
DEFAULT_DAILY_TOKEN_LIMIT=100000
```

### 16.2 Frontend — `frontend/.env.example`

```
NEXT_PUBLIC_ENTRA_AUTH_CLIENT_ID         # SPA app reg
NEXT_PUBLIC_AZURE_AD_CLIENT_ID           # fallback
NEXT_PUBLIC_AZURE_AD_TENANT_ID
NEXT_PUBLIC_REDIRECT_URI                 # http://localhost:3005 in dev
NEXT_PUBLIC_API_SCOPE                    # api://<client-id>/access_as_user
NEXT_PUBLIC_API_URL, NEXT_PUBLIC_API_VERSION=v1
NEXT_PUBLIC_DEV_USERNAME, NEXT_PUBLIC_DEV_PASSWORD
NEXT_PUBLIC_APP_NAME=Mela AI, NEXT_PUBLIC_ORG_NAME=Armely
NEXT_PUBLIC_PRIMARY_COLOR=#2f5597
NEXT_PUBLIC_ENABLE_{VOICE,FILE_UPLOAD,AGENTS,RAG,TRANSLATION,
                    IMAGE_GENERATION,DOCUMENT_INTELLIGENCE}
NEXT_PUBLIC_APP_INSIGHTS_KEY, NEXT_PUBLIC_ENABLE_ANALYTICS
```

### 16.3 Secrets flow

```
Local dev   → env/.env.local        (gitignored)
GitHub CI   → repository secrets    (encrypted)
                ↓ cd.yml writes to Key Vault post-Bicep
Azure KV    → kv-mela-mcpp
                ↓ App Setting "@Microsoft.KeyVault(SecretUri=...)"
App Service → injected as env var at runtime (managed identity reads KV)
```

---

## 17. Deployment, infrastructure, and CI/CD

### 17.1 Bicep stack — `infra/`

Resource‑group‑scoped, used by both `azd up` and the CD pipeline. Modules
under [infra/modules/](infra/modules/):

- **`monitoring.bicep`** — Log Analytics (`PerGB2018`, 30‑day retention)
  + Application Insights (LogAnalytics ingestion mode).
- **`key-vault.bicep`** — standard SKU, RBAC mode, soft‑delete (7 days),
  purge protection. CI/CD service principal gets **KV Secrets Officer**
  (`b86a8fe4-…`) at deploy time.
- **`app-service.bicep`** — Linux plan (`reserved=true`), SKU param
  default `B1` (allowed F1, B1‑B3, S1‑S3, P0v3‑P2v3); two apps:
  - **Backend** (Python 3.12) — system‑assigned MI, HTTPS only, health
    `/health`.
  - **Frontend** (Node 20) — system‑assigned MI, HTTPS only, health via
    `wget` to `:3000`.
  - App settings include `@Microsoft.KeyVault(SecretUri=...)` references
    and the App Insights connection string.
- **`role-assignments.bicep`** — assigns **KV Secrets User**
  (`4633458b-…`) to each app's MI (idempotent name via
  `guid(kvId, principalId, roleId)`).
- **`storage.bicep`** — StorageV2 / Standard_LRS, hot tier, HTTPS only,
  TLS 1.2 min, public blob disabled; containers `documents` + `uploads`
  (private). Conditional on `provisionStorage=true`.

The parallel `infrastructure/bicep/` stack is subscription‑scoped and not
the primary path; treat as legacy/alternative.

### 17.2 azd config — `azure.yaml`

Two services (`backend`/`appservice`/Python, `frontend`/`appservice`/JS,
dist `.next/standalone`); infra path `./infra/main.bicep`; preflight
hooks (`scripts/preflight.{sh,ps1}`) run before provision/deploy to
detect existing resources and emit `useExisting*` flags into the azd
env.

### 17.3 GitHub Actions

**`.github/workflows/ci.yml`** (PRs and non‑main pushes):

- Backend: `ruff check` (rules in [backend/ruff.toml](backend/ruff.toml)),
  `pytest` (excludes live‑prod tests), `pip-audit`, build zip.
- Frontend: `npm ci` → ESLint → `tsc --noEmit` → `next build` (standalone)
  → `npm audit` (continue‑on‑error for known Next 14 advisories) →
  package zip.

**`.github/workflows/cd.yml`** (push to `main` or manual dispatch):

1. **OIDC login** (`id-token: write`) — no client secret needed.
2. **Build** backend.zip + frontend.zip artefacts.
3. **Preflight** writes `.azure/armely-dev/preflight.env`; values exported
   into `$GITHUB_ENV`.
4. **Bicep deploy** (`az deployment group create`) with
   `useExisting*` and `deployPrincipalObjectId` parameters.
5. **Wait 60 s** for RBAC propagation.
6. **Write secrets** to Key Vault (jwt-secret-key, azure-client-secret,
   ai-foundry-api-key, azure-openai-api-key, azure-speech-key,
   azure-dalle-api-key, azure-search-admin-key,
   azure-storage-account-key, anthropic-api-key, gpt4o-api-key,
   azure-translator-key, database-url) — `continue-on-error: true`.
7. **Configure App Settings** (`APP_ENV=production`, `DEBUG=false`,
   `ENABLE_DEV_LOGIN=false` on `main`).
8. **Deploy backend** via `az webapp deployment source config-zip` (Kudu
   ZipDeploy with retries).
9. **Deploy frontend** the same way (or OneDeploy).
10. **Health check gate** — `curl /health` must return 200 or the
    workflow fails.

**`.github/workflows/security.yml`** (PRs, push, weekly Mon 06:00 UTC):

- Gitleaks (secrets), Bandit (Python SAST, severity HIGH + confidence
  MEDIUM, skips B101/B404/B603), pip-audit (continue‑on‑error).

### 17.4 Required GitHub secrets and variables

See [README.md](README.md) for the full table; secrets cover JWT,
client secret, AI Foundry, Speech, DALL‑E, Search, Storage,
Anthropic/GPT4o, Translator, Database, Dev password. Variables
cover OIDC IDs (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`), the SPA app reg (`AZURE_AD_CLIENT_ID`),
endpoints (`AI_FOUNDRY_ENDPOINT`), `FRONTEND_URL` (optional override),
`NEXT_PUBLIC_API_SCOPE`, and `DEV_USERNAME`.

### 17.5 Local dev

```powershell
# Backend
cd backend
python -m venv venv ; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend
cd frontend
npm install
npm run dev      # http://localhost:3005
```

`docker-compose.yml` boots both with bridge network and health‑check
gating.

### 17.6 Production startup script

`prod_startup.sh` (and `backend/startup.sh`):

1. Compute MD5 of `requirements*.txt`; only `pip install --user` if it
   changed (cached under `~/.local`).
2. Detect existing schema with empty `alembic_version` → `alembic stamp
   002_agent_memory` to avoid re‑running early revisions.
3. `alembic upgrade head`.
4. Hand off to gunicorn/uvicorn (App Service start command).

---

## 18. Observability and logging

- **Structured logging** in [app/core/logging.py](backend/app/core/logging.py)
  with `_SecretRedactFilter` that scrubs `token=…`, `key=…`, etc. patterns
  from log records. Ensure attached to all handlers (verified by
  security review §20).
- **Correlation ID** (12‑char hex) generated per chat request in
  `chat_service` (~L1072) and threaded through tool calls and search.
- **Request logs** at WARNING+ only or for >500 ms responses (suppresses
  the `/health` flood).
- **Application Insights** connection string injected into App Service
  via Bicep; Python SDK auto‑instrumentation is **not confirmed in the
  current codebase** — recommended addition.
- **Agent memory state transitions** logged at INFO with prefix
  `agent_memory.transition item=... status=...`
  (per [agent_memory_feature.md](memory:repo)).

---

## 19. Testing strategy

- **Unit/integration:** `backend/tests/` (pytest). Examples:
  `test_agent_memory_acl.py`, `test_agent_memory_indexing.py`,
  `test_agent_memory_ssrf.py`, `test_template_engine.py`,
  `test_collaboration.py`, `test_document_data_card.py`,
  `test_enterprise_knowledge.py`, `test_gemini_provider.py`,
  `test_live_prod.py` (excluded from CI).
- **Test patterns** that work in this repo (per memory):
  - `StaticPool` + single shared connection so a service that opens its
    own `async_session_maker()` sees rows the test created.
  - Patch `app.core.database.async_session_maker` with an
    `@asynccontextmanager`.
  - Patch `app.services.agent_memory_service._blob_store` (the imported
    binding, not the source module).
  - Patch `app.services.file_security.scan_file` to a fake `Safe()` so
    malformed test fixtures don't trip the security gate.
- **Frontend:** Jest (`npm test`) + `tsc --noEmit` (CI blocking) +
  ESLint.
- **Quick gates before commit:** `ruff check app/` and `pytest tests/`
  in backend; `npm run lint && npx tsc --noEmit` in frontend.
- **E2E harnesses at repo root:** `comprehensive_e2e_test.py`,
  `prod_test_suite.py`, `test_conversation_flow.py`, `test_claude.py`,
  `test_api.py`, `test_ci.py`. These hit the live deployment — use
  with care.

---

## 20. Security review (OWASP‑style)

This section is the **as‑of‑today** state of the repository. Items marked
**fixed/positive** are evidenced in code; items marked **gap** require
remediation before the next sensitive deployment.

### 20.1 Critical strengths (positive findings)

- ✅ **SSRF defence in `user_web_connector`** — blocks loopback, RFC1918,
  link‑local, multicast, reserved, IPv4‑mapped IPv6; resolves DNS and
  re‑validates *all* A records; fail‑closed.
- ✅ **Code interpreter sandbox** — disallowed module regexes, env
  redaction (AZURE_/DATABASE_/…), 60 s timeout, 100 KB stdout cap,
  whitelisted output extensions.
- ✅ **File security scan** — magic‑byte block list, MIME ↔ magic
  match, ZIP bomb (200:1, 200 MB), prompt‑injection text patterns.
- ✅ **Search ACL filter** — fail‑closed connectors (no empty ACL on
  permission failure); `workspace_id` always co‑applied.
- ✅ **MSAL singleton + Strict Mode safety** in
  [`MsalProvider.tsx`](frontend/src/components/providers/MsalProvider.tsx).
- ✅ **Markdown rendering** uses `react-markdown` with default
  sanitisation; no `dangerouslySetInnerHTML` found.
- ✅ **Worker callback auth** uses `hmac.compare_digest` (timing‑safe).
- ✅ **Logging redaction filter** in
  [`app/core/logging.py`](backend/app/core/logging.py).
- ✅ **JWT validation** uses RS256 + JWKS + `kid` rotation + tenant
  `tid` enforcement.

### 20.2 High‑severity gaps

| # | Finding | File | Fix |
| - | ------- | ---- | --- |
| H‑1 | **JWT issuer not validated** (intentional, documented) | [app/core/security.py](backend/app/core/security.py#L99) | Manually validate `iss` post‑decode against both v1 (`https://sts.windows.net/{tid}/`) and v2 (`https://login.microsoftonline.com/{tid}/v2.0`) |
| H‑2 | **Admin queries lack tenant filter** (`/admin/stats`, `/admin/analytics`) | [admin.py](backend/app/api/endpoints/admin.py#L199) | Add `.where(*.tenant_id == current_user.tenant_id)` everywhere; integration test cross‑tenant isolation |
| H‑3 | **Search cache hash omits tenant_id** | [query_pipeline.py](backend/app/services/search/query_pipeline.py#L122) | Prefix `tenant_id|` to the hash key |
| H‑4 | **`org_context_service._cache` global, keyed only by user_id** | [org_context_service.py](backend/app/services/org_context_service.py#L25) | Re‑key to `f"{tenant_id}|{user_id}"` |
| H‑5 | **No post‑filter ACL check after search** — relies entirely on index‑time ACL | [query_pipeline.py](backend/app/services/search/query_pipeline.py) | Add DB‑side trim; reconcile stale index entries periodically |
| H‑6 | **Dev login bypass risk if `ENABLE_DEV_LOGIN=true` in prod** | [auth.py](backend/app/api/endpoints/auth.py#L287) | Add startup assertion: `if APP_ENV=='production' and ENABLE_DEV_LOGIN: raise RuntimeError(...)` |
| H‑7 | **Audit log coverage incomplete** — no entries for failed login, token validation failures, role changes (besides bootstrap), file deletion, search queries, API key issue/revoke | [models.py](backend/app/models/models.py#L199), various | Wrap a `log_security_event()` helper and call it from every auth/admin/file path |
| H‑8 | **Bootstrap elevation re‑checks not logged when user is already admin** | [admin.py](backend/app/api/endpoints/admin.py#L100) | Always emit a `bootstrap_admin_check_triggered` audit row |

### 20.3 Medium‑severity gaps

| # | Finding | Fix |
| - | ------- | --- |
| M‑1 | Hardcoded `JWT_SECRET_KEY: "ci-test-key-not-for-production"` in CI workflows | Move to GitHub secret even for CI; reject literal in workflow file via lint |
| M‑2 | Localhost origins included in `CORS_ORIGINS` of `.env.dev` and `.env.example` | Strip `localhost` from any prod env file; add `field_validator` rejecting localhost when `APP_ENV=='production'` |
| M‑3 | `/admin/me` is callable by any authenticated user (returns admin status) — enables enumeration | Rate‑limit + audit, or restrict to known admin emails |
| M‑4 | No per‑user daily upload quota (per‑file 25 MB only) | Sum `Document.file_size` per user per day; reject if > `DAILY_UPLOAD_LIMIT` |
| M‑5 | No antivirus scan on uploads (only magic byte / structure checks) | ClamAV / Defender / Microsoft Purview; or store in quarantine and scan async |
| M‑6 | No per‑endpoint rate limit on `/chat/completions` and code interpreter | `slowapi` per‑user, or semaphore for code exec |
| M‑7 | All Graph operations run as enterprise app (no real OBO) | Implement OBO for `send_email`, calendar writes so M365 audit attributes the user |
| M‑8 | `python-jose 3.3.0` and `PyJWT 2.8.0` somewhat dated | Upgrade and re‑run `pip-audit`; consider migrating to `pyjwt[crypto]` only |
| M‑9 | Web crawler quota in‑process only — resets on restart, doesn't share across instances | Move to DB or Redis |
| M‑10 | Budget warnings notify but only block when `hard_stop=true` | Document and surface in admin settings |

### 20.4 Low‑severity / observational

- Dev login docs explicit; risk acceptable while gated.
- CSP could be tighter on the frontend (add `Content-Security-Policy`
  header in `next.config.js` headers list).
- `frontend/tmpclaude-*-cwd` directories appear to be Claude scratch
  folders — ensure they're in `.gitignore`.
- Several frontend admin panels (Invoices, Onboarding, Offboarding) read
  as UI shells; verify or disable until backend is implemented.

### 20.5 30‑day priority order

1. H‑6 (assert `ENABLE_DEV_LOGIN=false` in prod).
2. H‑1 (issuer validation).
3. H‑2/H‑3/H‑4 (tenant scoping in admin queries, search cache,
   org_context cache).
4. H‑5 (post‑filter ACL).
5. H‑7/H‑8 (audit log coverage).
6. M‑6 (rate limit chat + code interpreter).
7. M‑4/M‑5 (upload quota + AV scan).
8. M‑8 (dependency updates).
9. M‑2 (CORS allow‑list hardening).

---

## 21. How to rebuild Mela AI from scratch

The following sequence reproduces the platform as a green‑field project
for a new client tenant.

### 21.1 Azure resource prerequisites

Provisioned by `infra/main.bicep` (you can run via `azd up` or `az
deployment group create`):

1. Resource group (`rg-{client}-mela`) in target region.
2. App Service Plan Linux (`B1` minimum for prod, `F1` for dev).
3. Two App Services (backend Python 3.12, frontend Node 20) with
   system‑assigned managed identities.
4. Key Vault (RBAC mode, soft‑delete, purge protection).
5. Log Analytics + Application Insights.
6. Storage account (StorageV2, private blob, containers
   `documents`/`uploads`) — optional, can fall back to local disk.
7. Azure SQL (Basic+) with the schema from `database/schema.sql`. SQLite
   acceptable for dev only.
8. Azure AI Search (Basic+ for production; Free for dev) — create the
   `fileshare-vector-documents` and `mela-query-cache` indexes via
   `index_manager.ensure_*_index()` on first boot.
9. Azure OpenAI / AI Foundry resource with deployments for at least:
   - `gpt-5.2-chat` (or `gpt-4.1` as default)
   - `text-embedding-3-large`
10. Optional providers/services: separate GPT‑4o resource; DALL‑E
    deployment; Azure Speech; Azure Translator; Document Intelligence;
    Anthropic API key; Google AI Studio key.

### 21.2 Microsoft Entra app registrations

Two app registrations are recommended:

1. **Backend / API** (`api://{backend-client-id}`)
   - Expose API → scope `access_as_user`.
   - App Roles → `Admin` and `User` (App Roles delivered as `roles[]` in
     the token).
   - API permissions (admin‑consented):
     `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite`,
     `Tasks.ReadWrite`, `Group.Read.All`, `Sites.Read.All`,
     `Files.Read.All`, plus delegated `User.Read`.
2. **SPA / login** — type SPA, redirect URIs:
   - `http://localhost:3005` (dev)
   - `https://{frontend-domain}` (prod, including any future custom
     domain).
   - API permissions: delegated `User.Read`, `profile`, `email`, plus
     `api://{backend-client-id}/access_as_user`.

Set `BOOTSTRAP_ADMIN_EMAILS` (comma‑separated) so the first sign‑in by
those identities elevates them to `ADMIN`.

### 21.3 Secrets to inject

Populate **GitHub Actions secrets** (CI/CD source of truth) with all
items in §16; `cd.yml` writes them into Key Vault and the App Service
references them via `@Microsoft.KeyVault(SecretUri=…)`.

### 21.4 First deploy

```bash
# One-command provision + deploy
./scripts/azd-up.sh   # or .ps1 on Windows

# Or via GitHub Actions
gh workflow run cd.yml
```

The CD pipeline:

1. Builds + lints + tests.
2. Detects existing resources (preflight).
3. Provisions Bicep stack.
4. Waits 60 s for RBAC.
5. Writes secrets to KV.
6. Configures app settings.
7. Kudu ZipDeploy backend + frontend.
8. Health‑check gate on `/health`.

### 21.5 Post‑deploy validation

Run [docs/validation-checklist.md](docs/validation-checklist.md) (if
present) and the smoke E2E tests:

```bash
python prod_test_suite.py
python comprehensive_e2e_test.py
```

Confirm:

- `/health` returns 200.
- A login round‑trip yields a valid bearer token.
- A chat completion streams content + a `router_resolved` chunk.
- An Agent Memory upload reaches `status=ready` and the next chat
  surfaces `[AGENT_MEMORY]` content with citations.

---

## 22. How to extend it (new tool, connector, model, tab)

### 22.1 Add a new LLM tool

1. Implement the action as an async function in a service under
   `backend/app/services/`.
2. Register the OpenAI function definition in
   [tool_executor.py](backend/app/agents/tool_executor.py) (`tools[]`).
3. Add the dispatch case mapping the function name to the service call.
4. If personal mode shouldn't see it, add the name to
   `_PERSONAL_BLOCKED_TOOLS`.
5. Add tests under `backend/tests/` patterned on
   `test_agent_memory_indexing.py` (StaticPool, patched bindings).
6. Update the system prompt in [chat_service.py](backend/app/services/chat_service.py)
   only if the model needs guidance on when to call it.

### 22.2 Add a new connector

1. Subclass the connector pattern under
   `backend/app/services/connectors/` — implement `sync()` returning
   chunked documents with `acl_users` / `acl_groups`.
2. Fail‑closed if permission fetch fails (no empty ACL).
3. Wire into the ingestion worker in
   [main.py](backend/app/main.py) lifespan.
4. Add a feature flag (`CONNECTOR_<NAME>_ENABLED`) and (if user‑facing)
   a card to
   [AddKnowledgeModal.tsx](frontend/src/components/settings/AddKnowledgeModal.tsx).

### 22.3 Add a new model / provider

1. Create a service file mirroring
   [anthropic_service.py](backend/app/services/anthropic_service.py)
   (singleton, `stream_chat`, OpenAI‑schema input, `_with_retry`,
   provider‑specific rate limit if any).
2. Wire into [model_router.py](backend/app/services/model_router.py)
   selector and the failover chain.
3. Add the model id + cost multiplier to the
   `003_model_cost_multiplier` back‑fill (or a new migration).
4. Surface in the Models tab and in `ModelInsightsPanel`.

### 22.4 Add a new settings tab

1. Build the React component under
   `frontend/src/components/settings/`.
2. Register its tab id + icon + role gate in
   [SettingsModal.tsx](frontend/src/components/settings/SettingsModal.tsx).
3. Type the new API surface in
   [src/lib/api.ts](frontend/src/lib/api.ts) (request/response
   interfaces + ApiClient methods).
4. Add a feature flag in the backend's `/user/features` response so the
   tab is visible only when enabled.

---

## 23. How to rebrand and white‑label for clients

Mela's branding surface is intentionally narrow and env‑driven, so a
single repo can serve many clients.

### 23.1 Cosmetic rebrand

- **App name + org name:** set `NEXT_PUBLIC_APP_NAME` and
  `NEXT_PUBLIC_ORG_NAME` in the build environment (CI uses the GitHub
  variable `FRONTEND_URL` and the env file).
- **Primary colour:** override `NEXT_PUBLIC_PRIMARY_COLOR` and the
  `primary` palette in [tailwind.config.ts](frontend/tailwind.config.ts);
  the CSS variables in `globals.css` resolve from there.
- **Logo / favicon:** drop replacements into
  [frontend/public/](frontend/public/) (`mela-logo.png`, `favicon.ico`).
  For per‑tenant logos served at runtime, host them on the backend's
  blob and read via `/user/features`.
- **Custom domain:** point a CNAME at the frontend App Service, register
  the new redirect URI in the Entra app, set the GitHub variable
  `FRONTEND_URL` so the Next build bakes the right base URL, then re‑run
  CD. No code change required.

### 23.2 Functional rebrand (per tenant)

- Toggle features via `userFeatures.feature_flags` from
  `GET /user/features` (`private_chat_enabled`, `voice_mode_enabled`,
  `web_search_enabled`, `project_collaboration_enabled`,
  `agent_memory_enabled`, `workflows_enabled`,
  `mcp_integration_enabled`).
- Set per‑tenant model defaults via `Admin → Models` (writes
  `model_rankings`).
- Set per‑user / per‑tenant token + cost budgets via `UserBudget`
  (admin Settings tab).
- Restrict allowed tools per role in the `enabled_tools` table
  (`/admin/tools`).

### 23.3 Identity rebrand (single‑tenant deployments)

- Replace `AZURE_TENANT_ID`, the SPA + API app registrations, and
  `BOOTSTRAP_ADMIN_EMAILS`; the rest of the platform follows.

---

## 24. Productionisation checklist

A fast self‑check before a client go‑live:

**Identity & auth**

- [ ] Two Entra app registrations (SPA + API) with App Roles (`Admin`,
  `User`) and admin‑consented Graph permissions.
- [ ] `JWT_SECRET_KEY` ≥ 32 chars, rotated, stored only in Key Vault.
- [ ] `ENABLE_DEV_LOGIN=false`, `APP_ENV=production`, `DEBUG=false`.
- [ ] Issuer validation patched in (H‑1).
- [ ] `BOOTSTRAP_ADMIN_EMAILS`/`OIDS` populated and audit‑logged.

**Tenancy**

- [ ] Admin/analytics queries filter by `tenant_id` (H‑2).
- [ ] Search cache key includes `tenant_id` (H‑3).
- [ ] `org_context_service._cache` keyed by tenant + user (H‑4).
- [ ] Post‑filter ACL trim added to search results (H‑5).

**RAG / data**

- [ ] At least one connector configured and a successful first sync.
- [ ] Index `fileshare-vector-documents` exists with HNSW profile and
  3072‑dim vectors.
- [ ] Cache index `mela-query-cache` exists.
- [ ] Embeddings model deployed (`text-embedding-3-large`).
- [ ] Connector ACL extraction tested against a permission‑restricted
  document; verify it does **not** appear in another user's results.

**Operations**

- [ ] Application Insights linked and receiving telemetry.
- [ ] Log‑level redaction filter attached to all handlers.
- [ ] Background jobs visible (SharePoint sync, OneDrive refresh, KB
  expiry).
- [ ] Health check returns 200 from both apps.

**Security**

- [ ] CORS strips `localhost` (M‑2).
- [ ] Rate limit on `/chat/completions` and code interpreter (M‑6).
- [ ] Per‑user daily upload quota (M‑4).
- [ ] AV scan or quarantine path for uploads (M‑5).
- [ ] Dependencies refreshed; `pip-audit` and `npm audit` clean (or
  triaged).
- [ ] Audit log writes for all auth/admin/file events (H‑7).
- [ ] Worker callbacks gated by `X-Worker-Id` + `X-Worker-Api-Key`
  with `hmac.compare_digest`.

**Quality**

- [ ] CI green: `ruff`, `pytest`, ESLint, `tsc --noEmit`, `next build`.
- [ ] E2E suite (`prod_test_suite.py`) green against staging.
- [ ] Backup + restore tested for SQL and Search index.

---

## 25. Glossary

- **AAD / Entra ID** — Microsoft identity platform; issues OAuth2/OIDC
  tokens consumed by Mela.
- **Agent Memory** — user‑curated knowledge layer (uploads, websites,
  templates) with personal/workspace/tenant scopes; tagged
  `knowledge`/`template`/`brand`/`policy`/`demo`.
- **ACL (in this codebase)** — pair of `acl_users` (Entra OIDs) and
  `acl_groups` (Entra group OIDs) on each indexed Search document.
- **azd** — Azure Developer CLI; orchestrates `azure.yaml` + Bicep +
  service deploy.
- **Citation** — `SourceRecord.to_citation_dict()` output streamed to
  the UI as `chunk.type='citation'`.
- **DATA CARD** — pandas‑derived summary (shape + per‑column
  type/non‑null/min/max/sample) prepended to tabular content for LLM
  reasoning.
- **HNSW** — Hierarchical Navigable Small World, the vector index
  algorithm used by Azure AI Search.
- **MCP** — Model Context Protocol; Mela exposes its own tools via MCP
  and accepts MCP workers as tool providers.
- **MSAL** — Microsoft Authentication Library; SPA token flow.
- **OBO** — On‑Behalf‑Of OAuth2 flow; aliased to app‑only today.
- **Profile** — `personal` (no enterprise data, no Graph tools) vs
  `work` (full enterprise grounding, requires `tenant_id`).
- **Router resolved** — single SSE chunk emitted by `model_router`
  telling the UI which provider/model actually answered.
- **Workspace ID** — the search‑side scope key: `tenant_id` for org
  content, `f"user:{user.id}"` for personal Agent Memory.

---

### Appendix A — file pointers cheat sheet

| You want to … | Read this |
| ------------- | --------- |
| Trace a chat request end‑to‑end | [backend/app/services/chat_service.py](backend/app/services/chat_service.py) |
| See every router | [backend/app/api/router.py](backend/app/api/router.py) |
| Add a tool | [backend/app/agents/tool_executor.py](backend/app/agents/tool_executor.py) |
| Change provider behaviour | [backend/app/services/model_router.py](backend/app/services/model_router.py) |
| Inspect search filtering | [backend/app/services/search/query_pipeline.py](backend/app/services/search/query_pipeline.py) |
| Inspect index schema | [backend/app/services/search/index_manager.py](backend/app/services/search/index_manager.py) |
| Inspect Agent Memory pipeline | [backend/app/services/agent_memory_service.py](backend/app/services/agent_memory_service.py) |
| Validate a token / add an auth gate | [backend/app/core/security.py](backend/app/core/security.py) |
| Add a setting | [backend/app/core/config.py](backend/app/core/config.py) + (if frontend) [frontend/src/lib/api.ts](frontend/src/lib/api.ts) |
| Tweak streaming UI | [frontend/src/lib/store.ts](frontend/src/lib/store.ts) `sendMessage` |
| Add a settings tab | [frontend/src/components/settings/SettingsModal.tsx](frontend/src/components/settings/SettingsModal.tsx) |
| Change branding | [frontend/next.config.js](frontend/next.config.js), [frontend/tailwind.config.ts](frontend/tailwind.config.ts), [frontend/public/](frontend/public/) |
| Touch infrastructure | [infra/main.bicep](infra/main.bicep) + modules |
| Touch CI/CD | [.github/workflows/cd.yml](.github/workflows/cd.yml) |
| Run E2E | `python prod_test_suite.py`, `python comprehensive_e2e_test.py` |

---

*End of `mela.md` v1.0 — Generated 2026‑05‑12 from a full repository
inspection. Update this file when you change auth flows, tenancy
boundaries, the orchestration contract, or the ACL filter.*
