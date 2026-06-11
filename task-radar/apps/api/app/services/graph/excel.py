"""Excel via Graph — multi-sheet TaskInbox.xlsx with idempotent upserts.

Layout:
  Today        — overdue + due-today, sorted by PriorityScore desc
  This Week    — Mon-Sun grouped by day
  Inbox        — most recent N tasks needing triage
  All Tasks    — source-of-truth named table 'Tasks' (Claude-friendly)
  Summary      — counts by priority/status/source
  _Claude_Schema — hidden spec sheet for Claude-for-Excel queries
  _Meta        — hidden last-sync metadata

The "All Tasks" table is the only mutable record store and uses TaskID-based
upsert so re-syncing the same task patches in place rather than appending a
duplicate row. View sheets are rebuilt deterministically on each sync.
"""
from __future__ import annotations

import io
import logging
import urllib.parse as _up
import zipfile
from datetime import datetime, timedelta
from typing import Any

from .client import GraphClient

logger = logging.getLogger(__name__)


def _q(name: str) -> str:
    """URL-encode a sheet/table name for safe use in a Graph path segment."""
    return _up.quote(name, safe="")

WORKBOOK_NAME = "TaskInbox.xlsx"
TABLE_NAME = "Tasks"
SHEET_ALL = "All Tasks"
SHEET_TODAY = "Today"
SHEET_WEEK = "This Week"
SHEET_INBOX = "Inbox"
SHEET_SUMMARY = "Summary"
SHEET_CLAUDE = "_Claude_Schema"
SHEET_META = "_Meta"

COLUMNS = [
    "TaskID", "Source", "ReceivedAt", "From", "Subject",
    "Title", "Description", "TaskType",
    "DueDate", "DueTimeLocal", "Priority", "PriorityScore", "UrgencyBucket",
    "Confidence", "Status",
    "Reasoning", "Evidence", "AttachmentLinks", "SourceLink", "PlannerLink",
    "LastUpdated",
]


# ── workbook bootstrap ────────────────────────────────────────────────
async def find_or_create_task_workbook(client: GraphClient) -> dict[str, Any]:
    try:
        return await client.get(f"/me/drive/root:/{WORKBOOK_NAME}")
    except Exception:
        pass
    content = _empty_xlsx_bytes()
    token = await client._access_token()
    url = (
        f"https://graph.microsoft.com/v1.0/me/drive/root:/{WORKBOOK_NAME}:/content"
    )
    resp = await client._http.put(
        url,
        content=content,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        },
    )
    resp.raise_for_status()
    return resp.json()


def _empty_xlsx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{SHEET_ALL}" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>",
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData/></worksheet>",
        )
    return buf.getvalue()


# ── layout ────────────────────────────────────────────────────────────
async def ensure_workbook_layout(client: GraphClient, workbook_id: str) -> None:
    """Make sure all sheets and the Tasks table exist."""
    existing_sheets = {
        ws["name"]
        for ws in (
            await client.get(f"/me/drive/items/{workbook_id}/workbook/worksheets")
        ).get("value", [])
    }

    desired = [
        SHEET_TODAY, SHEET_WEEK, SHEET_INBOX,
        SHEET_ALL, SHEET_SUMMARY, SHEET_CLAUDE, SHEET_META,
    ]
    for name in desired:
        if name in existing_sheets:
            continue
        await client.post(
            f"/me/drive/items/{workbook_id}/workbook/worksheets/add",
            json={"name": name},
        )

    for name in (SHEET_CLAUDE, SHEET_META):
        try:
            await client.patch(
                f"/me/drive/items/{workbook_id}/workbook/worksheets/{_q(name)}",
                json={"visibility": "Hidden"},
            )
        except Exception:  # noqa: BLE001
            pass

    await _ensure_tasks_table(client, workbook_id)
    await _write_claude_schema(client, workbook_id)
    await _apply_header_formatting(client, workbook_id)


async def _ensure_tasks_table(client: GraphClient, workbook_id: str) -> dict[str, Any]:
    try:
        return await client.get(
            f"/me/drive/items/{workbook_id}/workbook/tables/{TABLE_NAME}"
        )
    except Exception:
        pass
    address = f"A1:{_col_letter(len(COLUMNS))}1"
    await client.patch(
        f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(SHEET_ALL)}')"
        f"/range(address='{address}')",
        json={"values": [COLUMNS]},
    )
    table = await client.post(
        f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(SHEET_ALL)}')/tables/add",
        json={"address": address, "hasHeaders": True},
    )
    await client.patch(
        f"/me/drive/items/{workbook_id}/workbook/tables/{table['id']}",
        json={"name": TABLE_NAME},
    )
    return table


