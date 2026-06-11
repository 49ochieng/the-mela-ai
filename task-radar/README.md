# Mela Task Radar

AI-powered task intelligence for Microsoft 365. Scans Outlook + Teams, extracts
actionable tasks with GPT-5.2, stores them in a central DB, and syncs to
Excel + Planner. Exposes REST + MCP tools so Mela AI can drive it.

## Repo layout

```
mela-task-radar/
  apps/
    api/          # FastAPI backend (REST + MCP + workers + scheduler)
    web/          # Next.js 14 frontend (App Router, TS, Tailwind)
  infra/azure/    # Bicep templates
  docs/           # Architecture, deployment, MCP, permissions, user guide
  env/            # .env.dev (gitignored, real secrets)
  .env.example
  docker-compose.yml
```

## Quick start (local dev)

> **Authentication.** Sign-in uses **Microsoft Entra ID** with the **PKCE auth-code flow**. After a successful sign-in the API sets an **httpOnly session cookie** (`mtr_session`) and redirects to the web app's `/dashboard`. The frontend reads `/api/me` with `credentials: "include"` and gates protected routes via the `(app)` route group. Logout clears the cookie via `POST /api/auth/logout`.

### 1. Backend

```powershell
cd apps/api
python -m venv .venv ; .\.venv\Scripts\activate
pip install -r requirements.txt
copy ..\..\env\.env.local .env          # or maintain your own
alembic upgrade head
# Run on port 8012 to match the Microsoft redirect URI in env/.env.local.
# Avoid --reload while debugging auth so the in-memory MSAL flow cache survives.
uvicorn app.main:app --port 8012
```

Optional separate processes:

```powershell
python -m app.mcp.server                # MCP server on :8090
python -m app.scheduler.scheduler       # daily scan trigger
# Worker runs in-process when QUEUE_PROVIDER=memory (the dev default).
```

### 2. Frontend

```powershell
cd apps/web
npm install
npm run dev                             # http://localhost:2005
```

Then open <http://localhost:2005>, click **Sign in with Microsoft 365**,
and you'll be taken to the dashboard.

### 3. Docker (everything together)

```bash
docker compose up --build
```

## Environment

Copy `.env.example` to `.env` (root) or use the existing `env/.env.dev` as a
template. See `docs/deployment-azure.md` for production secrets management
(Key Vault).

## MVP validation

- [docs/readiness-report.md](docs/readiness-report.md) — per-area audit of
  what is production-ready vs. needs-real-credential-test.
- [docs/manual-validation-checklist.md](docs/manual-validation-checklist.md)
  — 12-step end-to-end walkthrough using real Microsoft 365 + Azure OpenAI.

Run the test suite (37 tests, all passing):

```bash
cd apps/api
.\.venv\Scripts\activate
pytest
```

## MVP scope

In: Outlook scanning, GPT-5.2 extraction, Excel sync to OneDrive, Microsoft
Planner sync, daily scheduler, MCP tools, dashboard UI.

Phase 2 (flags off by default): Teams private-chat / channel scanning,
real-time Graph webhooks, semantic dedup, Jira / ClickUp connectors,
analytics dashboards.

## Documentation

- [docs/architecture.md](docs/architecture.md)
- [docs/deployment-azure.md](docs/deployment-azure.md)
- [docs/graph-permissions.md](docs/graph-permissions.md)
- [docs/mcp-tools.md](docs/mcp-tools.md)
- [docs/user-guide.md](docs/user-guide.md)

## Success criteria

See the master spec — all 18 success criteria are wired through the app.
MVP exclusions (semantic dedupe, Teams private chat, Jira/ClickUp,
realtime webhooks) are intentionally not built but their seams exist.
