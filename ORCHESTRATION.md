# Mela Orchestration Brain

Mela is an air traffic controller, not a pilot. It coordinates a network of independent worker apps without depending on any of them. This document is the canonical reference for the orchestration layer — the system spanning Phases 1–6 that turns Mela from "a chat app" into "infrastructure other apps plug into."

If you are joining the project and need to work on any orchestration code, read this file in full once. It is not exhaustive — code is the source of truth — but it is enough to make changes without breaking the system's invariants.

---

## The cardinal rule

> Workers never depend on Mela to function. Workers run fully independently. Mela observes, commands, aggregates, and reasons. Workers execute, persist, and report. If Mela goes down, every worker keeps running. If a worker goes down, Mela degrades gracefully.

Every architectural decision below either upholds this rule or is a deliberate tradeoff against it.

---

## Six phases, one system

```
Inbound users                   Outbound workers
     │                                │
     │  chat / embed                  │  capability calls
     ▼                                ▼
  ┌──────────┐    plan    ┌─────────────────┐
  │ outcome  │  ─────────▶│ orch. planner   │  (Phase 3 — LLM)
  │ orchest. │            └────────┬────────┘
  └────┬─────┘                     │
       │ tool call dispatch        ▼
       │            ┌──────────────────────────┐
       │            │  orchestration/executor  │  (Phase 2)
       │            └────────┬─────────────────┘
       │                     │ batches → asyncio.gather
       ▼                     ▼
  ┌──────────────┐   ┌───────────────────┐
  │ tool_bridge  │──▶│ orchestration/    │  Phase 1: registry + breaker
  │ (synthesises │   │   router          │  Phase 2: trace/task persistence
  │  worker tools│   └────────┬──────────┘  Phase 5C: tenant access gate
  │  for the LLM)│            │
  └──────────────┘            ▼
                     ┌─────────────────┐
                     │ MCPAdapter      │  Phase 1+2: per-protocol adapter
                     │ (HTTP/MCP)      │  Phase 1: circuit breaker per worker
                     └────────┬────────┘
                              │ MelaTask  (always returns MelaResult)
                              ▼
                  ┌─────────────────────────┐
                  │  Worker (Task Radar,    │
                  │  Meeting Assistant, …)  │
                  └────────────┬────────────┘
                               │ async result via /api/v1/ingest/result
                               │ events    via /api/v1/ingest/event
                               ▼
                  ┌─────────────────────────┐
                  │ ingest endpoints        │  Phase 2: store + KB write
                  │   → KnowledgeStore      │  Phase 3+4: summarise + index
                  │   → notification        │  Phase 5A: bus.publish(user)
                  │   → event_bus           │
                  └─────────────────────────┘
                               │
                               ▼
              SSE: /orchestration/events/stream  ◀── browser tabs subscribe
```

External callers and embed surface (Phase 6):

```
                ┌─────────────────────────────┐
                │  Mela MCP server /mcp/v1    │  Phase 6A
                │   • mela_chat               │  X-Api-Key (bcrypt-hashed)
                │   • mela_search_knowledge   │  per-client scopes
                │   • mela_get_worker_status  │
                │   • mela_trigger_plan       │
                │   • mela_get_trace_status   │
                │   • mela_ingest_context     │
                └──────────┬──────────────────┘
                           │ external app calls into Mela
                           ▼
                ┌─────────────────────────────┐
                │  Mela /embed page (iframe)  │  Phase 6B
                │   • <mela-chat> web         │  embed-token JWT, 1h
                │     component + bundle      │  scope ⊆ MCP client scope
                └─────────────────────────────┘

External worker that wants to register itself:

   Worker  ──POST /orchestration/register──▶  Mela
   Worker  ◀──── inbound_api_key ────────  Mela
   Worker  ──POST /api/v1/ingest/result──▶  Mela
                       (worker is now in the registry, fully orchestrable)
```

---

## What the layers own

### Phase 1 — Foundation

`backend/app/orchestration/`