async def _apply_header_formatting(client: GraphClient, workbook_id: str) -> None:
    try:
        await client.patch(
            f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(SHEET_ALL)}')"
            f"/range(address='A1:{_col_letter(len(COLUMNS))}1')/format/font",
            json={"bold": True},
        )
    except Exception:  # noqa: BLE001
        pass


async def _write_claude_schema(client: GraphClient, workbook_id: str) -> None:
    rows = [
        ["Mela Task Radar — Claude-for-Excel schema reference"],
        [""],
        ["Source-of-truth table:", TABLE_NAME, f"on sheet '{SHEET_ALL}'"],
        [""],
        ["Column", "Description", "Example"],
        ["TaskID", "Internal UUID; primary key for upsert.", "uuid"],
        ["Source", "email | teams", "email"],
        ["ReceivedAt", "ISO timestamp of source message", "2026-05-06T10:21:00Z"],
        ["From", "Sender email or display name", "alice@contoso.com"],
        ["Subject", "Email subject or Teams channel name", "Q3 forecast"],
        ["Title", "Concise task title (LLM)", "Send Q3 forecast"],
        ["Description", "Brief 1-2 sentence summary", "..."],
        ["TaskType", "review|respond|create|approve|schedule|forward|follow_up|other", "respond"],
        ["DueDate", "ISO date when extracted, blank otherwise", "2026-05-09"],
        ["DueTimeLocal", "HH:MM if extracted from natural language", "17:00"],
        ["Priority", "high|medium|low (LLM)", "high"],
        ["PriorityScore", "Deterministic 0-100 sortable focus score", "92"],
        ["UrgencyBucket", "Overdue|Today|Tomorrow|ThisWeek|Later|NoDate", "Today"],
        ["Confidence", "0-1 LLM confidence", "0.86"],
        ["Status", "open|in_progress|done|needs_review|ignored|duplicate", "open"],
        ["Reasoning", "Why the LLM judged this priority", "..."],
        ["Evidence", "Short quote from message that grounds the task", "..."],
        ["SourceLink", "Deep link back to the original message", "https://..."],
        ["PlannerLink", "URL of synced Planner task if any", "https://tasks.office.com/..."],
        ["LastUpdated", "Last sync timestamp", "..."],
        [""],
        ["Example questions Claude can answer from this sheet:"],
        ["* What are my top 5 highest PriorityScore tasks today?"],
        ["* Group all Overdue tasks by Source and count them."],
        ["* Show tasks where Status='needs_review' AND Confidence<0.7."],
        ["* Summarize this week's tasks by day and Priority."],
    ]
    addr = f"A1:C{len(rows)}"
    await client.patch(
        f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(SHEET_CLAUDE)}')"
        f"/range(address='{addr}')",
        json={"values": [_pad_row(r, 3) for r in rows]},
    )


