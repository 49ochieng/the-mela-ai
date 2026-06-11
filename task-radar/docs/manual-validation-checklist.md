# Mela Task Radar — Manual Validation Checklist (MVP)

Use this checklist to validate the MVP end-to-end with real Microsoft 365
credentials, Azure OpenAI GPT-5.2, OneDrive, and Microsoft Planner.

> Estimated time: 30–45 min for first run, 5 min for subsequent runs.

---

## 0. Prerequisites

- Windows or macOS dev box, Python **3.11+**, Node **20+**, Git
- An Azure subscription and a Microsoft 365 tenant where you can create app
  registrations
- An Azure OpenAI resource with a **`gpt-5.2-chat`** deployment
- (Optional) An empty Microsoft Planner plan in a group you belong to

---

## 1. Create the Microsoft Entra ID app registration

1. Open https://entra.microsoft.com → **Applications → App registrations → New registration**
2. Name: `Mela Task Radar (Dev)`
3. Supported account types: **Single tenant** (or multi-tenant if you prefer)
4. Redirect URI:
   - Platform: **Web**
   - URL: `http://localhost:8000/api/auth/microsoft/callback`
5. Click **Register**.
6. Copy:
   - `Application (client) ID` → `AZURE_CLIENT_ID`
   - `Directory (tenant) ID` → `AZURE_TENANT_ID`
7. **Certificates & secrets → New client secret** → copy the **Value** →
   `AZURE_CLIENT_SECRET`.

## 2. Configure Graph API permissions

Under **API permissions → Add a permission → Microsoft Graph → Delegated**,
add **all** of these (MVP set):

| Permission         | Why                                            |
| ------------------ | ---------------------------------------------- |
| `User.Read`        | Sign-in identity                               |
| `offline_access`   | Refresh tokens                                 |
| `Mail.Read`        | Scan Outlook                                   |
| `Files.ReadWrite`  | Create / update `TaskInbox.xlsx` in OneDrive   |
| `Tasks.ReadWrite`  | Create Microsoft Planner tasks                 |
| `Group.Read.All`   | Discover Planner plans across groups           |

Click **Grant admin consent for <tenant>**.

> **Phase 2 (do not add yet):** `Team.ReadBasic.All`, `Channel.ReadBasic.All`,
> `ChannelMessage.Read.All`.

## 3. Fill `.env`

```powershell
cd "c:\copilot\Mela Task Radar"
copy .env.example apps\api\.env
```

Open `apps/api/.env` and set at minimum:

```
AZURE_TENANT_ID=<from step 1>
AZURE_CLIENT_ID=<from step 1>
AZURE_CLIENT_SECRET=<from step 1>
AZURE_OPENAI_ENDPOINT=https://<your-aoai>.openai.azure.com
AZURE_OPENAI_API_KEY=<aoai key>
AZURE_OPENAI_DEPLOYMENT_GPT52=gpt-5.2-chat
TOKEN_ENCRYPTION_KEY=<paste output of: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
SECRET_KEY=<32+ random chars>
JWT_SECRET=<32+ random chars>
QUEUE_PROVIDER=memory
```

Leave `DATABASE_URL` as the default SQLite URL for the first run.

## 4. Start backend, frontend, MCP server

> With `QUEUE_PROVIDER=memory` the API auto-runs the scan worker in-process —
> you only need **three** terminals for MVP local testing.

**Terminal 1 — API + worker (lifespan):**
```powershell
cd apps\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

You should see in the logs: `In-process worker starting (QUEUE_PROVIDER=memory)`.

**Terminal 2 — MCP server (HTTP on :8090):**
```powershell
cd apps\api
.\.venv\Scripts\Activate.ps1
python -m app.mcp.server
```

**Terminal 3 — Frontend:**
```powershell
cd apps\web
npm install
npm run dev   # http://localhost:2005
```

**Optional Terminal 4 — Daily 7 AM scheduler:**
```powershell
cd apps\api
.\.venv\Scripts\Activate.ps1
python -m app.scheduler.scheduler
```

## 5. Sign in with Microsoft

1. Open http://localhost:2005
2. Click **Sign in with Microsoft**
3. Consent to the requested scopes
4. You should land on `/dashboard`

✅ **Acceptance:** Tenant + User + ScanSettings + GraphConnection rows exist
in the DB. `GET /api/me` returns your profile when called with the bearer
token from `localStorage["mtr.token"]`.

## 6. Send three test emails *to yourself*

| # | Subject                                      | Body                                                                           |
|---|----------------------------------------------|--------------------------------------------------------------------------------|
| A | `Please review the Q3 deck by Friday`        | "Hi, please review the attached Q3 deck and send feedback by Friday."          |
| B | `FYI: Q2 results published`                  | "Sharing the Q2 results page for your awareness — no action needed."           |
| C | `URGENT: Approve invoice #4421 today`        | "Need your approval on invoice 4421 by EOD today, blocking vendor payment."    |