| Module | Owns |
|---|---|
| `types.py` | `MelaTask`, `MelaResult`, `WorkerManifest`, `Capability`, `MelaContext`, `Priority`, `Protocol`, `WorkerStatus`. **Only types that cross adapter boundaries.** No worker-specific shapes leak past these. |
| `registry.py` | Source of truth for what Mela can orchestrate. SQLAlchemy `WorkerRegistryEntry` table; in-process cache w/ 60s TTL behind `asyncio.Lock`. Module singleton `worker_registry`. |
| `breaker.py` | Per-worker circuit breaker (CLOSED → OPEN → HALF_OPEN → CLOSED). `BreakerStore` ABC + `InMemoryBreakerStore`. **Adapters call `breaker.allow()` before each request, `record_success` / `record_failure` after.** Redis-backed swap is one new file. |
| `adapters/base.py` | `WorkerAdapter` ABC. `execute(MelaTask) → MelaResult`, **never raises**. Wraps every dispatch with breaker check + retry-policy from manifest (max 2 attempts). |
| `adapters/task_radar.py` | `MCPAdapter` (canonical) + `TaskRadarAdapter` alias. Any worker with `protocol="mcp"` is served by it. Always overlays explicit `user_id` / `tenant_id` from `MelaContext`. |
| `adapters/factory.py` | `AdapterFactory` keyed on `manifest.protocol`. Per-process cache invalidated by manifest signature. **Adding a new MCP worker requires zero adapter code.** |
| `health.py` | `get_worker_health_summary(db)`. Combines registry status with current breaker state. `UNCONFIGURED` is sticky — breaker has no opinion on a worker we never tried to call. |
| `seed.py` | Idempotent at startup. Builds Task Radar + Meeting Assistant manifests from env vars, upserts into the registry. |
| `api/endpoints/orchestration.py` | Admin: `GET /registry`, `PUT /registry/{id}`, `DELETE /registry/{id}`, `GET /health`. |

### Phase 2 — Routing, execution, ingestion

| Module | Owns |
|---|---|
| `orchestration/store.py` | `OrchestrationStore` ABC + `InMemoryOrchestrationStore`. Pending-task registry for async callbacks. `register / complete / wait_for_result(timeout)`. Redis-Pub/Sub swap is one file. |
| `orchestration/router.py` | `Router.route(db, task) → WorkerAdapter | RouteFailure`. Validates worker + capability + tenant access (Phase 5C); **does NOT consult the breaker** (adapter base does that). Never raises. Failure codes: `UNKNOWN_WORKER`, `UNKNOWN_CAPABILITY`, `WORKER_ACCESS_DENIED`, `ADAPTER_UNAVAILABLE`. |
| `orchestration/executor.py` | `Executor.run_single(task)` and `Executor.run_plan(plan)`. Sequential batches, parallel within each via `asyncio.gather(..., return_exceptions=True)`. Persists `OrchestrationTrace` + `OrchestrationTask` rows around every call. **No retry layer here** — adapter retries + breaker are the policy. |
| `orchestration/auth.py` | `require_worker_api_key` FastAPI dep — validates worker callbacks against `manifest.auth_config["inbound_api_key"]` with `hmac.compare_digest`. |
| `orchestration/tool_bridge.py` | Synthesises OpenAI tool-function defs from registered worker capabilities. Tool-name format: `worker__<slug>__<capability>`. Filters by personal/work scope (Phase 2) + tenant access (Phase 5C). |
| `api/endpoints/orchestration_ingest.py` | Worker callbacks: `POST /api/v1/ingest/result`, `POST /api/v1/ingest/event`, `GET /api/v1/ingest/status/{trace_id}`. Best-effort everything; never blocks the worker. |
| `agents/tool_executor.py` | Augmented (additively) — built-in tools unchanged; new branch routes `worker__*` tool calls through `tool_bridge.dispatch_worker_tool`. |
| `services/chat_service.py` | Single edit: passes `trace_id=_corr_id` into `tool_executor.execute_tool` so worker calls inherit the request's correlation ID. |

### Phase 3 — Knowledge Base, Planner, cross-worker chat

