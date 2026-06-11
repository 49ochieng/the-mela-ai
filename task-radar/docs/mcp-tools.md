# MCP tools

The MCP server runs at `MCP_SERVER_URL` (default `http://localhost:8090`)
and authenticates with **per-user agent tokens** issued from the web UI
(**Settings → Mela connection → Mint token**). Clients send the token in
the standard `Authorization: Bearer <agent_token>` header. Tokens are
prefixed `mtr_at_` so they are easy to spot in logs and revoke.

The legacy shared `MCP_API_KEY` / `X-Api-Key` flow has been removed —
every call is attributed to a real signed-in user, scoped to that user's
tenant, and impersonation across users is impossible.

| Tool | Input | Output |
|------|-------|--------|
| `scan_for_tasks` | `{source: email\|teams\|all, lookback_hours?: int}` | `{scan_run_id, status, summary}` |
| `get_today_tasks` | `{}` | `{tasks: Task[]}` grouped by priority |
| `get_overdue_tasks` | `{}` | `{tasks: Task[]}` |
| `search_tasks` | `{query?, source?, status?, priority?, date_range?}` | `{tasks: Task[]}` |
| `update_task_status` | `{task_id, status}` | `{task: Task}` |
| `sync_tasks_to_excel` | `{task_ids?: string[]}` | `{synced, failed, workbook_url}` |
| `create_planner_task` | `{task_id, plan_id?, bucket_id?}` | `{planner_url, sync_status}` |
| `get_task_brief` | `{date?: ISO date}` | `{summary, counts, top_items}` |
| `get_scan_status` | `{scan_run_id}` | `{status, metrics, errors}` |

Note: `user_id` is **never** accepted from the caller — the MCP server
forces every tool invocation onto the user identified by the bearer
token (see `apps/api/app/mcp/server.py`).

### Example Mela AI call

```bash
curl -X POST "$MCP_SERVER_URL/mcp/call" \
  -H "Authorization: Bearer $MTR_AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"get_today_tasks","arguments":{}}'
```

The MCP server delegates every call to the same FastAPI service used by
the web app, so authorization, tenancy, and audit logging are guaranteed.
