# Architecture

## Components

| # | Component | Path | Notes |
|---|-----------|------|-------|
| 1 | Web frontend | `apps/web` | Next.js 14 App Router, TS, Tailwind |
| 2 | Backend API | `apps/api/app/main.py` | FastAPI |
| 3 | Worker | `apps/api/app/workers/worker.py` | Pluggable queue (memory/Service Bus) |
| 4 | Scheduler | `apps/api/app/scheduler/scheduler.py` | APScheduler, daily 7am per user |
| 5 | Graph service | `apps/api/app/services/graph/` | Outlook, Teams, Excel, Planner |
| 6 | AI extraction | `apps/api/app/services/ai/` | GPT-5.2 + JSON validation/repair |
| 7 | Excel sync | `apps/api/app/services/excel/` | TaskInbox.xlsx via Graph |
| 8 | Planner sync | `apps/api/app/services/planner/` | Approval-first |
| 9 | Attachment archive | `apps/api/app/services/storage/` | Blob or local adapter |
| 10 | MCP server | `apps/api/app/mcp/server.py` | Wraps API as MCP tools |
| 11 | Migrations | `apps/api/alembic/` | Alembic |

## Data flow

```
Outlook/Teams ──► Graph ──► Normalize ──► Noise filter ──► Dedup
                                                       │
                                                       ▼
                                         GPT-5.2 task extraction
                                                       │
                                                       ▼
                                          Tasks DB (source of truth)
                                          │            │
                                          ▼            ▼
                                    Excel sync   Planner sync (approval)
                                          │
                                          ▼
                                    Mela AI / UI / MCP
```

## Tenancy

Every row is keyed by `tenant_id` and `user_id`. Every query filters by both.
A `RequestContext` dependency injected into routers carries the authenticated
identity and is used by all services.

## Tokens

Tokens are encrypted at rest via Fernet (`TOKEN_ENCRYPTION_KEY`) and stored
through a `TokenStore` interface. Production should use Key Vault references —
see `services/auth/token_store.py`.

## Queueing

`services/queue/` defines a `Queue` interface with two implementations:
`InMemoryQueue` (dev) and `ServiceBusQueue` (prod). Switch with `QUEUE_PROVIDER`.

## MVP exclusions (intentionally not built)

- Semantic deduplication
- Teams private chat scanning
- Realtime Graph webhooks (seam in `services/graph/webhooks.py`)
- Jira / ClickUp connectors (sync architecture is generic)