| Module | Owns |
|---|---|
| `orchestration/knowledge.py` | `KnowledgeStore` ABC + `SQLKnowledgeStore`. `ingest / search / get / expire / expire_stale / stats`. `KB_EXPIRY_DAYS_BY_TYPE` per-type policy. `summarise_if_needed(text)` — calls `gpt-4o-mini` only when text > 500 chars; truncates on failure. |
| `services/orchestration_planner.py` | `OrchestrationPlanner.plan(goal, ctx, db) → AnnotatedPlan | PlanningFailure`. Calls `openai_service.create_completion` (NOT `model_router.stream`) for structured JSON output. Hard guards in code (not in prompt): no workers → fail fast no LLM call; > 10 batches → `PLAN_TOO_COMPLEX`; unknown capabilities stripped + reported in `warnings`; `resolvable=false` → `UNRESOLVABLE`; > 45s estimated → `slow_plan=True` flag. |
| `services/outcome_orchestrator.py` | New `IntentType.CROSS_WORKER`; new `_run_cross_worker` branch in `run()`. Plans → executes → synthesises one coherent answer. **Falls through silently to the standard chat path on any planner failure.** |
| `services/chat_service.py` | New KB-context injection block in work mode only — `[KNOWLEDGE_CONTEXT]` system-prompt section with past goal/task/meeting summaries. Wrapped in try/except — KB failure NEVER breaks chat. |

### Phase 4 — Vector search, admin trace viewer, Meeting Assistant

| Module | Owns |
|---|---|
| `orchestration/knowledge_search.py` | `KBSearchClient` — Azure AI Search hybrid (vector + BM25) over a dedicated `mela-kb-entries` index. Module singleton `kb_search_client` is `None` when `AZURE_SEARCH_KB_INDEX` is blank — `SQLKnowledgeStore.search` falls back to keyword SQL transparently. |
| `services/openai_service.py` | New `get_embedding(text) → list[float] | None`. `text-embedding-3-small`, 1536 dims. Returns `None` on failure — never raises. |
| `api/endpoints/orchestration.py` | New admin endpoints: `GET /traces`, `GET /traces/{id}`, `GET /kb/stats`. `case((..., 1), else_=0)` aggregation works on SQLite + Azure SQL. |
| `main.py` lifespan | New `_kb_expiry_sweep_loop` — every 6h calls `knowledge_store.expire_stale()`. Same `asyncio.create_task` + cancel-on-shutdown pattern as every other background loop. |
| `orchestration/seed.py` | Now seeds Meeting Assistant unconditionally. Blank URL → `WorkerStatus.UNCONFIGURED` (distinct from `UNREACHABLE`). `base_url`/`health_check_url` use `about:blank` sentinel since Pydantic requires non-empty strings. |
| `orchestration/types.py` | `WorkerStatus.UNCONFIGURED` added. |

### Phase 5 — Real-time events, workflows, tenant access

| Module | Owns |
|---|---|
| `orchestration/event_bus.py` | `OrchestrationEventBus` singleton. `dict[user_id, list[asyncio.Queue]]`. `subscribe / unsubscribe / publish / publish_to_tenant`. Bounded queues (50); evicts OLDEST on overflow. **In-process** — Redis Pub/Sub swap documented. |
| `schemas/chat.py` | `WorkerEventChunk` + `WorkerEventType` enum. `StreamChunk.type` extended with `"worker_event"` and `"heartbeat"`. |
| `api/endpoints/orchestration.py` | `GET /orchestration/events/stream` — SSE channel per user. Heartbeat every 30s to survive Azure App Service's 230s timeout. Subscribe-on-connect, unsubscribe-on-disconnect (`finally` block). |
| `services/workflow_service.py` | New `orchestrate` action type. `_render_goal_template` substitutes `{{user_display_name}}`, `{{tenant_id}}`, `{{workflow_name}}` (unknown placeholders left intact). Always background — `asyncio.create_task` against a fresh session; workflow run never blocks. |
| `models.WorkflowRun` | New `orchestration_trace_ids: JSON` column for admin correlation. |
| `orchestration/access.py` | `is_default_allow / has_access / allowed_worker_ids`. Single source of truth for "can tenant T invoke worker W?". Default-allow short-circuits without DB hit. |
| `orchestration/tool_bridge.py + router.py` | Both consult `access.py`. Defence in depth — tool-list filter strips inaccessible workers; router has the access check as belt-and-suspenders. |
| `models.WorkerTenantAccess` | Soft-delete only. `revoked_at` instead of row deletion — audit trail persists indefinitely. |
| Frontend `chat/layout.tsx` | Owns the SSE lifecycle. One connection per session. Reconnect on non-AbortError disconnect with exponential backoff (5s → 60s cap). |
| Frontend `WorkerEventBar.tsx` | Stacks up to 3 banners; oldest drops off; auto-dismiss 8s. Above input, never blocks it. |