Optionally attach a small PDF to email A.

## 7. Run **Scan Now**

1. Go to **Dashboard → Run scan now**
2. Or call `POST /api/scans/run` with `{"source":"email"}` and the bearer token
3. Within a few seconds the scan_run row should reach status `completed`

✅ **Acceptance:**
- Email A → 1 task, priority **medium/high**, due_date = next Friday
- Email B → **0 tasks** (FYI ignored)
- Email C → 1 task, priority **high**
- If A had an attachment, a `task_attachments` row exists

Verify via `GET /api/tasks` or `/tasks` page.

## 8. Sync to Excel

1. Open **Tasks** page
2. Click **Sync to Excel** (or `POST /api/excel/sync` with empty body)

✅ **Acceptance:**
- `TaskInbox.xlsx` exists in your OneDrive root (open OneDrive web)
- The workbook contains a worksheet `Tasks` with a table named `TaskLog`
- The `TaskLog` table has 15 columns (`Source, DateReceived, From, Subject,
  TaskDescription, TaskType, DueDate, DueDateRaw, Priority,
  PriorityReasoning, AttachmentLinks, Status, MessageId, ConversationId,
  SourceLink`)
- Rows for each synced task are appended

## 9. Connect Planner & create a task

1. Go to **Settings → Planner**
2. Pick a Plan and Bucket from the dropdowns (populated from
   `GET /api/planner/plans` and the buckets endpoint)
3. Save settings (this is the **approval-first** configuration)
4. Open a Task detail page → **Send to Planner**

✅ **Acceptance:**
- A new Planner task appears in the chosen plan/bucket with the matching title
  and description
- A `task_syncs` row exists with `target_type=planner`, `sync_status=synced`
  and a Planner URL

## 10. Trigger the daily 7 AM scheduler (smoke test)

To smoke-test without waiting until 7 AM, set `scan_time_local` to one
minute from now via:

```powershell
$body = '{"daily_scan_enabled": true, "scan_time_local": "23:42:00", "timezone": "UTC"}'
curl -X PATCH http://localhost:8000/api/settings/scan -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d $body
```

Then start `python -m app.scheduler.scheduler` and wait for that minute. A new
`scan_runs` row should appear with `source_scope.trigger == "schedule"`.

✅ **Acceptance:** Scheduler creates a scan_run for that minute and the
in-process worker drains it.

## 11. Call MCP — `get_today_tasks`

First mint a per-user agent token from the web UI (Settings → Mela
connection → Mint token). Treat the returned `mtr_at_…` value like a
password. Then:

```powershell
$env:MTR_AGENT_TOKEN = "mtr_at_<your-token>"
curl -X POST http://localhost:8090/mcp/call `
  -H "Authorization: Bearer $env:MTR_AGENT_TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"tool":"get_today_tasks","arguments":{}}'
```

✅ **Acceptance:** JSON response with `{ ok: true, result: { tasks_by_priority,
total } }` showing only **your** tasks.

```powershell
curl -X POST http://localhost:8090/mcp/call `
  -H "Authorization: Bearer $env:MTR_AGENT_TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"tool":"scan_for_tasks","arguments":{"source":"email"}}'
