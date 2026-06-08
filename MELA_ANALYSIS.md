# Mela AI — Full Project Analysis

> Analysis date: 2026-06-08 · Hackathon deadline: 2026-06-14 (6 days) · Branch: `main`
> Method: full read of backend `app/`, orchestration layer, connectors, frontend `src/`, infra, env files (`env/.env.local`, `env/.env.dev`, `backend/.env`), and the local `backend/mela_dev.db`.

**One-line verdict:** The "brain" (chat + RAG + agentic tools + the whole 6-phase orchestration layer) is genuinely built and mostly production-grade. The single biggest gap is that **the Task Radar worker app does not exist in this repository** — only Mela's *adapter* and *manifest* for it exist — and it is **not configured** (`TASK_RADAR_BASE_URL` is blank), so the orchestration→worker→callback loop has never run. The demo's "create Planner tasks" outcome is nonetheless achievable today through Mela's **built-in `create_task` Graph tool**, which is wired and configured.

---

## 1. Repository structure

Monorepo, no workspace manager — backend (Python/FastAPI) and frontend (Next.js) are independent trees deployed separately.

| Top-level dir | Purpose | State |
|---|---|---|
| `backend/` | FastAPI app. `app/api` (routers), `app/orchestration` (the worker-orchestration brain), `app/services` (LLM, RAG, connectors, alerting, code-interpreter…), `app/agents` (tool executor), `app/mcp` (inbound MCP server), `app/models`, `app/core`. Alembic migrations in `backend/alembic/versions` (001–006). | Mature |
| `frontend/` | Next.js 14 App Router (`src/app`), components (`chat`, `admin`, `settings`, `embed`, `ui`), MSAL provider, Zustand store (`lib/store.ts`), API client (`lib/api.ts`, 3300 lines), embeddable web component (`src/embed/mela-chat.ts`). | Mature |
| `infra/` | Bicep IaC — `main.bicep` + modules: `app-service`, `redis`, `storage`, `key-vault`, `monitoring`, `alerts`, `code-runner` (gVisor sidecar), `role-assignments`. | Complete |
| `database/` | `schema.sql` reference dump. | Reference |
| `docs/` | `azure-setup.md`, `resources-required.md`, `validation-checklist.md`, `mela-orchestration-onepager.html`. | Docs |
| `scripts/` | `azd-up`, `deploy.sh`, `preflight`, `scan-resources`, `demo_readiness_check.py`. | Ops |
| `env/` | `.env.dev.sample`, `.env.prod.sample`, **plus real (gitignored) `.env.local` and `.env.dev` with live Azure secrets**. | Configured |
| `tests/` + `backend/tests/` | ~40 backend test files incl. `test_orchestration_phase2..6`, `test_obo_flow`, `test_agent_memory_acl`, `test_alert_service`. | Good coverage |
| repo root | `ORCHESTRATION.md` (canonical architecture doc — excellent), `README.md`, `azure.yaml` (azd), `docker-compose.yml`, `.github/workflows` (ci/cd/security). | — |

**Worker apps:** there is **no `workers/` directory and no Task Radar / Meeting Assistant app source anywhere in the repo.** Workers are external apps Mela calls over MCP-over-HTTP. The only worker-specific code here is `backend/app/orchestration/adapters/task_radar.py` (a generic `MCPAdapter` with a `TaskRadarAdapter` alias) and the manifests in `backend/app/orchestration/seed.py`.

**Config files:** `backend/.env` (forces SQLite + ACS/Teams alert creds), `env/.env.dev` (Azure dev), `env/.env.local` (loaded **last**, wins — points `DATABASE_URL` at Azure SQL and supplies all real keys). `infra/main.parameters.json`, `azure.yaml`, `docker-compose.yml`.

---

## 2. What is working (production-ready or near-ready)