### Phase 6 — Embedding surface

| Module | Owns |
|---|---|
| `mcp/server.py` | Inbound MCP-over-HTTP server. Single `POST /` dispatcher keyed on `tool` body field. Six tools (`mela_chat`, `mela_search_knowledge`, `mela_get_worker_status`, `mela_trigger_plan`, `mela_get_trace_status`, `mela_ingest_context`). Per-tool scope check via `assert_tool_scope`. |
| `mcp/auth.py` | `bcrypt.hashpw` / `bcrypt.checkpw`. Plaintext keys generated as `mela_<token>` and returned exactly once on creation. `last_used_at` touched best-effort on each auth. |
| `mcp/tools.py` | `MELA_TOOL_DEFS` (OpenAI-compatible function definitions), `MELA_TOOL_NAMES` frozenset, `is_tool_in_scope`, `SCOPE_WILDCARD = "*"`. |
| `models.MCPClient` | bcrypt hash + scopes JSON + soft-delete. |
| `api/endpoints/embed.py` | `POST /api/v1/embed/token` (auth: `X-Mela-Client-Key` MCP key) → 1h JWT. `GET /api/v1/embed/config?token=` — embed page reads its own scope/tenant. |
| `core/middleware.py` | New `EmbedFrameMiddleware`. Default `X-Frame-Options: SAMEORIGIN`; `Content-Security-Policy: frame-ancestors` when `MELA_EMBED_ALLOWED_ORIGINS` matches request `Origin`. |
| `api/endpoints/orchestration.py` | New: `GET /capabilities` (public, no auth — same shape as worker manifests so external apps discover Mela the same way Mela discovers workers). `POST /register` (worker self-registration; gated by `MELA_WORKER_REGISTRATION_KEY`; mints fresh `inbound_api_key`). MCP client CRUD: `POST/GET/DELETE /mcp-clients`. |
| Frontend `/embed/page.tsx` + `EmbedChatInterface.tsx` | Stripped chat — no sidebar, no profile switcher, no admin links. Auth via embed token, never MSAL. `postMessage` bridge between iframe and host page. |
| Frontend `embed/mela-chat.ts` | `<mela-chat>` Web Component. Builds to `public/embed.bundle.js` via `npm run build:embed` (esbuild). Host pages load with one script tag; communicate through `mela-response` custom events and `sendMessage()` API. |

---

## Architectural boundaries — what NOT to do

These are the rules that keep the system coherent. Breaking any one introduces a class of bugs that's hard to diagnose later.

1. **`MelaTask` and `MelaResult` are the only types that cross the adapter boundary.** Worker-specific shapes (MCP args, REST payloads, gRPC messages) translate to/from these inside `adapters/*.py` and never leak past. If you find yourself importing httpx into `router.py` or the executor, stop.

2. **`WorkerAdapter.execute()` never raises.** Every failure path returns `MelaResult.failure(code=..., message=..., retryable=...)`. The base class `_execute_with_retry` catches everything and translates. Subclasses can raise inside `_dispatch()` only — the base class converts.

3. **No fourth retry layer.** Existing layers: model_router cross-provider failover, openai_service per-model fallback chain, outcome_orchestrator MAX_ATTEMPTS=3. The orchestration brain adds: adapter manifest retry policy (cap = 2), circuit breaker fail-fast. **Do not add another.** If you find yourself wanting one, you probably want the circuit breaker — see `orchestration/breaker.py`.

4. **Workers never import Mela code.** The dependency only flows one way: Mela knows about workers. Workers know Mela's ingestion API URL — nothing else about Mela's internals. If you add a worker SDK, document this explicitly in its README.

