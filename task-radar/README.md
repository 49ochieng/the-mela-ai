# Mela Task Radar

An **independent** MCP-over-HTTP worker app that Mela's orchestration brain
dispatches work to. It runs as its own process (default port `8001`), separate
from the Mela backend, and follows the cardinal rule from `ORCHESTRATION.md`:
**Task Radar never depends on Mela to run.** If Mela is down, the worker keeps
serving; its callbacks simply fail and are logged.

## What it does

Exposes one async capability today — **`create_followup_tasks`** — which:

1. Receives a list of work items (`title`, optional `description`, `due_date`,
   `assignee_email`) plus an optional `plan_id` / `bucket_id`.
2. Creates each item as a **Microsoft Planner task** via the Microsoft Graph
   API using an app-only (client-credentials) token.
3. POSTs a `MelaResult`-shaped callback to Mela's
   `POST /api/v1/ingest/result` so the orchestration brain wakes, surfaces a
   worker event in the chat UI, writes the Knowledge Base, and notifies the
   user.

It always returns one callback (`completed` / `partial` / `failed`) so Mela is
never left awaiting.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/` | MCP dispatcher. Header `X-Api-Key: <TASK_RADAR_MCP_API_KEY>`. Body `{"tool": "create_followup_tasks", "arguments": {...}}`. Returns `{"status": "accepted", "task_id": ...}` immediately. |
| `GET` | `/health?deep=true` | Liveness. `deep=true` also probes Graph connectivity. This is the URL Mela's registry health-checks. |

## Run

```bash
cd task-radar
python -m venv venv && source venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.sample .env        # fill in the values
python main.py             # or: uvicorn main:app --host 0.0.0.0 --port 8001
```

Health check: <http://localhost:8001/health?deep=true>

## Environment variables

| Var | Purpose |
|---|---|
| `TASK_RADAR_MCP_API_KEY` | Key Mela presents inbound (`X-Api-Key`). Must match Mela's backend env. |
| `TASK_RADAR_INBOUND_API_KEY` | Key this worker presents on callbacks (`X-Worker-Api-Key`). Must match Mela's `auth_config.inbound_api_key`. |
| `TASK_RADAR_PLANNER_PLAN_ID` | Default Planner plan for task creation. |
| `MELA_INGESTION_BASE_URL` | Mela backend base URL for the callback. |
| `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` / `AZURE_TENANT_ID` | App-only Graph credentials (needs `Tasks.ReadWrite` app permission + admin consent). |
| `PORT` | Listen port (default `8001`). |

## How Mela reaches it

Set these in Mela's backend env so the worker is seeded into the registry:

```
TASK_RADAR_BASE_URL=http://localhost:8001
TASK_RADAR_MCP_API_KEY=<same as worker>
TASK_RADAR_INBOUND_API_KEY=<same as worker>
MELA_INGESTION_BASE_URL=http://localhost:8000
```

Mela's `seed.py` then registers `task-radar` with the `create_followup_tasks`
capability and its generic `MCPAdapter` dispatches to `POST /` here.