```

✅ **Acceptance:** Returns `scan_run_id` and `status=pending`. The in-process
worker picks it up.

## 12. Confirm Mela can use the response
Authorization: Bearer mtr_at_…
The shape returned by `get_today_tasks`, `get_overdue_tasks`,
`get_task_brief` is documented in `docs/mcp-tools.md` and is stable across
calls. Mela can call either:

- **MCP HTTP**: `POST :8090/mcp/call` with `X-Api-Key`
- **Mela REST mirror**: `POST :8000/api/mela/tools/...` with the user's bearer
  JWT (per-user scoping enforced)

---

## Common pitfalls

| Symptom                                     | Fix                                                          |
| ------------------------------------------- | ------------------------------------------------------------ |
| `NeedsReconnect` on first scan              | You skipped admin consent — re-run step 2                    |
| Excel sync 403                              | Ensure `Files.ReadWrite` was admin-consented                 |
| Planner plans list is empty                 | Ensure `Group.Read.All` granted, and you belong to ≥1 group  |
| AI returns `has_task=false` always          | Check `AZURE_OPENAI_DEPLOYMENT_GPT52` matches your deployment name |
| Worker job stays `pending`                  | Confirm log line `In-process worker starting`. Don't run a separate worker process when `QUEUE_PROVIDER=memory` |
| Scheduler creates duplicate runs            | Idempotency key is `created_at >= now.replace(second=0)`. Don't run two scheduler processes |

---

## 12. Microsoft Teams scanning (MVP)

Teams scanning is part of MVP. Validate end-to-end with at least one team and
one channel where you can post a test message.

### 12.1 Permissions

- [ ] In Entra ID app registration, ensure delegated scopes include
  `Team.ReadBasic.All`, `Channel.ReadBasic.All`, and
  `ChannelMessage.Read.All`. Grant admin consent.
- [ ] Reconnect Microsoft from Settings → Connections after consent.

### 12.2 Channel selection

- [ ] Open Settings → Teams. The page lists the teams you have joined.
- [ ] Expand a team. Channels load. Tick at least one channel. Save.
- [ ] Toggle "Only when I'm @mentioned" off for the first run so you can
  validate any new message picks up.
- [ ] Toggle "Include thread replies" on.

### 12.3 Seed a test message

In the selected Teams channel, post:

> **@Edgar can you update the project tracker with the new milestone dates by
> tomorrow morning?**

(Replace @Edgar with the signed-in user's display name so you can also
exercise the mention-fallback path.)

### 12.4 Run a Teams-only scan

- [ ] Settings → Teams → click **Run Teams scan**, OR
- [ ] In Mela, say: *"Scan Teams for tasks"*.
- [ ] In the Scans list, the new run shows **Source = Teams** and finishes as
  `completed` (not `completed_with_errors`).
- [ ] Open the scan detail. Stages `graph_fetch` → `normalize` →
  `ai_extract` → `persist` show success rows. `teams_messages_fetched`
  is ≥ 1 and `teams_tasks_created` is ≥ 1.

### 12.5 Task surface

- [ ] In **Tasks**, the new task carries the **Teams** source badge and the
  team / channel under the title.
- [ ] Open the task. The **Teams details** card shows Team, Channel, Sender,
  Posted time, and **Mentioned you = Yes** for the seeded message.
- [ ] Click **Open in Teams**. The deep link opens the original message in
  the Teams desktop or web client.

### 12.6 Mentions-only mode

- [ ] In Settings → Teams, switch **Only when I'm @mentioned** on.
- [ ] Post a non-mentioning message in the same channel ("FYI: numbers refreshed").
- [ ] Re-run the Teams scan.
- [ ] The non-mentioning message must NOT produce a task. Confirm via the
  Scan detail (it appears under `noise_filter` or `skipped`).

### 12.7 Mela voice + REST + MCP

- [ ] *"What Teams tasks do I have today?"* — only Teams-sourced tasks.
- [ ] *"Show me tasks from Teams"* — same filter from the search route.
- [ ] *"Create Planner tasks for the high-priority Teams items"* — pushes
  the Teams task to Planner; **Sync status** card shows `synced`.
- [ ] REST: `GET /api/mela/tools/tasks/today?source=teams` — Teams only.
- [ ] REST: `GET /api/mela/tools/tasks/overdue?source=teams` — Teams only.
- [ ] MCP: `POST /mcp/call` with `{"name":"get_today_tasks","arguments":{"user_id":"<uuid>","source":"teams"}}`
  returns Teams tasks only and includes `"source":"teams"` per item.
- [ ] MCP: `{"name":"search_tasks","arguments":{"user_id":"<uuid>","query":"tracker","source":"teams"}}`
  returns the seeded Teams task.

### 12.8 Permission failure path

- [ ] In Entra, temporarily revoke admin consent for `ChannelMessage.Read.All`.
  Reconnect Microsoft.
- [ ] Re-run the Teams scan. The scan finishes as `completed_with_errors`
  and the Scan detail shows an event with category
  `graph_permission_missing` and a clear message.
- [ ] Restore consent and confirm the next scan succeeds.