5. **`Promise.allSettled` semantics in Python = `asyncio.gather(..., return_exceptions=True)`.** Never `Promise.all` for worker calls. A failed worker should produce a failed `MelaResult`, not an exception. The executor uses `gather(return_exceptions=True)` everywhere.

6. **The LLM reads `summary`, not `data`.** Any worker result over 500 chars that gets fed to the planner/synthesiser is summarised first. `KnowledgeStore.summarise_if_needed` is the helper. Full data is stored only in the worker; we keep a `data_pointer` and fetch on demand.

7. **Every execution carries a `trace_id`.** Log it. It is the thread that ties a chat request through the planner, the executor, multiple worker calls, and any worker callbacks that arrive minutes later.

8. **The registry is the only truth.** Capability names and worker URLs are NEVER hardcoded outside `seed.py` or registry rows. `tool_bridge` synthesises tools from manifests; the planner reads capabilities from manifests; the router validates against manifests. If a value isn't in the registry, it doesn't exist.

9. **Singletons hold per-process state — tests need explicit reset.** `worker_registry._cache`, `breaker_store._data`, `event_bus._subscribers`, the `AdapterFactory._cache`. When tests use isolated DBs, call `worker_registry._invalidate()` or use a fresh instance. Documented inline in `test_orchestration_phase5.py`.

10. **`app/services/__init__.py` shadows submodule attributes.** `from app.services.openai_service import openai_service` rebinds `app.services.openai_service` (the attribute on the parent package) to the singleton. Tests must reach the module via `sys.modules["app.services.openai_service"]` to monkeypatch the singleton. Same gotcha applies to `app.services.chat_service`. Documented inline in `test_orchestration_phase3.py`.

11. **No imports from `outcome_orchestrator` in the orchestration package.** They solve different problems and must stay decoupled. The single point of contact is the additive `_run_cross_worker` branch in `outcome_orchestrator.run()` — the orchestration brain is called from there, never the reverse.

12. **The MCP server (Phase 6A) is a CALLER.** Per-tool handlers translate inbound MCP arguments into the existing service-layer calls (`chat_service`, `knowledge_store`, `executor`). They do not introduce new business logic. If you need to do something new for an MCP-only path, add it to a service first, then call it from the handler.

13. **Soft-delete only for audit-bearing tables.** `worker_tenant_access`, `mcp_clients`. Set `revoked_at`, never `DELETE`. Admins need to know who had access when.

14. **Server-authoritative profile context.** Never trust client body for `profile_mode` / `tenant_id`. Read from `X-Profile-Mode` / `X-Tenant-Id` headers via the `ProfileContext` dependency.

15. **Every worker MCP call always overlays explicit `user_id` and `tenant_id`** from `MelaContext.user_id` / `tenant_id`. Many MCP workers fall back to "first user in DB" when these are omitted. Hard rule, not a TODO.

16. **`bcrypt` for client keys, never plaintext.** MCP client keys are hashed at rest and returned exactly once. If a key is lost, revoke and recreate.

---

## Background loops, all in `main.py` lifespan

Every long-running task in Mela uses the same pattern. Adding a new one means following it.

```python
async def _new_loop() -> None:
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        try:
            async with async_session_maker() as db:
                await do_thing(db)
        except Exception as exc:
            logger.warning("new_loop error: %s", exc)

_new_task = asyncio.create_task(_new_loop())
logger.info("New loop started")

# ...later, in shutdown:
_new_task.cancel()
try:
    await _new_task
except asyncio.CancelledError:
    pass
```

Existing loops (read these to confirm the pattern):
- `ingestion_worker.process_queue` — connector sync queue
- `_onedrive_periodic_loop` — 30-min OneDrive sync
- `_acl_refresh_periodic_loop` — 24-hour ACL refresh
- `_session_memory_cleanup_loop` — 6-hour session cleanup
- `_kb_expiry_sweep_loop` — 6-hour KB expiry (Phase 4)
- `private_chat_cleanup` — private chat sweep

---

## Tables introduced by the orchestration layer

All idempotent `CREATE TABLE IF NOT EXISTS` migrations live in `app/core/database.py`'s `init_db()` migrations list. Add new ones at the bottom; never reorder.