# ── upsert ────────────────────────────────────────────────────────────
async def upsert_task_rows(
    client: GraphClient,
    workbook_id: str,
    rows: list[list[Any]],
) -> tuple[int, int]:
    """Insert new rows / update existing ones, keyed on TaskID (column A)."""
    if not rows:
        return 0, 0

    existing = await _read_existing_taskid_to_index(client, workbook_id)

    to_update: list[tuple[int, list[Any]]] = []
    to_insert: list[list[Any]] = []
    for r in rows:
        tid = r[0]
        if tid in existing:
            to_update.append((existing[tid], r))
        else:
            to_insert.append(r)

    for row_index, row in to_update:
        try:
            await client.patch(
                f"/me/drive/items/{workbook_id}/workbook/tables/{TABLE_NAME}"
                f"/rows/itemAt(index={row_index})",
                json={"values": [row]},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Excel row PATCH failed for index %s: %s", row_index, exc)

    BATCH = 100
    for i in range(0, len(to_insert), BATCH):
        batch = to_insert[i : i + BATCH]
        await client.post(
            f"/me/drive/items/{workbook_id}/workbook/tables/{TABLE_NAME}/rows/add",
            json={"values": batch},
        )

    return len(to_insert), len(to_update)


async def _read_existing_taskid_to_index(
    client: GraphClient, workbook_id: str,
) -> dict[str, int]:
    try:
        col = await client.get(
            f"/me/drive/items/{workbook_id}/workbook/tables/{TABLE_NAME}"
            f"/columns('TaskID')/dataBodyRange"
        )
    except Exception:
        return {}
    values = col.get("values") or []
    out: dict[str, int] = {}
    for i, v in enumerate(values):
        if v and v[0]:
            out[str(v[0])] = i
    return out


# ── view sheets (rebuilt every sync) ─────────────────────────────────
async def rebuild_view_sheets(
    client: GraphClient,
    workbook_id: str,
    *,
    last_sync_at: datetime,
    scan_run_id: str | None = None,
) -> None:
    all_rows = await _read_all_tasks(client, workbook_id)

    today = datetime.utcnow().date()

    def _due(r: dict) -> str:
        return r.get("DueDate") or ""

    def _score(r: dict) -> int:
        try:
            return int(r.get("PriorityScore") or 0)
        except Exception:
            return 0

    today_rows = [
        r for r in all_rows
        if r.get("UrgencyBucket") in ("Overdue", "Today")
        and r.get("Status") not in ("done", "ignored", "duplicate")
    ]
    today_rows.sort(key=lambda r: (-_score(r), _due(r)))

    week_rows = [
        r for r in all_rows
        if r.get("UrgencyBucket") in ("Overdue", "Today", "Tomorrow", "ThisWeek")
        and r.get("Status") not in ("done", "ignored", "duplicate")
    ]
    week_rows.sort(key=lambda r: (_due(r), -_score(r)))

    inbox_rows = [r for r in all_rows if r.get("Status") in ("open", "needs_review")]
    inbox_rows.sort(key=lambda r: (r.get("ReceivedAt") or ""), reverse=True)
    inbox_rows = inbox_rows[:50]

    await _write_view_sheet(
        client, workbook_id, SHEET_TODAY,
        ["Priority", "Title", "Due", "Source", "From", "Score", "Reasoning", "SourceLink"],
        [
            [
                r.get("Priority"), r.get("Title"), r.get("DueDate"),
                r.get("Source"), r.get("From"), r.get("PriorityScore"),
                r.get("Reasoning"), r.get("SourceLink"),
            ]
            for r in today_rows
        ],
    )
    await _write_view_sheet(
        client, workbook_id, SHEET_WEEK,
        ["Day", "Priority", "Title", "Due", "Source", "From", "Score", "SourceLink"],
        [
            [
                _day_label(r.get("DueDate"), today),
                r.get("Priority"), r.get("Title"), r.get("DueDate"),
                r.get("Source"), r.get("From"), r.get("PriorityScore"),
                r.get("SourceLink"),
            ]
            for r in week_rows
        ],
    )
    await _write_view_sheet(
        client, workbook_id, SHEET_INBOX,
        ["Status", "Priority", "Title", "Source", "From", "ReceivedAt", "Confidence", "SourceLink"],
        [
            [
                r.get("Status"), r.get("Priority"), r.get("Title"),
                r.get("Source"), r.get("From"), r.get("ReceivedAt"),
                r.get("Confidence"), r.get("SourceLink"),
            ]
            for r in inbox_rows
        ],
    )
    await _write_summary(client, workbook_id, all_rows)
    await _write_meta(client, workbook_id, last_sync_at, scan_run_id, len(all_rows))


async def _read_all_tasks(client: GraphClient, workbook_id: str) -> list[dict[str, Any]]:
    try:
        rng = await client.get(
            f"/me/drive/items/{workbook_id}/workbook/tables/{TABLE_NAME}/range"
        )
    except Exception:
        return []
    vals = rng.get("values") or []
    if len(vals) < 2:
        return []
    headers = vals[0]
    out: list[dict[str, Any]] = []
    for row in vals[1:]:
        out.append({headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))})
    return out


async def _write_view_sheet(
    client: GraphClient, workbook_id: str, sheet: str,
    headers: list[str], rows: list[list[Any]],
) -> None:
    try:
        await client.post(
            f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(sheet)}')"
            "/usedRange/clear",
            json={"applyTo": "Contents"},
        )
    except Exception:  # noqa: BLE001
        pass
    matrix = [headers] + rows if rows else [headers, [""] * len(headers)]
    height = len(matrix)
    width = len(headers)
    addr = f"A1:{_col_letter(width)}{height}"
    await client.patch(
        f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(sheet)}')"
        f"/range(address='{addr}')",
        json={"values": matrix},
    )
    try:
        await client.patch(
            f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(sheet)}')"
            f"/range(address='A1:{_col_letter(width)}1')/format/font",
            json={"bold": True},
        )
    except Exception:  # noqa: BLE001
        pass


