# End-to-End Validation Checklist

A 12-step manual smoke test that exercises every critical path of Mela Task
Radar against a real Microsoft 365 tenant. Run this before every demo, every
deploy, and after any change that touches Graph, the AI extractor, or the
scan runner.

> Time budget: ~15 minutes. If anything in this list fails or is unclear,
> the product is not ready to ship.

---

## 0. Prerequisites

- Backend running: `cd apps/api && uvicorn app.main:app --reload --port 8010`
- Frontend running: `cd apps/web && npm run dev -- -p 2005`
- MCP running (optional for steps 10–11): `cd apps/api && python -m app.mcp.server`
- A Microsoft 365 work account in the configured Entra tenant
  (`AZURE_TENANT_ID`) with at least one Outlook message and one Teams team.
- `.env` populated with `AZURE_OPENAI_*`, `MCP_API_KEY`, `JWT_SECRET`,
  `TOKEN_ENCRYPTION_KEY`, `MICROSOFT_REDIRECT_URI=http://localhost:8012/api/auth/microsoft/callback`.

---

## 1. Login → /dashboard

1. Open `http://localhost:2005/`.
2. Click **Sign in with Microsoft**.
3. Complete Microsoft login + consent.
4. ✅ Browser lands on `http://localhost:2005/dashboard` (not `/`).
5. ✅ User name and email are visible in the top-right.

**Failure modes:** redirect loops, "401" toast, blank dashboard. If the
redirect goes anywhere other than `/dashboard`, check
`apps/api/app/routers/auth.py` callback's `RedirectResponse` target.

---

## 2. Connections page shows Microsoft as connected

1. Navigate to **Settings → Connections**.
2. ✅ Microsoft tile is **Connected**, with token expiry > now.

---

## 3. Configure scan settings

1. Navigate to **Settings → Scan**.
2. Set:
   - Lookback: 24 hours
   - Confidence threshold: 0.55
   - Max messages per scan: 50 (lower for fast iteration)
   - Max AI calls per scan: 30
3. Save.
4. ✅ Toast or saved indicator appears.

---

## 4. Configure Teams

1. Navigate to **Settings → Teams**.
2. ✅ "Joined teams" list loads (no "Reconnect Microsoft" warning).
3. Toggle **Enable Teams scanning** ON.
4. Toggle **Mentions only** ON, **Include thread context** ON.
5. Expand a team, check 1–2 channels.
6. **Save changes**.
7. ✅ Selected count badge updates per team.

---

## 5. Run an Outlook scan

1. Navigate to **Scans**.
2. Click **Run scan** (defaults to source = all).
3. Wait 5–30 seconds, refresh.
4. ✅ Newest row shows status `Completed` or `Completed (errors)`.
5. ✅ Per-stage columns are populated:
   `Scanned`, `Noise`, `Dup`, `AI`, `No-task`, `Tasks`, `Review`, `Errors`.
6. ✅ If any errors, an amber chip shows the category
   (e.g. `ai_rate_limit: 2`, `model_param_unsupported: 1`).

**Failure mode (regression):** `Scanned: N, Tasks: 0, Errors: M` with no
explanation. The 0001 schema or stale extractor is in play. Re-apply
migration `0002_scan_diagnostics` and restart the API.

---

## 6. Open scan detail

1. Click **Details** on the latest scan.
2. ✅ Summary card shows all 12 metric tiles.
3. ✅ "Per-message events" section lists events grouped by stage:
   `graph_fetch`, `noise_filter`, `dedup`, `ai_extract`, `persist`,
   `excel_sync`, `planner_sync` (whichever applied).
4. ✅ Each error event shows category + human message (PII-free).

---

## 7. Run a Teams scan end-to-end

1. Back on **Settings → Teams**, click **Run Teams scan**.
2. Go to **Scans**, watch the new row.
3. ✅ Type column shows `teams`.
4. ✅ At least one event has `source_type: teams_message`.
5. ✅ If any selected channel had a message addressed to you,
   the **Tasks** column is > 0 and a TaskAttachment with type `linked`
   was created for any file mentioned in the message.

---

## 8. Verify Outlook delta-link is being stored

1. In a SQL client (or `sqlite3 taskradar.db`):
   ```sql
   SELECT email_delta_link, last_email_scan_at FROM scan_settings;
   ```
2. ✅ `last_email_scan_at` is recent.
3. ✅ `email_delta_link` is non-NULL after the second consecutive email scan.

---

## 9. Excel sync

1. Open **Settings → Excel**, configure a target workbook (or skip if not
   set up — sync is best-effort).
2. Open a task in **Tasks**, click **Sync to Excel**.
3. ✅ A row is appended in the target workbook within ~5 seconds.
4. ✅ If sync fails, the task page shows the error reason; the scan-run
   row reflects `Excel failed` count > 0.

---

## 10. Planner sync

1. On a task, click **Push to Planner**.
2. ✅ A Planner task is created in the configured plan.
3. ✅ Source-link in the Planner task description points back to the
   originating Outlook/Teams message.

---

## 11. MCP tool probe

```bash
# scan_for_tasks
curl -s -X POST http://localhost:8090/mcp/call \
  -H "Authorization: Bearer $MTR_AGENT_TOKEN" \
  -H "content-type: application/json" \
  -d '{"name":"scan_for_tasks","arguments":{"source":"email"}}'

# get_scan_status
curl -s -X POST http://localhost:8090/mcp/call \
  -H "Authorization: Bearer $MTR_AGENT_TOKEN" \
  -H "content-type: application/json" \
  -d '{"name":"get_scan_status","arguments":{"scan_run_id":"<ID>"}}'
```

1. ✅ Both calls return JSON, not a 500.
2. ✅ Calling **without** a valid Bearer token returns HTTP 401.
3. ✅ Any caller-supplied `user_id` argument is silently overwritten with
   the authenticated user — confirm via DB that returned rows belong only
   to the bearer's user.
4. ✅ Backend logs show `mcp.tool.invoke` and `mcp.tool.ok` lines for each
   call.
5. ✅ No "first user in database" fallback triggered (search backend logs
   for `first user` — should be empty).

---

## 12. Cleanup / regression guard

1. Re-run the Pytest suite:
   ```powershell
   cd apps\api ; python -m pytest -q
   ```
   ✅ All tests pass (currently 51).
2. Check backend logs since startup for any line containing
   `Traceback` — there should be none after a successful end-to-end run.
3. ✅ The README "Deployment" command snippets (backend, frontend, MCP)
   match what's actually running on this machine.

---

If all 12 steps pass, the product is in client-ready state for the
Outlook+Teams scenario. Anything below 12/12 is a release-blocker and
must be fixed before the next demo.