| Table | Phase | Purpose |
|---|---|---|
| `worker_registry` | 1 | Source of truth for registered workers (JSON manifest column + flat queryable columns). |
| `orchestration_traces` | 2 | One row per goal — execution-wide telemetry (status, plan_json, timing). |
| `orchestration_tasks` | 2 | One row per `MelaTask` — per-call telemetry (latency, summary, error_code, status). |
| `worker_events` | 2 | Audit row for unsolicited worker pushes via `/ingest/event`. |
| `knowledge_entries` | 3 | What Mela remembers — short summaries + `data_pointer` back to source. Indexed for tenant/user/type queries. |
| `worker_tenant_access` | 5C | Per-tenant access grants (soft-delete only). Hot lookup index on `(worker_id, tenant_id, revoked_at)`. |
| `mcp_clients` | 6A | External apps that call Mela's MCP server. bcrypt-hashed keys + scopes JSON. Soft-delete. |

Existing tables extended:
- `messages.profile_mode` + `tenant_id` — Phase 0 (pre-orchestration)
- `workflow_runs.orchestration_trace_ids` — Phase 5B (correlation between workflow runs and orchestration plans)

---

## Environment variables introduced

All have blank defaults. Existing deployments are unaffected unless they opt in.

| Var | Phase | Effect when blank |
|---|---|---|
| `TASK_RADAR_BASE_URL` / `TASK_RADAR_MCP_API_KEY` | 1 | Task Radar not seeded; Mela boots without it. |
| `TASK_RADAR_INBOUND_API_KEY` | 2 | Worker callbacks from Task Radar are rejected with 401. |
| `MELA_INGESTION_BASE_URL` | 2 | Manifests have no `report_back_url`; async callbacks won't auto-route until set. |
| `AZURE_SEARCH_KB_INDEX` | 4 | KB falls back to SQL keyword search. No vector embeddings used. |
| `KB_DEFAULT_EXPIRY_DAYS` | 4 | Per-type overrides (in `knowledge.py`) still apply; default is 30 days. |
| `MEETING_ASSISTANT_BASE_URL` / `MEETING_ASSISTANT_MCP_API_KEY` / `MEETING_ASSISTANT_INBOUND_API_KEY` | 4 | Meeting Assistant is seeded with `status=unconfigured`. |
| `WORKER_ACCESS_DEFAULT_ALLOW` | 5C | `True` (default) → access table is never consulted. Set to `False` to require explicit grants. |
| `MELA_EMBED_ALLOWED_ORIGINS` | 6B | `X-Frame-Options: SAMEORIGIN`. Mela cannot be framed by third-party sites. |
| `MELA_WORKER_REGISTRATION_KEY` | 6C | `POST /api/v1/orchestration/register` returns 503. Self-registration disabled. |

---

## A developer joining the project

If you are about to make a change, read this checklist first:

1. **Adding a new worker?** It should require zero code changes — just an env var and a row in `worker_registry`. If it's MCP-over-HTTP, the `MCPAdapter` already serves it. Only add a new adapter if the worker speaks a new protocol; if so, add it to `_PROTOCOL_TO_ADAPTER` in `factory.py`.

2. **Adding a new capability to an existing worker?** Add it to the worker's `capabilities` list in `seed.py` (or in the worker's self-registration manifest). The tool bridge picks it up automatically; the planner sees it next time it runs.

3. **Adding a new MCP tool Mela exposes?** Add to `MELA_TOOL_DEFS` and `MELA_TOOL_NAMES` in `mcp/tools.py`, write the handler in `mcp/server.py`, register it in `_HANDLERS`. Update tests.

4. **Adding a new entry_type to the KB?** Add a per-type expiry override to `KB_EXPIRY_DAYS_BY_TYPE` in `knowledge.py`. The frontend KB stats endpoint will pick up new types automatically.

5. **Adding a new admin endpoint?** Mount under `/api/v1/orchestration/`. Use `Depends(get_current_admin_user)`. Document admin-only in the docstring.

6. **Adding a new background task?** Use the lifespan pattern documented above. Cancel + await on shutdown.

