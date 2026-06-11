# User guide

## 1. Sign in
Open Mela Task Radar and click **Sign in with Microsoft**.

## 2. Connect Microsoft 365
On first login you're taken to **Settings → Connections**. Click **Connect** and
grant the requested permissions (Outlook, OneDrive, Planner, Teams).

## 3. Configure scanning
**Settings → Scan Schedule**: pick scan time (default 7:00 AM), timezone, and
whether to scan email and/or Teams.

**Settings → Teams**: pick teams and channels. By default only @mentions are
scanned.

## 4. Run a scan
On the dashboard click **Run Scan Now**. After ~30s you'll see new tasks in the
**Task Inbox**.

## 5. Review tasks
Tasks under the **Needs Review** filter are low-confidence and won't be
auto-synced. Edit them, mark done, ignore, or send to Planner.

## 6. Sync to Excel
**Settings → Excel** → **Create workbook**. The first sync creates
`TaskInbox.xlsx` in your OneDrive root with a `TaskLog` table. Subsequent syncs
append new rows.

## 7. Send to Planner
Select tasks → **Send to Planner**. Tasks are created in the plan/bucket you
configured under **Settings → Planner**.

## 8. Ask Mela AI
With Mela AI configured to use the Task Radar MCP server, ask things like:
- “What do I have today?”
- “Create Planner tasks for the high-priority ones.”
- “Run a scan now.”