async def _write_summary(
    client: GraphClient, workbook_id: str, rows: list[dict[str, Any]],
) -> None:
    by_priority: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    by_status: dict[str, int] = {}
    by_source: dict[str, int] = {"email": 0, "teams": 0}
    by_bucket: dict[str, int] = {}
    for r in rows:
        p = str(r.get("Priority") or "").lower()
        by_priority[p] = by_priority.get(p, 0) + 1
        st = str(r.get("Status") or "")
        by_status[st] = by_status.get(st, 0) + 1
        src = str(r.get("Source") or "").lower()
        by_source[src] = by_source.get(src, 0) + 1
        b = str(r.get("UrgencyBucket") or "")
        by_bucket[b] = by_bucket.get(b, 0) + 1

    matrix: list[list[Any]] = [
        ["Mela Task Radar — Summary"],
        [""],
        ["By Priority", "", "By Status", "", "By Source", "", "By Urgency", ""],
    ]
    pri = list(by_priority.items())
    sta = sorted(by_status.items())
    src = sorted(by_source.items())
    bkt = sorted(by_bucket.items())
    height = max(len(pri), len(sta), len(src), len(bkt))
    for i in range(height):
        matrix.append([
            pri[i][0] if i < len(pri) else "", pri[i][1] if i < len(pri) else "",
            sta[i][0] if i < len(sta) else "", sta[i][1] if i < len(sta) else "",
            src[i][0] if i < len(src) else "", src[i][1] if i < len(src) else "",
            bkt[i][0] if i < len(bkt) else "", bkt[i][1] if i < len(bkt) else "",
        ])
    width = 8
    matrix = [_pad_row(r, width) for r in matrix]
    addr = f"A1:{_col_letter(width)}{len(matrix)}"
    try:
        await client.post(
            f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(SHEET_SUMMARY)}')"
            "/usedRange/clear",
            json={"applyTo": "Contents"},
        )
    except Exception:  # noqa: BLE001
        pass
    await client.patch(
        f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(SHEET_SUMMARY)}')"
        f"/range(address='{addr}')",
        json={"values": matrix},
    )


async def _write_meta(
    client: GraphClient, workbook_id: str, last_sync_at: datetime,
    scan_run_id: str | None, total_tasks: int,
) -> None:
    matrix = [
        ["Key", "Value"],
        ["last_sync_at", last_sync_at.isoformat()],
        ["scan_run_id", scan_run_id or ""],
        ["total_tasks", total_tasks],
        ["workbook_version", "1"],
    ]
    addr = f"A1:B{len(matrix)}"
    try:
        await client.post(
            f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(SHEET_META)}')"
            "/usedRange/clear",
            json={"applyTo": "Contents"},
        )
    except Exception:  # noqa: BLE001
        pass
    await client.patch(
        f"/me/drive/items/{workbook_id}/workbook/worksheets('{_q(SHEET_META)}')"
        f"/range(address='{addr}')",
        json={"values": matrix},
    )


# ── helpers ───────────────────────────────────────────────────────────
def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _pad_row(row: list[Any], width: int) -> list[Any]:
    if len(row) >= width:
        return row[:width]
    return row + [""] * (width - len(row))


def _day_label(due_iso: str | None, today) -> str:
    if not due_iso:
        return ""
    try:
        d = datetime.fromisoformat(str(due_iso).replace("Z", "")).date()
    except Exception:
        return str(due_iso)
    delta = (d - today).days
    if delta < 0:
        return f"Overdue ({d.strftime('%a %b %d')})"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return d.strftime("%a %b %d")


async def get_workbook_url(client: GraphClient, workbook_id: str) -> str | None:
    item = await client.get(f"/me/drive/items/{workbook_id}")
    return item.get("webUrl")


# ── back-compat shims ────────────────────────────────────────────────
async def ensure_tasklog_table(client: GraphClient, workbook_id: str) -> dict[str, Any]:
    """Legacy name retained — now ensures full multi-sheet layout."""
    await ensure_workbook_layout(client, workbook_id)
    return await client.get(
        f"/me/drive/items/{workbook_id}/workbook/tables/{TABLE_NAME}"
    )


async def append_task_rows(
    client: GraphClient, workbook_id: str, rows: list[list[Any]],
) -> int:
    """Legacy name retained — delegates to upsert_task_rows."""
    inserted, updated = await upsert_task_rows(client, workbook_id, rows)
    return inserted + updated