**Conversation engine (full trace).** `POST /api/v1/chat/completions` → `OutcomeOrchestrator.run()` ([outcome_orchestrator.py](backend/app/services/outcome_orchestrator.py)) → intent detection + Auto-Mode model selection → `ChatService.process_chat()` ([chat_service.py:1108](backend/app/services/chat_service.py#L1108)). Inside: budget enforcement → model-access downgrade → conversation persistence → RAG injection → tool synthesis → `ModelRouter.stream()` → **agentic tool loop (`_MAX_TOOL_ROUNDS = 5`, [chat_service.py:1744](backend/app/services/chat_service.py#L1744))** that executes tools, feeds results back, and re-prompts until a final answer. Streamed to the browser as SSE via `api.streamChat()` ([api.ts:529](frontend/src/lib/api.ts#L529), `response.body.getReader()`). This path is solid.

**Model router with silent cross-provider failover** ([model_router.py](backend/app/services/model_router.py)). Routes by model prefix (`claude-*`→Anthropic, `gemini-*`→Gemini, else Azure OpenAI/AI Foundry), buffers pre-content chunks so it can fail over before the user sees anything, and budget-downgrades expensive models at ≥70%/≥90% usage.

**Outcome Orchestrator** — fully implemented (not a placeholder). File-artifact guarantee with up-to-3 correction passes, empty-response retry, intent classes incl. `CROSS_WORKER`.

**RAG pipeline** ([search/query_pipeline.py](backend/app/services/search/query_pipeline.py)). Hybrid vector+keyword over Azure AI Search, **per-tenant + per-user ACL** enforced twice (OData `_build_acl_filter` fail-closed + Python post-filter `_result_visible_to_user`), 3072-dim embeddings (`text-embedding-3-large`), 1-hour query cache index, prompt-injection scanning that drops/flags chunks, sensitivity-label ceiling, and a live Graph search fallback when indexed results are sparse. `AZURE_SEARCH_ENDPOINT` + index `fileshare-documents` are configured.

**The entire orchestration layer (Phases 1–6)** — registry, breaker, router, executor, store, adapters, tool-bridge, ingest endpoints, planner, MCP server, embed surface. See §7; this is the strongest part of the codebase.

**Built-in agent tools** ([tool_executor.py](backend/app/agents/tool_executor.py)) — 18 built-ins: email (get/send/draft/search/reply/thread), calendar (get/schedule/availability), **Planner (`list_planner_tasks`, `create_task` → real Graph task creation)**, `search_documents`, `search_graph`, `onboard_user`, `apply_template`, `run_python_code`. Confirmation gate for dangerous tools, role gates, audit logging.

**Microsoft Graph service** ([graph_service.py](backend/app/services/graph_service.py)) — app-only **and** OBO delegated flows; mail, calendar, Planner create/update, To-Do, Teams meetings, full identity lifecycle (create/disable/license/group). `GRAPH_DEFAULT_PLANNER_PLAN_ID` is set in `.env.local`.

**Code interpreter** ([code_interpreter_service.py](backend/app/services/code_interpreter_service.py)) — 771 lines, live; gVisor sidecar option behind `USE_GVISOR_RUNTIME`/`CODE_RUNNER_URL`, local subprocess fallback otherwise.

**Auth (Entra/MSAL/OBO)** — frontend MSAL SPA ([providers/MsalProvider.tsx](frontend/src/components/providers/MsalProvider.tsx)), backend JWT validation against the login app registration, OBO exchange in [obo_service.py](backend/app/services/obo_service.py) (flag `USE_OBO_FOR_GRAPH`, default off → falls back to app-only). Bootstrap-admin elevation by email/OID. Dev-login enabled locally.

**Frontend** — chat (streaming, citations, worker-event bar, voice overlay, model insights), admin panel (overview/users/tenants/models/audit/errors/invoices/onboarding/offboarding), settings (connectors/knowledge/skills/workflows/agent-memory/monitoring/MCP clients/worker registry), embed page + `<mela-chat>` web component.

**Image/speech/translation/document-intelligence** — FLUX primary + DALL-E fallback ([config.py:380](backend/app/core/config.py#L380), `IMAGE_PROVIDER_ORDER=flux,dalle`, FLUX endpoint configured), Azure Speech, Translator, Document Intelligence services all present and flag-gated.

**Infra + CI/CD** — Bicep + azd, GitHub Actions ci/cd/security (ruff, pytest, tsc, Gitleaks), Key Vault references.

---

## 3. What is partially built (skeleton there, needs completion)

- **Async worker callback loop end-to-end.** The code is complete (`store.py` pending-task registry, `/api/v1/ingest/result` resolves it, notifies, pushes SSE, writes KB). But it has **never executed** because no async worker is configured (`worker_events`, `orchestration_traces`, `knowledge_entries` tables are all empty in the dev DB). It is wired but unproven against a live worker.
- **Knowledge Base vector search.** `AZURE_SEARCH_KB_INDEX` is **blank**, so `SQLKnowledgeStore` silently falls back to SQL keyword search ([knowledge_search.py]). Works, but the "hybrid KB vector recall" feature is dormant.
- **Agent memory ACL recall** ([agent_memory_service.py](backend/app/services/agent_memory_service.py), 735 lines + `test_agent_memory_acl.py`) — implemented and ACL-scoped; functional but lightly exercised. Memory blobs live in Azure Blob (`AZURE_STORAGE_CONTAINER_AGENT_MEMORY`).
- **Meeting Assistant worker** — seeded as `status=unconfigured` / `about:blank` (confirmed in DB). Manifest + 6 capabilities exist; no worker behind it.
- **Worker self-registration** (`POST /orchestration/register`) — implemented but returns 503 because `MELA_WORKER_REGISTRATION_KEY` is blank.
- **Per-tenant worker access (Phase 5C)** — implemented but inert: `WORKER_ACCESS_DEFAULT_ALLOW=True` means the access table is never consulted.
- **Redis-backed shared state** — `REDIS_URL` blank. Registry cache, circuit-breaker store, orchestration store, event bus, and **alert cooldown dedup** all fall back to in-process/no-op. Fine for single instance; breaks dedup and multi-instance correctness.

---

## 4. What is broken (exists but does not work)

- **Local SQLite dev DB is stale, not the source of truth.** `backend/mela_dev.db` last logged errors on 2026-05-25 (`no such column: model_rankings.cost_multiplier`). That column **now exists** in the DB (migration `003_model_cost_multiplier` applied), so the incident is resolved — but the file is a leftover; the app actually runs against **Azure SQL** (`.env.local` `DATABASE_URL=mssql+aioodbc://…`, loaded last). Risk: if anyone runs the demo on the SQLite file without `init_db` migrations, the `cost_multiplier` / model-settings endpoint regresses again ([demo_readiness_check.py](scripts/demo_readiness_check.py) "Incident 1").
- **Worker-tool calls in the agentic loop don't propagate `trace_id`/progress.** [chat_service.py:1770](backend/app/services/chat_service.py#L1770) calls `tool_executor.execute_tool(...)` **without** `trace_id=` or `on_progress=`. ORCHESTRATION.md (line 123) states chat_service passes `trace_id=_corr_id`; it currently does not. Consequence: `worker__*` tool calls still run (tool_bridge mints a fresh `uuid4` trace), but the chat's correlation ID isn't threaded and no `tool_executing`/worker-event progress streams for worker tools. Minor, but it breaks the "watch the worker work" demo narration.
- **No alert has ever fired.** `alert_events` table is empty (0 rows). The alerting code is sound but unexercised; see §9.

No hard crashes found in the brain path. The "broken" items are integration/config gaps, not logic bugs.

---

## 5. What is missing entirely

- **The Task Radar worker application.** Not in this repo in any form. Only the calling adapter + manifest exist. There is **no** service that registers a Task Radar manifest, receives `trigger_scan`, talks to Planner, and POSTs back to `/api/v1/ingest/result`.
- **A "create tasks" worker capability.** Even the *seeded* Task Radar manifest ([seed.py:40](backend/app/orchestration/seed.py#L40)) only declares read/observe capabilities: `get_tasks`, `get_task_detail`, `get_scan_runs`, `trigger_scan`, `get_connections`, `update_task_status`, `get_tasks_today`, `get_overdue_tasks`, `get_audit_log`. **There is no `create_task`/`create_planner_tasks` capability on any worker.** Task creation only exists as Mela's *built-in* Graph tool.
- **Any Microsoft IQ branding/surface in code.** Grep for `Foundry IQ` / `Work IQ` / `Fabric IQ` / `Fabric` → zero hits. See §8.
- **A Teams app / M365 Copilot extension.** The MCP server + embed widget *could* back one, but there is no Teams app manifest, no Copilot plugin/declarative-agent manifest, no `manifest.json`/`teamsapp` package.
- **Microsoft Fabric / data-pipeline integration.** None.
- **A configured ingestion callback URL.** `MELA_INGESTION_BASE_URL` is blank → manifests get no `report_back_url`; async results cannot auto-route.

---

## 6. Task Radar — current state and what is needed

**Where it lives:** nowhere as an app. The integration surface is:
- Adapter: [adapters/task_radar.py](backend/app/orchestration/adapters/task_radar.py) — generic `MCPAdapter`; always overlays `user_id`/`tenant_id`, treats `trigger_scan` as async, never raises.
- Manifest builder: [seed.py:202](backend/app/orchestration/seed.py#L202) `_build_task_radar_manifest()` — **returns `None` and logs "seed skipped" when `TASK_RADAR_BASE_URL` is blank.** It is blank → Task Radar is not in the registry (confirmed: dev DB `worker_registry` contains only `meeting-assistant`).

**Checklist against your questions:**
- Registered manifest? **No** — skipped because unconfigured.
- Can it receive a task payload from the orchestration layer? The *plumbing* can (`MCPAdapter._dispatch` POSTs `{tool, arguments}` to `base_url`), but there is no worker and no URL, so **no**.
- Connects to Microsoft Planner via Graph? **No** — there is no Task Radar code. (Mela's own connector `connectors/planner.py` only *reads/indexes* Planner tasks for RAG; it has no create path.)
- Sends a callback on completion? **No** — no worker exists; and `report_back_url` is unset anyway.
- Completely missing for a working demo: (1) the worker app itself; (2) a task-creation capability; (3) `TASK_RADAR_BASE_URL` + `TASK_RADAR_MCP_API_KEY` + `TASK_RADAR_INBOUND_API_KEY` + `MELA_INGESTION_BASE_URL` env; (4) a Planner plan/bucket the worker writes to.
- Broken: nothing in Mela's adapter — the failure mode is "worker absent," which the system handles gracefully (planner returns `UNRESOLVABLE`/`NO_VALID_TASKS`, router would return `UNKNOWN_WORKER`).

**Fastest demo-credible path:** build a ~150-line FastAPI MCP worker that (a) exposes `POST /` accepting `{tool, arguments}` with `X-Api-Key`, (b) implements one async capability `create_followup_tasks` that calls Graph `POST /planner/tasks` (app-only, reusing the same `AZURE_CLIENT_ID` + `GRAPH_DEFAULT_PLANNER_PLAN_ID`), (c) POSTs a `MelaResult` to `/api/v1/ingest/result`, and (d) exposes `GET /health?deep=true`. Then add that capability to the seeded manifest and set the four env vars. Zero changes needed to Mela's adapter/router.

---

## 7. Worker orchestration — current state

This layer is **genuinely complete and well-engineered** (Phases 1–6, ~380 passing tests claimed in ORCHESTRATION.md). Component-by-component:

- **Registry** ([registry.py](backend/app/orchestration/registry.py)) — `WorkerRegistryEntry` table, **60-second in-process TTL cache** behind an `asyncio.Lock`, single-flight refresh, idempotent upsert. ✅ (Redis swap noted but not done.)
- **Router** ([router.py](backend/app/orchestration/router.py)) — validates worker + capability + tenant access; never raises; returns adapter or `RouteFailure` with codes `UNKNOWN_WORKER` / `UNKNOWN_CAPABILITY` / `WORKER_ACCESS_DENIED` / `ADAPTER_UNAVAILABLE`. ✅
- **Adapters** ([adapters/](backend/app/orchestration/adapters/)) — `MCPAdapter` (MCP-over-HTTP) implemented and generic; `AdapterFactory` keyed on protocol, cache invalidated by manifest signature. **REST and gRPC adapters: NOT implemented** — `Protocol` enum exists but only MCP is in `_PROTOCOL_TO_ADAPTER`. Adding an MCP worker needs zero adapter code; a REST/gRPC worker needs a new adapter.
- **Executor + store** ([executor.py](backend/app/orchestration/executor.py), [store.py](backend/app/orchestration/store.py)) — `run_single` + `run_plan` (sequential batches, **parallel within batch via `asyncio.gather(return_exceptions=True)`**), per-session persist lock, full `OrchestrationTrace`/`OrchestrationTask` persistence, async pending-task registry with `wait_for_result(timeout)`. ✅
- **Circuit breakers** ([breaker.py](backend/app/orchestration/breaker.py)) — **3-state per worker (CLOSED→OPEN→HALF_OPEN→CLOSED)**, failure-window threshold (3 in 60s), 30s cooldown, single-probe gate, fires ops alerts on trip. Tested (`test_orchestration_phase*`). In-memory store only. ✅
- **Tool-bridge** ([tool_bridge.py](backend/app/orchestration/tool_bridge.py)) — synthesises OpenAI tool defs from manifests (`worker__<slug>__<capability>`), strips `user_id`/`tenant_id` from the exposed schema, personal/work scope + tenant-access filtering, dispatches via executor and maps `MelaResult`→tool-dict. Hooked into `tool_executor.get_available_tools` and `_execute_tool_inner` (`worker__` prefix branch). ✅ **Wired** — but see §4: chat_service doesn't pass `trace_id`/`on_progress` into it.
- **Async callback handling** ([orchestration_ingest.py](backend/app/api/endpoints/orchestration_ingest.py)) — `/ingest/result` resolves the pending task, wakes awaiters, updates the row, finalizes the trace, writes a KB entry, **pushes a real-time `WorkerEventChunk` to the user's SSE channel**, and creates an in-app notification. Worker identity verified via `require_worker_api_key`; rejects cross-worker task spoofing. ✅ Fully wired, never exercised live (0 rows).

**Net:** orchestration is the crown jewel. It just has no worker plugged in.

---

## 8. Microsoft IQ integration — current gaps

- **Foundry IQ — PRESENT but not branded.** Azure AI Foundry **is** the primary LLM + embedding layer: `AI_FOUNDRY_ENDPOINT` is configured and `effective_openai_endpoint` resolves to it ([config.py:117](backend/app/core/config.py#L117)); all named deployments (`gpt-4.1`, `Kimi-K2.5`, `Mistral-Large-3`, `gpt-5.2-chat`, `grok-3-mini`, `Llama-4-Maverick`, `text-embedding-3-large`) run on Foundry. **Gap:** nothing in code, UI, or docs *names* it "Foundry IQ." For judging you need to surface it explicitly (a label in the model panel / a one-line architecture callout). The substance is real; the framing is missing.
- **Work IQ — PARTIAL.** Deep M365/Graph integration exists (mail, calendar, Planner, To-Do, Teams meetings, SharePoint/OneDrive, identity lifecycle), plus an **inbound MCP server** (`/mcp/v1`, 6 tools) and an **embeddable `<mela-chat>` widget** — both are credible "surface Mela into Microsoft tools" hooks. **Gap:** there is no Teams app package or M365 Copilot declarative-agent/plugin manifest, so Mela agents do not actually appear *inside* Teams/Copilot yet. This is the cheapest high-impact IQ win for the demo.
- **Fabric IQ — ABSENT.** No Microsoft Fabric, OneLake, or data-pipeline integration of any kind.

**Recommendation:** lead with **Foundry IQ** (make it visible — it's already true and load-bearing), and add a thin **Work IQ** proof (a Teams app manifest pointing at the MCP server, or a Copilot declarative agent). Don't attempt Fabric IQ in 6 days.

---

## 9. Notification system — findings and recent alerts

**Where it lives:** [alert_service.py](backend/app/services/alert_service.py) (ops/incident alerting) + [notification_service.py](backend/app/services/notification_service.py) (in-app user notifications) + `ai_triage.py` (LLM root-cause) + `alert_events` table (migration `006_alert_events`) + `infra/modules/alerts.bicep`.

**What it monitors/alerts on:** circuit-breaker transitions (`BREAKER_OPEN`/`BREAKER_HALF_OPEN`, fired from [breaker.py:35](backend/app/orchestration/breaker.py#L35)), unhandled API exceptions (wired in `main.py` lifespan/global handler, lines 56 & 649), and ingestion-worker failures (`test_ingestion_worker_alerts.py`). Channels: **ACS Email + Microsoft Teams Adaptive Card**, with AI triage auto-attached for `critical`, a dead-letter file fallback (`/tmp/alert_deadletter.jsonl`), and an emergency Teams fallback if email fails.

**Config:** `ACS_CONNECTION_STRING` and `TEAMS_WEBHOOK_URL` **are set** (in `backend/.env`). `ALERT_ENABLED=true`, `ALERT_COOLDOWN_SECONDS=300`, always-CC `edgar.mcochieng@armely.com`.

**Redis cooldown dedup:** implemented via `_fingerprint()` (sha256 of `code:route:severity`) + `_is_suppressed`/`_set_cooldown` against Redis ([alert_service.py:60](backend/app/services/alert_service.py#L60)). **But `REDIS_URL` is blank** → `get_redis()` returns `None` → `_is_suppressed` returns `False` every time → **dedup is effectively disabled; every non-critical alert would send with no suppression.** Critical alerts bypass cooldown by design.

**Recent alert history / errors:** **None.** `alert_events` table has **0 rows** in the dev DB — no alert has ever been recorded or fired here. The only recorded incidents anywhere are stale `error_logs` rows from 2026-05-25 (`model_rankings.cost_multiplier` OperationalError ×8, since fixed). There are no breaker trips, no worker failures, no ACS/Teams send records. **What's "silenced":** nothing is actively silenced; the system is simply idle because no worker traffic and no Redis. To validate it for the demo, force a breaker trip or POST a bad `/ingest/result` and confirm a Teams card lands.

### Validation update (2026-06-08)

**Root cause of the empty table FIXED.** `send_alert()` never persisted an `AlertEvent` row despite the model docstring ("one per send_alert call") — that is why `alert_events` was empty. Added `_persist_alert_event()` to `alert_service.py` and call it inside `send_alert()` after delivery, recording severity, code, route, tenant, and `channels_attempted` (email/Teams success). The 15 alert unit tests pass (an autouse fixture patches the new DB write so they stay hermetic).

**Self-test added:** `scripts/test_alert.py` trips a circuit breaker (3 failures → OPEN), fires the `BREAKER_OPEN` critical alert through `send_alert`, then verifies the `alert_events` row and reports which channels delivered.

**What the run proved (in this dev sandbox):**
- Circuit breaker **trips to OPEN** at the threshold and the breaker→alert wiring auto-fires `send_alert` (`Circuit breaker tripped OPEN for worker=… (3 failures in 60s window)` + the alert dispatch executing). ✓
- `send_alert` runs all channels with graceful fallback and never raises. ✓
- Redis cooldown path executes and **falls back to in-process state** when Redis is unreachable, exactly as designed. ✓

**What still requires a connected environment (could NOT be confirmed from this sandbox):**
- **ACS email** — the `azure-communication-email` package (declared in `requirements.txt`) is not installed in this local venv (`No module named 'azure.communication'`).
- **Teams Adaptive Card delivery + `alert_events` persistence** — this sandbox has no network egress to Azure; the Azure SQL connection and the Teams webhook POST time out.
- **Action for the demo host:** run `cd backend && python ../scripts/test_alert.py` from an environment with `pip install -r requirements.txt` done and network access to Azure SQL + the Teams webhook (and ideally a reachable `REDIS_URL`). Expected: `alert_events row FOUND [OK]` with `channels_attempted={'email': True/…, 'teams': True}` and a card in the configured Teams channel.

---

## 10. Demo scenario — step-by-step success/failure trace

**Scenario:** user opens chat → "Review the attached compliance document and create follow-up tasks for my team" → RAG retrieve → reason → dispatch to Task Radar → Task Radar creates Planner tasks → brain confirms with synthesis.

| Step | What happens | Outcome |
|---|---|---|
| 1. Message in | `POST /chat/completions` → `OutcomeOrchestrator.run` → `detect_intent`. Text has "review" (ANALYSIS word) and one task domain only → classified **ANALYSIS, not CROSS_WORKER** ([outcome_orchestrator.py:113](backend/app/services/outcome_orchestrator.py#L113)). So it runs the **standard agentic chat path**, *not* the planner/worker path. | ✅ runs (but not via the route the scenario assumes) |
| 2. RAG retrieve | Agentic loop exposes `search_documents`; in work mode with `AZURE_SEARCH_ENDPOINT` set, `enterprise_query.search` runs ACL-filtered hybrid search. An **attached** doc would instead arrive as an uploaded file / agent-memory data-card and be passed inline. | ✅ if the doc is indexed or attached as an upload; ⚠️ a freshly "attached" file is handled via upload/agent-memory, not the SharePoint index |
| 3. Reasoning | LLM (Auto-Mode, likely `gpt-5.2-chat`/`gpt-4.1` on Foundry) reads context, decides to create tasks. | ✅ |
| 4. "Dispatch to Task Radar" | **FAILS as literally described.** Task Radar is not registered (no `TASK_RADAR_BASE_URL`), so no `worker__task_radar__*` tool is synthesized and the planner has no task-creation capability. If a worker tool *were* called, the only Task Radar capabilities are read-only + `trigger_scan` — **none create tasks.** | ❌ via Task Radar |
| 4'. Built-in task creation | Instead, the LLM calls the **built-in `create_task` tool** → `graph_service.create_planner_task_for_user(plan_id=GRAPH_DEFAULT_PLANNER_PLAN_ID, …)` (app-only token via `AZURE_CLIENT_ID`). Both are configured. Real Planner tasks are created. | ✅ this is the working path |
| 5. Worker callback | N/A — no async worker; `/ingest/result` never invoked. No `WorkerEventChunk` on the SSE bar. | ❌ (not reached) |
| 6. Synthesis confirm | Agentic loop's final LLM pass narrates "I created N tasks in Planner…" and streams via SSE. | ✅ |

**Bottom line:** the *outcome* (review doc → create Planner tasks → confirm) **works today through the built-in Graph tool path**, provided (a) the user is in **work mode** with a Microsoft work account, (b) `GRAPH_DEFAULT_PLANNER_PLAN_ID` resolves to a plan the app can write to, and (c) Graph app permissions (`Tasks.ReadWrite`/`Group.ReadWrite.All`) have admin consent. The *architecture you want to show off* (brain → Task Radar → async callback → live worker-event bar → KB write) **does not run** because the worker doesn't exist and isn't configured. Likely concrete failure if you force the orchestration path: planner returns `UNRESOLVABLE`/`NO_VALID_TASKS`, or a hand-built `worker__task_radar__*` call returns `UNKNOWN_WORKER`. Likely failure on the built-in path if mis-set: Graph `404 (plan not found)` or `403 (no admin consent)` → handled gracefully by `_graph_error`, falling back to a personal To-Do task.

---

## 11. Recommended build order for the next 6 days (deadline June 14)

Goal: make the *advertised* orchestration story real and put a Microsoft IQ flag clearly on the board, without destabilizing the working brain.

**Day 1 — De-risk the demo path that already works + pick the DB.**
- Lock the demo to **Azure SQL** (confirm `.env.local` wins; run `init_db`/alembic on it). Delete/ignore `mela_dev.db` so nobody demos the stale SQLite.
- End-to-end test the built-in path: work-mode chat → `search_documents` → `create_task` into the real `GRAPH_DEFAULT_PLANNER_PLAN_ID`. Fix Graph admin-consent/bucket issues now, not on stage. Run `scripts/demo_readiness_check.py`.

**Day 2–3 — Build the Task Radar worker (the missing centerpiece).**
- New ~150–250-line FastAPI app (separate process/container): `POST /` MCP dispatcher (`X-Api-Key`), `GET /health?deep=true`.
- Implement one async capability `create_followup_tasks(items[], plan_id?)` that calls Graph `POST /planner/tasks` (reuse `AZURE_CLIENT_ID` + plan id), then POSTs a `MelaResult` to `MELA_INGESTION_BASE_URL/api/v1/ingest/result` with `X-Worker-Id: task-radar` + `X-Worker-Api-Key`.
- Add `create_followup_tasks` (and optionally `trigger_scan`) to `_TASK_RADAR_CAPABILITIES` in [seed.py:40](backend/app/orchestration/seed.py#L40).
- Set env: `TASK_RADAR_BASE_URL`, `TASK_RADAR_MCP_API_KEY`, `TASK_RADAR_INBOUND_API_KEY`, `MELA_INGESTION_BASE_URL`. Confirm it appears in the Worker Registry admin tab as healthy.

**Day 3 — Thread the orchestration path into chat + fix the trace gap.**
- In [chat_service.py:1770](backend/app/services/chat_service.py#L1770) pass `trace_id=_corr_id` and an `on_progress` callback into `tool_executor.execute_tool` so worker tools stream `tool_executing` + drive the WorkerEventBar.
- Verify the full loop live: chat → `worker__task_radar__create_followup_tasks` (async) → adapter returns "accepted" → worker callback → `/ingest/result` → SSE worker-event + notification + KB entry. This makes `orchestration_traces`/`worker_events`/`knowledge_entries` populate for the first time.
- Tune intent: ensure a "create follow-up tasks for my team" phrasing reliably reaches the worker tool (either via the agentic tool list or by nudging `CROSS_WORKER` keywords).

**Day 4 — Microsoft IQ flags.**
- **Foundry IQ (cheap, already true):** add a visible "Powered by Azure AI Foundry" surface — model panel badge + an architecture line in the admin Overview/Monitoring tab listing Foundry as the LLM+embedding layer. No backend change.
- **Work IQ (high impact):** ship a minimal **Teams app manifest** (or M365 Copilot declarative-agent manifest) that points at the existing `/mcp/v1` MCP server or the `<mela-chat>` embed. Even sideloaded, "Mela answering inside Teams" is a strong judge moment.

**Day 5 — Reliability + alerting proof.**
- Set `REDIS_URL` (Azure Redis from `infra/modules/redis.bicep`) so alert cooldown dedup, breaker, and registry caches are correct and multi-instance-safe.
- Force one alert (trip a breaker by pointing Task Radar at a dead URL for 3 calls) and confirm a **Teams Adaptive Card** lands with AI triage. Screenshot it — that's your "zero-blindness ops" story and your first row in `alert_events`.

**Day 6 — Rehearse + harden.**
- Two scripted demo runs: (1) the **orchestration** story (chat → Task Radar async → live worker-event bar → Planner tasks → KB recall on a follow-up question), (2) fallback to the **built-in** path if the worker misbehaves.
- Pre-create the Planner plan/buckets, pre-warm RAG with the compliance doc, pre-load the Teams app. Freeze code; only config after this point.

**Explicitly do NOT attempt in 6 days:** Fabric IQ, REST/gRPC adapters, Redis-backed store rewrites beyond setting `REDIS_URL`, Meeting Assistant worker, worker self-registration. They add risk without changing the winning narrative.