7. **Touching `outcome_orchestrator.py`?** Do not import from the orchestration package. The integration point is the existing `_run_cross_worker` branch in `run()`. Anything else is a smell.

8. **Touching `chat_service.process_chat`?** Only ADD blocks. Existing budget / model-access / RAG / agentic-loop / fallback logic is load-bearing. New context injections go in the system-prompt block area; KB context is the model to follow.

9. **Anything new that calls workers?** Use `executor.run_single` or `executor.run_plan`. Never use `WorkerAdapter` directly unless you're inside the orchestration package.

10. **Anything new that talks to the chat?** Don't use `chat_service.process_chat` directly — go through `outcome_orchestrator.run()`. It owns the file-artifact verification, the budget enforcement, and the model fallback. Bypassing it loses those guarantees silently.

11. **Tests use isolated SQLite DBs.** Module-level singletons (`worker_registry`, `breaker_store`, `adapter_factory`, `event_bus`) cache state across tests. Reset them explicitly when needed; the inline fix patterns are documented in `test_orchestration_phase5.py` and `test_orchestration_phase6.py`.

---

## Test count by phase

| Phase | Total passing | Cumulative new |
|---|---|---|
| Pre-orchestration baseline | 297 | 0 |
| Phase 1 (foundation) | 297 + 0 = 297 | smoke-only via lifespan |
| Phase 2 (router + executor + ingest) | 308 | +11 |
| Phase 3 (planner + KB + cross-worker) | 322 | +14 |
| Phase 4 (search + traces + Meeting Assistant) | 339 | +17 |
| Phase 5 (events + workflows + access) | 355 | +16 |
| Phase 6 (MCP server + embed + handshake) | 381 | +26 |

`pytest -q` passes; `ruff check app/` clean; `npx tsc --noEmit` clean.

Run the Phase-2 smoke script (`backend/scripts/phase2_smoke.py`) against a live deployment to confirm the worker-callback path. Everything else is covered by the test suite.

---

## Files NOT in the orchestration package

The orchestration brain crosses package boundaries. These are the touch points outside `app/orchestration/`:

- `app/api/endpoints/orchestration.py` — admin/user endpoints
- `app/api/endpoints/orchestration_ingest.py` — worker callbacks
- `app/api/endpoints/embed.py` — Phase 6B
- `app/mcp/` — Phase 6A inbound MCP server
- `app/services/orchestration_planner.py` — Phase 3 planner
- `app/services/outcome_orchestrator.py` — additive cross-worker branch only
- `app/services/chat_service.py` — KB-context injection block + `trace_id` propagation
- `app/services/workflow_service.py` — `orchestrate` action
- `app/agents/tool_executor.py` — `worker__*` dispatch branch
- `app/services/openai_service.py` — `get_embedding` helper
- `app/models/models.py` — every new ORM model
- `app/core/database.py` — every new DDL migration
- `app/core/config.py` — every new env var
- `app/core/middleware.py` — `EmbedFrameMiddleware`, silent path prefixes
- `app/main.py` — lifespan task wiring + middleware mount + MCP router mount
- `app/schemas/chat.py` — `WorkerEventChunk`, `WorkerEventType`, `StreamChunk` type extension

Frontend:
- `frontend/src/lib/api.ts` — typed methods + types for every new endpoint
- `frontend/src/lib/store.ts` — `workerEvents` slice + actions
- `frontend/src/components/chat/WorkerEventBar.tsx`
- `frontend/src/components/embed/EmbedChatInterface.tsx`
- `frontend/src/components/settings/MonitoringTab.tsx` — Workers + Traces + Access Control sections
- `frontend/src/app/chat/layout.tsx` — SSE lifecycle owner
- `frontend/src/app/chat/page.tsx` — `<WorkerEventBar />` mount
- `frontend/src/app/embed/page.tsx`
- `frontend/src/embed/mela-chat.ts`

If a feature touches more than these, double-check the layering. Drift is how this kind of system rots.

---

That is the complete picture. Mela now coordinates an arbitrary number of independent worker apps, lives inside other apps as an iframe, exposes itself as a service via MCP, and lets new workers register themselves without admin intervention. Everything else is just configuration.
