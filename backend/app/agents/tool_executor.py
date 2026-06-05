"""
Mela AI - Agent Tool Executor

Graph tools (email, calendar, Planner) use app-only tokens:
  1. Backend authenticates as AZURE_CLIENT_ID (enterprise app)
  2. Graph API calls use /users/{user.email}/... endpoints
  3. No OBO or delegated token required from the frontend
"""

import logging
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.mode import UserSession
from app.schemas.auth import UserInfo

logger = logging.getLogger(__name__)

# Graph tools that are blocked in personal mode
_BLOCKED_GRAPH_TOOLS = frozenset({
    "get_inbox",
    "send_email",
    "create_draft_email",
    "send_draft_email",
    "search_emails",
    "get_email_details",
    "get_email_thread",
    "reply_to_email",
    "get_calendar",
    "search_graph",
    "schedule_meeting",
    "check_availability",
    "list_planner_tasks",
    "create_task",
    "onboard_user",
})

# Lazy imports to avoid circular dependencies:
# tool_executor → graph_service → services.__init__ → chat_service → tool_executor


def _get_graph_service():
    from app.services.graph_service import graph_service
    return graph_service


def _get_code_interpreter():
    from app.services.code_interpreter_service import code_interpreter
    return code_interpreter


def _get_openai_service():
    from app.services.openai_service import openai_service
    return openai_service


# ── Tool definitions for OpenAI function calling ──────────────────────────────

TOOLS = [
    # ── EMAIL ─────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_inbox",
            "description": (
                "Read the signed-in user's inbox. Returns recent emails with "
                "sender, subject, preview, and received time. "
                "Use this when the user asks to check their email, read their inbox, "
                "or see recent messages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of emails to return (default 10, max 50)",
                        "default": 10,
                    },
                    "filter": {
                        "type": "string",
                        "description": (
                            "Optional OData filter string, e.g. "
                            "\"isRead eq false\" or \"from/emailAddress/address eq 'alice@contoso.com'\""
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Send an email immediately as the signed-in user via Microsoft Graph. "
                "Use this tool when:\n"
                "1. EXPLICIT DIRECT SEND: The user explicitly says to send directly, "
                "e.g. 'send this directly', 'send without draft', 'go ahead and send'. "
                "Do not supply workflow_type.\n"
                "2. USER-CONFIRMED SEND: The user has already seen the draft (via "
                "create_draft_email) and explicitly approved it with words like "
                "'yes send it', 'go ahead', 'send now'. Do not supply workflow_type.\n"
                "3. AUTOMATED WORKFLOW: workflow_type is explicitly set to an approved value "
                "('onboarding', 'offboarding', 'system_notification', 'automated_report').\n"
                "For all other cases — new email requests without explicit send confirmation — "
                "use create_draft_email first so the user can review."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of recipient email addresses",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line",
                    },
                    "body": {
                        "type": "string",
                        "description": (
                            "Email body — plain text or markdown. Write the full polished body "
                            "including greeting and sign-off. Do NOT include raw HTML tags."
                        ),
                    },
                    "cc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional CC recipients",
                    },
                    "bcc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional BCC recipients",
                    },
                    "workflow_type": {
                        "type": "string",
                        "enum": ["onboarding", "offboarding", "system_notification", "automated_report"],
                        "description": (
                            "Set only for automated workflow sends. Omit for user-confirmed sends."
                        ),
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_draft_email",
            "description": (
                "Compose and save a draft email to the user's Drafts folder WITHOUT sending it. "
                "Use this first when the user asks to compose, write, or send an email — "
                "unless they explicitly say 'send directly' or 'send without reviewing'. "
                "Show the draft content to the user and ask for confirmation before sending. "
                "The draft_id in the result is used to send later with send_draft_email."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of recipient email addresses",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line",
                    },
                    "body": {
                        "type": "string",
                        "description": (
                            "Email body content — plain text or markdown. "
                            "Write the full, polished email body here including greeting and sign-off. "
                            "Do NOT include raw HTML tags like <br> — use line breaks and markdown instead."
                        ),
                    },
                    "cc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional CC recipients",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_draft_email",
            "description": (
                "Send an existing draft email that was previously saved with create_draft_email. "
                "ONLY call this after the user has explicitly confirmed they want to send the draft. "
                "Requires the draft_id returned by create_draft_email."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "The draft message ID returned by create_draft_email",
                    },
                    "to_summary": {
                        "type": "string",
                        "description": "Human-readable summary of recipients for confirmation message, e.g. 'alice@example.com'",
                    },
                    "user_confirmation": {
                        "type": "string",
                        "description": (
                            "The user's verbatim confirmation phrase from the most recent "
                            "turn (e.g. 'yes send it'). Required — without it the call is rejected."
                        ),
                    },
                },
                "required": ["draft_id", "user_confirmation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": (
                "Search the signed-in user's mailbox. Use this for queries like: "
                "'find emails from John about budget', "
                "'show my unread emails', "
                "'show important emails', "
                "'show flagged emails', "
                "'emails from Mary last week', "
                "'emails with attachments from finance'. "
                "Supports searching by sender name or address, subject, body content, "
                "unread/read status, flagged/important status, date range, folder, and attachments. "
                "Returns matching emails with subject, sender, preview, read/flag status, and ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Free-text search across subject, body, and sender (KQL). "
                            "Examples: 'budget approval', 'project kickoff', 'invoice'"
                        ),
                    },
                    "from_address": {
                        "type": "string",
                        "description": "Filter by exact sender email address, e.g. 'alice@contoso.com'",
                    },
                    "from_name": {
                        "type": "string",
                        "description": (
                            "Search by sender display name (partial match). "
                            "E.g. 'John' or 'Mary Smith'. Use this when the user says 'emails from John'."
                        ),
                    },
                    "subject_contains": {
                        "type": "string",
                        "description": "Search within subject line only, e.g. 'Q3 report'",
                    },
                    "folder": {
                        "type": "string",
                        "description": (
                            "Folder to search: 'inbox', 'sentitems', 'drafts', "
                            "'deleteditems', 'archive', 'junkemail'. "
                            "Leave empty to search all mail."
                        ),
                    },
                    "is_unread": {
                        "type": "boolean",
                        "description": "If true, return only unread emails. Use for 'show unread emails'.",
                    },
                    "is_important": {
                        "type": "boolean",
                        "description": "If true, return only high-importance emails.",
                    },
                    "is_flagged": {
                        "type": "boolean",
                        "description": "If true, return only flagged/starred emails.",
                    },
                    "has_attachments": {
                        "type": "boolean",
                        "description": "If true, return only emails with attachments.",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start date filter, ISO format YYYY-MM-DD",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date filter, ISO format YYYY-MM-DD",
                    },
                    "top": {
                        "type": "integer",
                        "description": "Max results to return (default 20, max 50)",
                        "default": 20,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_details",
            "description": (
                "Retrieve the full body and metadata of a specific email by its ID. "
                "Use this after search_emails or get_inbox to read the complete "
                "content of an email — needed for questions like "
                "'what exactly did she say', 'summarise this email', "
                "'extract tasks from this email'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "The message ID returned by search_emails or get_inbox",
                    },
                },
                "required": ["email_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_thread",
            "description": (
                "Retrieve the full conversation thread for an email — all replies, "
                "forwards, and prior messages in the same conversation. "
                "Use this to summarise an entire email exchange or understand context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "ID of any message in the thread",
                    },
                    "top": {
                        "type": "integer",
                        "description": "Max thread messages to return (default 20)",
                        "default": 20,
                    },
                },
                "required": ["email_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_email",
            "description": (
                "Send a reply to a specific email. Use this when the user wants "
                "to respond to an email they received. The reply maintains the "
                "original thread. Write professional, well-formatted content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "ID of the email to reply to",
                    },
                    "body": {
                        "type": "string",
                        "description": "Reply body — plain text or markdown. Write the full reply including greeting and sign-off. Do NOT include raw HTML tags.",
                    },
                    "reply_all": {
                        "type": "boolean",
                        "description": "Reply to all recipients (default false — reply to sender only)",
                        "default": False,
                    },
                    "cc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional CC recipients to add",
                    },
                    "save_as_draft": {
                        "type": "boolean",
                        "description": "Save as draft instead of sending (default false)",
                        "default": False,
                    },
                },
                "required": ["email_id", "body"],
            },
        },
    },
    # ── CALENDAR ──────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_calendar",
            "description": "Get the signed-in user's upcoming calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of days ahead to look (default 7)",
                        "default": 7,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_meeting",
            "description": (
                "Schedule a meeting or event in the signed-in user's calendar. "
                "Creates a Teams online meeting by default."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Meeting subject/title",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start time in ISO 8601 format (e.g. 2024-01-15T10:00:00)",
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Meeting duration in minutes (default 60)",
                        "default": 60,
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of attendee email addresses",
                    },
                    "description": {
                        "type": "string",
                        "description": "Meeting description or agenda",
                    },
                    "location": {
                        "type": "string",
                        "description": "Physical location (room, address). Omit for online meetings.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone (e.g. 'America/New_York'). Defaults to UTC.",
                        "default": "UTC",
                    },
                    "is_teams_meeting": {
                        "type": "boolean",
                        "description": "Whether to create a Teams online meeting link (default true)",
                        "default": True,
                    },
                },
                "required": ["subject", "start_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Check availability of one or more people for scheduling.",
            "parameters": {
                "type": "object",
                "properties": {
                    "emails": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Email addresses to check availability for",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date to check in ISO format (e.g. 2024-01-15)",
                    },
                },
                "required": ["emails", "date"],
            },
        },
    },
    # ── PLANNER / TASKS ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_planner_tasks",
            "description": (
                "List Microsoft Planner tasks assigned to or created by the signed-in user, "
                "or tasks in a specific plan. "
                "Use this when the user asks about their tasks, to-dos, or Planner items."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "string",
                        "description": (
                            "Optional Planner plan ID to filter tasks. "
                            "If omitted, returns all tasks assigned to the signed-in user."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Create a task for the signed-in user. "
                "If a plan_id is provided, creates a Planner task in that plan. "
                "Otherwise creates a Microsoft To Do task in the user's default task list. "
                "Use this when the user asks to add or create a task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Task title",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Due date in ISO format (e.g. 2024-01-20)",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Additional notes or description for the task",
                    },
                    "plan_id": {
                        "type": "string",
                        "description": (
                            "Microsoft Planner plan ID. "
                            "If provided, creates a Planner task (enterprise). "
                            "If omitted, creates a To Do task (personal)."
                        ),
                    },
                    "assigned_to": {
                        "type": "string",
                        "description": "Entra Object ID (OID) of the user to assign the Planner task to",
                    },
                },
                "required": ["title"],
            },
        },
    },
    # ── ENTERPRISE SEARCH ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search Armely's enterprise knowledge base, including SharePoint "
                "document libraries (armely.sharepoint.com), OneDrive files, and "
                "the armely.com website. Use this for ANY question about Armely's "
                "services, products, team, clients, policies, projects, or any "
                "company-specific information. Also use when the user asks to find, "
                "look up, or search for internal documents or company information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query describing what to find",
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["sharepoint", "org_website", "onedrive"],
                        "description": (
                            "Optional: filter by source. "
                            "'sharepoint' for SP document libraries, "
                            "'org_website' for armely.com pages, "
                            "'onedrive' for personal files."
                        ),
                    },
                    "file_type": {
                        "type": "string",
                        "description": "Optional: filter by file type (pdf, docx, pptx, etc.)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    # ── GRAPH SEARCH (live file discovery) ────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "search_graph",
            "description": (
                "Search Microsoft 365 for recent or newly created files across "
                "SharePoint and OneDrive using the Microsoft Graph Search API. "
                "Use this when the user asks for the latest or freshest version "
                "of a file, or when the indexed knowledge base might be stale."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "File search query (keywords, file name, etc.)",
                    },
                    "top": {
                        "type": "integer",
                        "description": "Max results (default 5, max 25)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    # ── ONBOARDING ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "onboard_user",
            "description": (
                "Automate the full onboarding workflow for a new employee. "
                "Sends a personalised welcome email, schedules an orientation meeting, "
                "and creates onboarding tasks in Microsoft Planner. "
                "Use this when a user or admin asks to onboard a new hire, welcome a new employee, "
                "or set up someone's first week. "
                "All steps are best-effort — partial success is reported clearly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "new_user_email": {
                        "type": "string",
                        "description": "Email address of the new employee to onboard.",
                    },
                    "new_user_name": {
                        "type": "string",
                        "description": "Full name of the new employee.",
                    },
                    "department": {
                        "type": "string",
                        "description": "Department or team the new employee is joining (optional).",
                    },
                    "manager_email": {
                        "type": "string",
                        "description": "Email of the new employee's direct manager (optional).",
                    },
                    "send_welcome_email": {
                        "type": "boolean",
                        "description": "Whether to send a welcome email. Default true.",
                    },
                    "schedule_orientation": {
                        "type": "boolean",
                        "description": "Whether to schedule an orientation meeting. Default true.",
                    },
                    "create_tasks": {
                        "type": "boolean",
                        "description": "Whether to create onboarding tasks in Planner. Default true.",
                    },
                },
                "required": ["new_user_email", "new_user_name"],
            },
        },
    },
    # ── AGENT MEMORY: TEMPLATE APPLICATION ────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "apply_template",
            "description": (
                "Look up a saved Agent Memory template by name and return its parsed "
                "schema (sections, headings, placeholders, tone, branding). Use this "
                "when the user asks to write something using a specific template "
                "they previously uploaded (e.g. 'use my Q3 report template'). "
                "After receiving the schema, generate output that follows the section "
                "order, heading text, and placeholder names exactly. Do NOT invent "
                "sections that are not in the schema."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_name": {
                        "type": "string",
                        "description": (
                            "Title (or fragment) of the template the user wants to apply. "
                            "Matched case-insensitively against the user's saved templates."
                        ),
                    },
                    "data_hint": {
                        "type": "string",
                        "description": (
                            "Optional one-line summary of the data the user wants to fill "
                            "into the template (e.g. 'Q3 revenue numbers from finance@')."
                        ),
                    },
                },
                "required": ["template_name"],
            },
        },
    },
    # ── CODE INTERPRETER ──────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "run_python_code",
            "description": (
                "Execute Python code in a secure sandbox and return downloadable files. "
                "ALWAYS use this tool when the user asks for a file, report, chart, "
                "analysis, or data transformation — do not describe what code would do; "
                "actually run it and produce the file.\n\n"
                "Available libraries:\n"
                "  - pandas, numpy: data loading, cleaning, analysis, pivots\n"
                "  - matplotlib, seaborn: charts — save with plt.savefig('chart.png')\n"
                "  - openpyxl, xlsxwriter: Excel .xlsx (xlsxwriter for charts/formatting)\n"
                "  - fpdf2 (fpdf): PDF reports with text, tables, images\n"
                "  - python-docx (docx): Word .docx with headings, tables, styles\n"
                "  - fitz (PyMuPDF): read/extract content from uploaded PDF files\n"
                "  - scipy: statistical tests, signal processing, optimization\n"
                "  - Pillow (PIL): image manipulation\n"
                "  - csv, json, io, pathlib, zipfile: standard library\n\n"
                "Input files: uploaded files are pre-loaded in the working directory "
                "by their original filename — open them directly, e.g. open('data.csv').\n"
                "Output files: write to the current directory (e.g. open('report.xlsx', 'wb')). "
                "All files written are returned as download buttons.\n\n"
                "Multi-file output: use zipfile.ZipFile to bundle into 'output.zip'.\n\n"
                "Agent Memory: pass 'memory_item_ids' with IDs from [DATA_CARD] or "
                "[AGENT_MEMORY] blocks to auto-load those files into the sandbox by "
                "their original filename — ideal for analysing uploaded CSV/XLSX.\n\n"
                "Rules:\n"
                "  - NO network requests (requests, urllib, socket)\n"
                "  - NO shell commands (os.system, subprocess)\n"
                "  - NO file paths outside the working directory\n"
                "  - Always save files to disk; never just print them\n"
                "  - For charts, always call plt.savefig() before plt.show()"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Complete, runnable Python code. "
                            "Write all output files to the current directory."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "One-sentence description of what this code does "
                            "and what files it produces."
                        ),
                    },
                    "memory_item_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of agent_memory_item_id values to "
                            "auto-load as input files into the sandbox."
                        ),
                    },
                },
                "required": ["code", "description"],
            },
        },
    },
]


# ── ToolExecutor ──────────────────────────────────────────────────────────────

# Phase 2 (H-7): tools whose execution must produce an audit row.
# Mutating / external-side-effect tools only — read-only Graph queries are
# excluded to keep audit volume bounded.
_AUDITED_TOOLS: set[str] = {
    "send_email",
    "create_draft_email",
    "send_draft_email",
    "reply_to_email",
    "schedule_meeting",
    "create_task",
    "run_python_code",
    "onboard_user",
}

# Argument keys whose values must be redacted before audit-logging.
_REDACT_KEYS = {"password", "secret", "token", "api_key", "apikey",
                "authorization", "auth", "credentials"}


def _redact_tool_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``args`` safe for audit logs.

    * Keys matching common secret patterns are replaced with ``"<redacted>"``.
    * Long string values (e.g. email bodies, code blobs) are truncated to 200
      chars to keep audit rows bounded.
    """
    if not isinstance(args, dict):
        return {"_value": "<non-dict>"}
    safe: Dict[str, Any] = {}
    for k, v in args.items():
        kl = str(k).lower()
        if any(p in kl for p in _REDACT_KEYS):
            safe[k] = "<redacted>"
        elif isinstance(v, str) and len(v) > 200:
            safe[k] = v[:200] + "…"
        elif isinstance(v, (str, int, float, bool)) or v is None:
            safe[k] = v
        elif isinstance(v, (list, tuple)):
            safe[k] = f"<{type(v).__name__} len={len(v)}>"
        else:
            safe[k] = f"<{type(v).__name__}>"
    return safe


async def _audit_tool_execution(
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    user: UserInfo,
    result: Dict[str, Any],
    trace_id: Optional[str],
) -> None:
    """Fire-and-forget audit write for sensitive tool calls (H-7)."""
    try:
        from app.core.database import async_session_maker
        from app.core.logging import log_security_event
        is_error = isinstance(result, dict) and "error" in result
        async with async_session_maker() as _db:
            await log_security_event(
                _db,
                user_id=getattr(user, "id", None) or "<unknown>",
                action="tool_executed",
                event_type="tool",
                resource_type="agent_tool",
                resource_id=tool_name,
                details={
                    "tool": tool_name,
                    "trace_id": trace_id,
                    "arguments": _redact_tool_args(arguments),
                    "outcome": "error" if is_error else "success",
                    "error": (result.get("error") if is_error else None),
                },
                success=not is_error,
                error_message=(result.get("error") if is_error else None),
            )
            await _db.commit()
    except Exception as exc:
        logger.warning("tool audit failed for %s: %s", tool_name, exc)


class ToolExecutor:
    """Execute agent tools."""

    async def get_available_tools(
        self,
        user: UserInfo,
        user_session: Optional[UserSession] = None,
    ) -> List[Dict]:
        """Get list of available tools for user.

        Returns the union of:
          1. Built-in tools (the static TOOLS list), filtered for personal mode
          2. Synthesised orchestration-brain worker tools, filtered the same way

        Worker tool synthesis is best-effort: if the registry lookup fails
        (DB down, etc.) we still return the built-in set so the chat path
        never breaks because the orchestration brain hiccupped.
        """
        if not settings.ENABLE_AGENTS:
            return []

        if user_session and user_session.is_personal:
            base = [
                t for t in TOOLS
                if t.get("function", {}).get("name") not in _BLOCKED_GRAPH_TOOLS
            ]
        else:
            base = list(TOOLS)

        # Append synthesised worker tools (from orchestration brain).
        try:
            from app.core.database import async_session_maker
            from app.orchestration.tool_bridge import synth_worker_tools
            async with async_session_maker() as _db:
                worker_tools = await synth_worker_tools(
                    _db, user_session=user_session
                )
            base.extend(worker_tools)
        except Exception as exc:
            logger.warning(
                "tool_executor: worker tool synthesis skipped: %s", exc
            )
        return base

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user: UserInfo,
        access_token: Optional[str] = None,
        input_files: Optional[List[Dict]] = None,
        user_session: Optional[UserSession] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a tool and return result."""
        logger.info("Executing tool: %s user=%s", tool_name, getattr(user, "id", "?"))

        # ── Phase 3c (CR-3): strip LLM-supplied workflow_type ────────────────
        # `workflow_type` may only be set by trusted server-side callers
        # (onboarding_service, offboarding_service). When the LLM places it
        # in tool args we silently remove it so it cannot be used to bypass
        # the confirmation gate via the "automated workflow" escape hatch.
        sanitized_args: Dict[str, Any] = dict(arguments or {})
        if tool_name == "send_email" and "workflow_type" in sanitized_args:
            logger.warning(
                "[security] Stripping LLM-supplied workflow_type=%r from send_email "
                "(user=%s) — only trusted server callers may set this field.",
                sanitized_args.get("workflow_type"),
                getattr(user, "id", "?"),
            )
            sanitized_args.pop("workflow_type", None)

        # ── Phase 3a (CR-3): code-level confirmation gate ────────────────────
        # Dangerous tools require a one-shot confirmation token minted only by
        # the explicit user-confirmation API. The LLM cannot bypass this gate.
        # The `_confirmation_token` key (leading underscore) is NEVER part of
        # any tool's JSON schema — the LLM has no way to learn about it.
        from app.agents.confirmation import (
            DANGEROUS_TOOLS, consume_token, make_confirmation_required_result,
        )
        if tool_name in DANGEROUS_TOOLS:
            supplied_token = sanitized_args.pop("_confirmation_token", None)
            user_id_str = str(getattr(user, "id", "") or "")
            # Validate using the args the LLM intends to execute with
            # (after workflow_type stripping). The token was issued against
            # exactly this payload at confirmation time.
            ok = consume_token(
                token=supplied_token or "",
                user_id=user_id_str,
                tool_name=tool_name,
                arguments=sanitized_args,
            )
            if not ok:
                logger.warning(
                    "[security] Confirmation gate blocked %s for user=%s "
                    "(token_present=%s)",
                    tool_name, user_id_str, bool(supplied_token),
                )
                gate_result = make_confirmation_required_result(
                    tool_name=tool_name,
                    arguments=sanitized_args,
                )
                # Still audit the blocked attempt so we can spot
                # injection-driven send_email storms.
                if tool_name in _AUDITED_TOOLS:
                    await _audit_tool_execution(
                        tool_name=tool_name,
                        arguments=sanitized_args,
                        user=user,
                        result={"error": "user_confirmation_required"},
                        trace_id=trace_id,
                    )
                return gate_result

        # ── Sprint 3.2: role-based tool gating ───────────────────────────────
        # When ENFORCE_TOOL_ROLE_GATES=true and an EnabledTool row exists for
        # this tool, the caller's role must be in allowed_roles. Defaults to
        # allow-all (no row → no enforcement) so existing tools keep working.
        from app.core.config import settings as _settings
        if getattr(_settings, "ENFORCE_TOOL_ROLE_GATES", False):
            try:
                from app.core.database import async_session_maker
                from app.models.models import EnabledTool
                from sqlalchemy import select as _select
                async with async_session_maker() as _gate_db:
                    row = await _gate_db.scalar(
                        _select(EnabledTool).where(
                            EnabledTool.tool_name == tool_name
                        )
                    )
                if row is not None:
                    if not row.is_enabled:
                        logger.info(
                            "[role-gate] Tool %s disabled by admin", tool_name
                        )
                        return {
                            "error": "tool_disabled",
                            "message": (
                                f"The tool '{tool_name}' has been disabled "
                                "by your administrator."
                            ),
                        }
                    user_role = str(getattr(user, "role", "") or "").lower()
                    allowed = [
                        str(r).lower()
                        for r in (row.allowed_roles or [])
                    ]
                    # Treat empty allowed_roles as "everyone" for back-compat.
                    if allowed and user_role and user_role not in allowed:
                        logger.info(
                            "[role-gate] Denied %s for user=%s role=%s "
                            "(allowed=%s)",
                            tool_name, getattr(user, "id", "?"),
                            user_role, allowed,
                        )
                        return {
                            "error": "permission_denied",
                            "message": (
                                f"Your role ('{user_role}') is not permitted "
                                f"to call '{tool_name}'."
                            ),
                            "tool": tool_name,
                        }
            except Exception as _gate_err:
                # Fail open on infrastructure errors — never block valid
                # users because of a DB hiccup. Log so admins notice.
                logger.warning(
                    "[role-gate] Lookup failed for %s (allowing): %s",
                    tool_name, _gate_err,
                )

        result: Dict[str, Any]
        try:
            from app.core.telemetry import start_span
            with start_span(
                f"tool.{tool_name}",
                tool=tool_name,
                user_id=str(getattr(user, "id", "") or ""),
            ):
                result = await self._execute_tool_inner(
                    tool_name=tool_name,
                    arguments=sanitized_args,
                    user=user,
                    access_token=access_token,
                    input_files=input_files,
                    user_session=user_session,
                    trace_id=trace_id,
                )
        except Exception as e:
            logger.error("Tool execution error for %s: %s", tool_name, e, exc_info=True)
            result = {"error": str(e), "tool": tool_name}

        # Phase 2 (H-7): audit sensitive tool calls regardless of outcome.
        if tool_name in _AUDITED_TOOLS:
            await _audit_tool_execution(
                tool_name=tool_name,
                arguments=sanitized_args,
                user=user,
                result=result,
                trace_id=trace_id,
            )
        return result

    async def _execute_tool_inner(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user: UserInfo,
        access_token: Optional[str] = None,
        input_files: Optional[List[Dict]] = None,
        user_session: Optional[UserSession] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Internal dispatch (no audit). Caller wraps in audit shim."""
        if user_session and user_session.is_personal:
            if tool_name in _BLOCKED_GRAPH_TOOLS:
                return {
                    "error": "Enterprise data access is not allowed in Personal mode.",
                    "tool": tool_name,
                }

        # Orchestration-brain worker tool? Dispatch through Router → Adapter.
        # Identified solely by the synthesised name prefix; built-in tools
        # never use the worker__ prefix so this is unambiguous.
        if tool_name.startswith("worker__"):
            from app.core.database import async_session_maker
            from app.orchestration.tool_bridge import dispatch_worker_tool
            async with async_session_maker() as _db:
                out = await dispatch_worker_tool(
                    _db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_id=str(getattr(user, "id", "")),
                    tenant_id=getattr(user, "tenant_id", None),
                    trace_id=trace_id or str(uuid.uuid4()),
                    project_id=None,
                )
            if out is None:
                return {"error": f"Unknown worker tool: {tool_name}"}
            return out

        # Graph tools — app-only via AZURE_CLIENT_ID
        if tool_name in {
            "get_inbox", "send_email", "create_draft_email",
            "send_draft_email",
            "search_emails", "get_email_details", "get_email_thread",
            "reply_to_email",
            "get_calendar", "schedule_meeting", "check_availability",
            "list_planner_tasks", "create_task",
        }:
            return await self._dispatch_graph_tool(
                tool_name, arguments, user, access_token=access_token,
            )

        # Non-Graph tools
        if tool_name == "search_documents":
            return await self._search_documents(arguments, user, user_session)
        elif tool_name == "search_graph":
            return await self._search_graph(arguments, user, user_session)
        elif tool_name == "onboard_user":
            return await self._onboard_user(arguments, access_token, user)
        elif tool_name == "run_python_code":
            logger.debug("TE: Calling _run_python_code with %d chars of code", len(arguments.get("code", "")))
            result = await self._run_python_code(
                arguments,
                input_files=input_files,
                user_id=str(getattr(user, "id", "") or "") or None,
            )
            logger.debug("TE: _run_python_code returned success=%s, files=%d", result.get("success"), len(result.get("files", [])))
            return result
        elif tool_name == "apply_template":
            return await self._apply_template(arguments, user)
        else:
            return {"error": f"Unknown tool: {tool_name}"}

    # ── Graph tool dispatcher ─────────────────────────────────────────────────

    async def _dispatch_graph_tool(
        self,
        tool_name: str,
        arguments: Dict,
        user: Optional[Any],
        access_token: Optional[str] = None,
    ) -> Dict:
        """Route to the correct Graph tool handler.

        ``access_token`` is the user's bearer token; when present and the
        ``USE_OBO_FOR_GRAPH`` flag is on, the Graph service exchanges it
        for a delegated token via the On-Behalf-Of flow so the action is
        attributed to the real user in Microsoft 365 audit logs.
        """
        user_email = getattr(user, "email", None) or ""
        if not user_email:
            return {
                "error": (
                    "Cannot perform this action: user email is not available. "
                    "Please sign in with your Microsoft work account."
                )
            }
        if tool_name == "get_inbox":
            return await self._get_inbox(arguments, user_email, access_token)
        elif tool_name == "send_email":
            return await self._send_email(arguments, user_email, access_token)
        elif tool_name == "create_draft_email":
            return await self._create_draft(arguments, user_email, access_token)
        elif tool_name == "send_draft_email":
            return await self._send_draft_email(arguments, user_email, access_token)
        elif tool_name == "search_emails":
            return await self._search_emails(arguments, user_email, access_token)
        elif tool_name == "get_email_details":
            return await self._get_email_details(arguments, user_email, access_token)
        elif tool_name == "get_email_thread":
            return await self._get_email_thread(arguments, user_email, access_token)
        elif tool_name == "reply_to_email":
            return await self._reply_to_email(arguments, user_email, access_token)
        elif tool_name == "get_calendar":
            return await self._get_calendar(arguments, user_email, access_token)
        elif tool_name == "schedule_meeting":
            return await self._schedule_meeting(arguments, user_email, access_token)
        elif tool_name == "check_availability":
            return await self._check_availability(arguments, user_email, access_token)
        elif tool_name == "list_planner_tasks":
            return await self._list_planner_tasks(arguments, access_token)
        elif tool_name == "create_task":
            return await self._create_task(arguments, user_email, access_token)
        return {"error": f"Unknown Graph tool: {tool_name}"}

    # ── Email tools ───────────────────────────────────────────────────────────

    async def _get_inbox(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Read the user's inbox (OBO when available, else app-only)."""
        gs = _get_graph_service()
        limit = min(int(args.get("limit", 10)), 50)
        filter_query = args.get("filter")
        try:
            result = await gs.get_emails_for_user(
                user_email=user_email,
                folder="inbox",
                top=limit,
                filter_query=filter_query,
                user_assertion=access_token,
            )
            messages = []
            for msg in result.get("value", []):
                sender = msg.get("from", {}).get("emailAddress", {})
                messages.append({
                    "id": msg.get("id"),
                    "subject": msg.get("subject"),
                    "from": sender.get("name") or sender.get("address"),
                    "from_address": sender.get("address"),
                    "preview": msg.get("bodyPreview", "")[:200],
                    "received": msg.get("receivedDateTime"),
                    "is_read": msg.get("isRead", True),
                    "has_attachments": msg.get("hasAttachments", False),
                    "importance": msg.get("importance", "normal"),
                })
            logger.info(
                "[graph] get_inbox user=%s count=%d",
                user_email, len(messages),
            )
            return {"messages": messages, "count": len(messages)}
        except Exception as exc:
            return self._graph_error("read inbox", exc)

    async def _send_email(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Send email as the user (OBO when available, else app-only)."""
        from app.services.email_service import format_plain_text_email
        gs = _get_graph_service()

        # Validate workflow_type against approved whitelist
        _APPROVED_WORKFLOWS = {"onboarding", "offboarding", "system_notification", "automated_report"}
        workflow_type = args.get("workflow_type")
        if workflow_type and workflow_type not in _APPROVED_WORKFLOWS:
            return {
                "success": False,
                "error": f"Unapproved workflow_type '{workflow_type}'. "
                         f"Allowed: {sorted(_APPROVED_WORKFLOWS)}",
            }

        try:
            raw_body = args["body"]
            sender_name = user_email.split("@")[0].replace(".", " ").title()
            plain_body = format_plain_text_email(body=raw_body, sender_name=sender_name)

            await gs.send_email_for_user(
                user_email=user_email,
                to=args["to"],
                subject=args["subject"],
                body=plain_body,
                cc=args.get("cc"),
                bcc=args.get("bcc"),
                is_html=False,
                user_assertion=access_token,
            )
            if workflow_type:
                logger.info(
                    "[graph] send_email AUTOMATED workflow=%s from=%s → %s",
                    workflow_type, user_email, args["to"],
                )
            else:
                logger.info(
                    "[graph] send_email USER-CONFIRMED from=%s → %s",
                    user_email, args["to"],
                )
            return {
                "success": True,
                "message": f"Email sent successfully to {', '.join(args['to'])}.",
                **({"workflow_type": workflow_type} if workflow_type else {}),
            }
        except Exception as exc:
            return self._graph_error("send email", exc)

    async def _create_draft(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Create a professional plain-text draft email for the user."""
        from app.services.email_service import format_plain_text_email
        gs = _get_graph_service()
        try:
            raw_body = args["body"]
            sender_name = user_email.split("@")[0].replace(".", " ").title()
            plain_body = format_plain_text_email(body=raw_body, sender_name=sender_name)
            result = await gs.create_draft_for_user(
                user_email=user_email,
                to=args["to"],
                subject=args["subject"],
                body=plain_body,
                is_html=False,
                cc=args.get("cc"),
                user_assertion=access_token,
            )
            draft_id = result.get("id", "")
            if not draft_id:
                logger.warning(
                    "[graph] create_draft: no id in response user=%s — result keys: %s",
                    user_email, list(result.keys()),
                )
            logger.info(
                "[graph] create_draft user=%s subject=%r draft_id=%s",
                user_email, args.get("subject", ""), draft_id[:20] if draft_id else "",
            )
            return {
                "success": True,
                "message": "Draft saved to your Drafts folder in Outlook.",
                "draft_id": draft_id,
                "to": args["to"],
                "subject": args["subject"],
                "body_preview": raw_body[:500],
            }
        except Exception as exc:
            return self._graph_error("create draft", exc)

    async def _send_draft_email(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Send an existing draft email by its ID.

        Safety guard: the LLM must echo the user's verbatim confirmation
        phrase. If it can't (because the user never confirmed in the most
        recent turn), the call is refused — the front-end's "Send" button
        on the draft card calls /mail/draft/send directly, which is the
        intended path for user-initiated sends.
        """
        gs = _get_graph_service()
        draft_id = args.get("draft_id", "").strip()
        if not draft_id:
            return {"error": "draft_id is required"}

        _confirm = (args.get("user_confirmation") or "").strip().lower()
        # Must look like an actual confirmation phrase from the user, not
        # something the model invented. Require an affirmative keyword.
        _affirmatives = ("send", "confirm", "yes", "go ahead", "do it", "approved")
        if not _confirm or not any(w in _confirm for w in _affirmatives):
            return {
                "error": (
                    "Refusing to send the draft without explicit user confirmation. "
                    "Ask the user to confirm (e.g. 'send it'), then retry with "
                    "user_confirmation set to their verbatim reply."
                ),
                "success": False,
            }
        try:
            await gs.send_draft_for_user(
                user_email=user_email,
                draft_id=draft_id,
                user_assertion=access_token,
            )
            to_summary = args.get("to_summary", "recipient(s)")
            logger.info(
                "[graph] send_draft user=%s draft_id=%s",
                user_email, draft_id[:20],
            )
            return {
                "success": True,
                "message": f"Email sent successfully to {to_summary}.",
            }
        except Exception as exc:
            return self._graph_error("send draft email", exc)

    async def _search_emails(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Search emails by subject, sender, content, date, importance, unread, or flagged."""
        from app.services.email_service import normalize_message_list
        gs = _get_graph_service()
        try:
            result = await gs.search_emails_for_user(
                user_email=user_email,
                query=args.get("query", ""),
                from_address=args.get("from_address", ""),
                from_name=args.get("from_name", ""),
                subject_contains=args.get("subject_contains", ""),
                is_important=bool(args.get("is_important", False)),
                is_unread=args.get("is_unread"),
                is_flagged=args.get("is_flagged"),
                has_attachments=args.get("has_attachments"),
                date_from=args.get("date_from", ""),
                date_to=args.get("date_to", ""),
                folder=args.get("folder", ""),
                top=min(int(args.get("top", 20)), 50),
                user_assertion=access_token,
            )
            messages = normalize_message_list(result.get("value", []))
            logger.info(
                "[graph] search_emails user=%s found=%d",
                user_email, len(messages),
            )
            return {"emails": messages, "count": len(messages)}
        except Exception as exc:
            return self._graph_error("search emails", exc)

    async def _get_email_details(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Retrieve full content of a specific email by ID."""
        from app.services.email_service import normalize_message
        gs = _get_graph_service()
        email_id = args.get("email_id", "").strip()
        if not email_id:
            return {"error": "email_id is required"}
        try:
            raw = await gs.get_email_by_id_for_user(
                user_email=user_email,
                message_id=email_id,
                include_body=True,
                user_assertion=access_token,
            )
            msg = normalize_message(raw, include_body=True)
            logger.info(
                "[graph] get_email_details user=%s id=%s",
                user_email, email_id[:20],
            )
            return {"email": msg}
        except Exception as exc:
            return self._graph_error("get email details", exc)

    async def _get_email_thread(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Retrieve all messages in a conversation thread."""
        from app.services.email_service import normalize_message_list
        gs = _get_graph_service()
        email_id = args.get("email_id", "").strip()
        top = min(int(args.get("top", 20)), 50)
        if not email_id:
            return {"error": "email_id is required"}
        try:
            result = await gs.get_email_thread_for_user(
                user_email=user_email,
                message_id=email_id,
                top=top,
                user_assertion=access_token,
            )
            messages = normalize_message_list(
                result.get("value", []), include_body=True
            )
            logger.info(
                "[graph] get_email_thread user=%s messages=%d",
                user_email, len(messages),
            )
            return {
                "thread": messages,
                "count": len(messages),
                "subject": messages[0]["subject"] if messages else "",
            }
        except Exception as exc:
            return self._graph_error("get email thread", exc)

    async def _reply_to_email(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Send or draft a reply to an email with professional plain-text formatting."""
        from app.services.email_service import format_plain_text_email
        gs = _get_graph_service()
        email_id = args.get("email_id", "").strip()
        body = args.get("body", "").strip()
        reply_all = bool(args.get("reply_all", False))
        save_as_draft = bool(args.get("save_as_draft", False))
        cc = args.get("cc") or []

        if not email_id:
            return {"error": "email_id is required"}
        if not body:
            return {"error": "body is required"}

        # Format the reply body as professional plain text
        plain_body = format_plain_text_email(
            body=body,
            sender_name=user_email.split("@")[0].replace(".", " ").title(),
        )

        try:
            if save_as_draft:
                result = await gs.create_draft_reply_for_user(
                    user_email=user_email,
                    message_id=email_id,
                    body=plain_body,
                    reply_all=reply_all,
                    is_html=False,
                    user_assertion=access_token,
                )
                return {
                    "success": True,
                    "saved_as_draft": True,
                    "draft_id": result.get("id"),
                    "message": "Reply saved to your Drafts folder for review.",
                }
            else:
                await gs.reply_to_email_for_user(
                    user_email=user_email,
                    message_id=email_id,
                    body=plain_body,
                    reply_all=reply_all,
                    cc=cc or None,
                    is_html=False,
                    user_assertion=access_token,
                )
                action = "replied-all" if reply_all else "replied"
                logger.info(
                    "[graph] reply_to_email user=%s id=%s action=%s",
                    user_email, email_id[:20], action,
                )
                return {
                    "success": True,
                    "message": f"Reply sent successfully ({action}).",
                }
        except Exception as exc:
            return self._graph_error("reply to email", exc)

    # ── Calendar tools ────────────────────────────────────────────────────────

    async def _get_calendar(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Get upcoming calendar events (OBO when available, else app-only)."""
        gs = _get_graph_service()
        days = int(args.get("days_ahead", 7))
        start = datetime.utcnow()
        end = start + timedelta(days=days)
        try:
            result = await gs.get_calendar_events_for_user(
                user_email, start, end, user_assertion=access_token,
            )
            events = []
            for ev in result.get("value", []):
                attendees = [
                    a["emailAddress"]["address"]
                    for a in ev.get("attendees", [])
                    if a.get("emailAddress", {}).get("address")
                ]
                events.append({
                    "id": ev.get("id"),
                    "subject": ev.get("subject"),
                    "start": ev.get("start", {}).get("dateTime"),
                    "end": ev.get("end", {}).get("dateTime"),
                    "timezone": ev.get("start", {}).get("timeZone"),
                    "location": (ev.get("location") or {}).get("displayName"),
                    "is_online": ev.get("isOnlineMeeting", False),
                    "meeting_link": (
                        (ev.get("onlineMeeting") or {}).get("joinUrl")
                    ),
                    "organizer": (
                        ((ev.get("organizer") or {}).get("emailAddress") or {})
                        .get("address")
                    ),
                    "attendees": attendees,
                })
            return {"events": events, "count": len(events)}
        except Exception as exc:
            return self._graph_error("read calendar", exc)

    async def _schedule_meeting(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Schedule a meeting on the user's calendar (OBO when available, else app-only)."""
        gs = _get_graph_service()
        try:
            start_str = args["start_time"]
            start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            duration = int(args.get("duration_minutes", 60))
            end = start + timedelta(minutes=duration)
            timezone = args.get("timezone", "UTC")
            result = await gs.create_event_for_user(
                user_email=user_email,
                subject=args["subject"],
                start=start,
                end=end,
                attendees=args.get("attendees"),
                body=args.get("description"),
                location=args.get("location"),
                is_online_meeting=args.get("is_teams_meeting", True),
                timezone=timezone,
                user_assertion=access_token,
            )
            meeting_link = None
            if result.get("onlineMeeting"):
                meeting_link = result["onlineMeeting"].get("joinUrl")
            logger.info(
                "[graph] schedule_meeting: event_id=%s", result.get("id")
            )
            return {
                "success": True,
                "message": f"Meeting '{args['subject']}' scheduled",
                "event_id": result.get("id"),
                "start": result.get("start", {}).get("dateTime"),
                "end": result.get("end", {}).get("dateTime"),
                "meeting_link": meeting_link,
                "web_link": result.get("webLink"),
            }
        except Exception as exc:
            return self._graph_error("schedule meeting", exc)

    async def _check_availability(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """Check free/busy for a list of email addresses."""
        gs = _get_graph_service()
        try:
            date = datetime.fromisoformat(args["date"])
            start = date.replace(hour=8, minute=0, second=0, microsecond=0)
            end = date.replace(hour=18, minute=0, second=0, microsecond=0)
            result = await gs.get_free_busy_for_user(
                user_email=user_email,
                schedules=args["emails"],
                start=start,
                end=end,
                user_assertion=access_token,
            )
            availability = [
                {
                    "email": s.get("scheduleId"),
                    "availability_view": s.get("availabilityView"),
                    "schedule_items": [
                        {
                            "status": i.get("status"),
                            "start": i.get("start", {}).get("dateTime"),
                            "end": i.get("end", {}).get("dateTime"),
                        }
                        for i in s.get("scheduleItems", [])
                    ],
                }
                for s in result.get("value", [])
            ]
            return {"availability": availability}
        except Exception as exc:
            return self._graph_error("check availability", exc)

    # ── Planner / Tasks tools ─────────────────────────────────────────────────

    async def _list_planner_tasks(
        self, args: Dict, access_token: Optional[str] = None,
    ) -> Dict:
        """List Planner tasks (OBO when available, else app-only)."""
        gs = _get_graph_service()
        plan_id = args.get("plan_id") or settings.GRAPH_DEFAULT_PLANNER_PLAN_ID
        try:
            result = await gs.get_planner_tasks_for_user(
                plan_id=plan_id, user_assertion=access_token,
            )
            tasks = []
            for t in result.get("value", []):
                assignments = (
                    list(t.get("assignments", {}).keys())
                    if t.get("assignments") else []
                )
                tasks.append({
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "plan_id": t.get("planId"),
                    "bucket_id": t.get("bucketId"),
                    "due_date": t.get("dueDateTime"),
                    "percent_complete": t.get("percentComplete", 0),
                    "priority": t.get("priority"),
                    "created": t.get("createdDateTime"),
                    "assigned_to": assignments,
                })
            logger.info("[graph] list_planner_tasks: %d tasks", len(tasks))
            return {"tasks": tasks, "count": len(tasks)}
        except Exception as exc:
            return self._graph_error("list Planner tasks", exc)

    async def _create_task(
        self, args: Dict, user_email: str,
        access_token: Optional[str] = None,
    ) -> Dict:
        """
        Create a task. Routes to Planner (if plan_id) or To Do (personal).
        Uses OBO when available, else falls back to app-only.
        """
        gs = _get_graph_service()
        title = args["title"]
        due_date = args.get("due_date")
        notes = args.get("notes")
        plan_id = (
            args.get("plan_id") or settings.GRAPH_DEFAULT_PLANNER_PLAN_ID
        )
        assigned_to = args.get("assigned_to")  # Entra OID

        # ── Planner task (enterprise, requires plan_id) ────────────────────
        if plan_id:
            try:
                due_dt = None
                if due_date:
                    due_dt = datetime.fromisoformat(
                        due_date.replace("Z", "+00:00")
                    )
                result = await gs.create_planner_task_for_user(
                    plan_id=plan_id,
                    title=title,
                    due_date=due_dt,
                    assigned_to=assigned_to,
                    user_assertion=access_token,
                )
                logger.info(
                    "[graph] create Planner task: id=%s plan=%s",
                    result.get("id"), plan_id,
                )
                return {
                    "success": True,
                    "type": "planner",
                    "message": f"Planner task '{title}' created",
                    "task_id": result.get("id"),
                    "plan_id": plan_id,
                    "due_date": due_date,
                }
            except Exception as exc:
                logger.warning(
                    "[graph] Planner task failed (plan=%s): %s — "
                    "falling back to To Do.",
                    plan_id, exc,
                )
                # Fall through to To Do

        # ── To Do task (personal, no plan needed) ─────────────────────────
        try:
            result = await gs.create_todo_task_for_user(
                user_email=user_email,
                title=title,
                due_date=due_date,
                notes=notes,
                user_assertion=access_token,
            )
            task_id = result.get("id") if isinstance(result, dict) else None
            logger.info("[graph] create To Do task: id=%s", task_id)
            return {
                "success": True,
                "type": "todo",
                "message": f"Task '{title}' added to your To Do list",
                "task_id": task_id,
                "due_date": due_date,
            }
        except Exception as exc:
            return self._graph_error("create task", exc)

    # ── Error helpers ─────────────────────────────────────────────────────────

    def _graph_error(self, action: str, exc: Exception) -> Dict:
        """Format a Graph API error for the assistant."""
        exc_str = str(exc)
        # Parse Graph error codes for actionable messages
        if "401" in exc_str or "Unauthorized" in exc_str:
            hint = "Token may have expired or the required scope is missing."
        elif "403" in exc_str or "Forbidden" in exc_str:
            hint = "The app may not have admin consent for the required permissions."
        elif "404" in exc_str or "Not Found" in exc_str:
            hint = "The resource was not found."
        elif "429" in exc_str or "Too Many Requests" in exc_str:
            hint = "Rate limit reached. Try again in a moment."
        else:
            hint = "Check backend logs for details."

        logger.error("[graph] %s failed: %s", action, exc_str)
        return {
            "error": f"Failed to {action}: {exc_str[:200]}",
            "hint": hint,
        }

    # ── Search ────────────────────────────────────────────────────────────────

    async def _search_documents(
        self,
        args: Dict,
        user: Optional[Any],
        user_session: Optional[UserSession],
    ) -> Dict:
        """Search enterprise documents (SharePoint, OneDrive, website) using Azure AI Search.
        Falls back to simple RAG index if enterprise search is not configured.
        """
        query = args.get("query", "")
        if not query:
            return {"results": [], "count": 0, "error": "No query provided"}

        if (
            user_session
            and user_session.is_work
            and settings.AZURE_SEARCH_ENDPOINT
            and settings.AZURE_SEARCH_ADMIN_KEY
        ):
            source_type = args.get("source_type")
            user_email = getattr(user, "email", None)

            # Source-specific routing: bypass AI Search when user explicitly names a source.
            if source_type in ("onedrive",) and user_email:
                try:
                    import asyncio as _asyncio
                    from app.services.connectors.graph_client import GraphClient as _GC
                    from app.services.document_service import extract_text as _extract
                    from app.services.search.query_pipeline import EnterpriseSearchResult as _ESR
                    _gc = _GC()
                    od_hits = await _gc.search_user_drive(user_id=user_email, query=query, top=8)
                    ent_results = []
                    for hit in od_hits:
                        od_name = hit.get("name", "")
                        od_url = hit.get("webUrl", "")
                        od_id = hit.get("id", "")
                        od_content = hit.get("_summary", "")
                        od_score = 0.5
                        try:
                            async def _dl(h=hit, nm=od_name):
                                b = await _gc.download_user_drive_item(user_email, h.get("id", ""))
                                if not b:
                                    return ""
                                ext = nm.rsplit(".", 1)[-1].lower() if "." in nm else ""
                                _mm = {"pdf": "application/pdf",
                                       "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                       "txt": "text/plain", "csv": "text/csv", "json": "application/json"}
                                return _extract(b, _mm.get(ext, "application/octet-stream"), nm) or ""
                            text = await _asyncio.wait_for(_dl(), timeout=8.0)
                            if text:
                                od_content = text[:2000]
                                od_score = 0.72
                        except Exception:
                            pass
                        ent_results.append(_ESR(
                            chunk_id=od_id or od_url,
                            document_title=od_name,
                            content=od_content or f"File: {od_name}",
                            score=od_score,
                            source_type="onedrive",
                            url=od_url,
                            author=hit.get("createdBy", {}).get("user", {}).get("displayName", ""),
                            last_modified=hit.get("lastModifiedDateTime", ""),
                        ))
                    return {
                        "results": [{"title": r.document_title, "content": r.content[:500],
                                     "source_type": r.source_type, "source": r.url, "score": r.score}
                                    for r in ent_results],
                        "count": len(ent_results),
                        "source": "onedrive_direct",
                        "note": "No matching OneDrive files found." if not ent_results else None,
                    }
                except Exception as e:
                    logger.warning("OneDrive direct search failed: %s", e)
                    return {"results": [], "count": 0, "error": str(e), "source": "onedrive_direct"}

            if source_type == "sharepoint" and user_email:
                try:
                    from app.services.connectors.graph_client import GraphClient as _GC2
                    from app.services.search.query_pipeline import EnterpriseSearchResult as _ESR2
                    _gc2 = _GC2()
                    sp_hits = await _gc2.search_files(query=query, entity_types=["listItem"], top=8)
                    ent_results = []
                    for hit in sp_hits:
                        sp_name = hit.get("name", "") or hit.get("displayName", "")
                        sp_url = hit.get("webUrl", "")
                        sp_content = hit.get("_summary", f"SharePoint file: {sp_name}")
                        ent_results.append(_ESR2(
                            chunk_id=hit.get("id", sp_url),
                            document_title=sp_name,
                            content=sp_content,
                            score=0.6,
                            source_type="sharepoint",
                            url=sp_url,
                            author=hit.get("createdBy", {}).get("user", {}).get("displayName", ""),
                            last_modified=hit.get("lastModifiedDateTime", ""),
                        ))
                    return {
                        "results": [{"title": r.document_title, "content": r.content[:500],
                                     "source_type": r.source_type, "source": r.url, "score": r.score}
                                    for r in ent_results],
                        "count": len(ent_results),
                        "source": "sharepoint_direct",
                        "note": "No matching SharePoint files found." if not ent_results else None,
                    }
                except Exception as e:
                    logger.warning("SharePoint direct search failed: %s", e)
                    return {"results": [], "count": 0, "error": str(e), "source": "sharepoint_direct"}

            try:
                from app.services.search.query_pipeline import enterprise_query
                tenant_id = getattr(user, "tenant_id", None)
                if not tenant_id:
                    return {
                        "results": [],
                        "count": 0,
                        "error": "Enterprise search requires a work profile tenant context.",
                        "source": "enterprise",
                    }
                # Get user groups for ACL filtering - ensures users only see documents they have access to
                user_groups = getattr(user, "groups", []) or []
                ent_results = await enterprise_query.search(
                    query=query,
                    workspace_id=tenant_id,
                    context_type="org",
                    user_id=str(getattr(user, "id", "tool")),
                    user_groups=user_groups,
                    tenant_id=tenant_id,
                    top_k=8,
                    use_cache=True,
                    source_types=[source_type] if source_type else None,
                    user_email=user_email,
                )
                return {
                    "results": [
                        {
                            "title": r.document_title,
                            "content": r.content[:500],
                            "source_type": r.source_type,
                            "source": r.url,
                            "score": r.score,
                        }
                        for r in ent_results
                    ],
                    "count": len(ent_results),
                    "source": "enterprise",
                    "note": "No matching documents found." if not ent_results else None,
                }
            except Exception as e:
                logger.warning("Enterprise search in tool failed: %s", e)
                return {"results": [], "count": 0, "error": str(e), "source": "enterprise"}

        try:
            from app.services.rag_service import rag_service
            filters = {}
            if args.get("file_type"):
                filters["file_type"] = args["file_type"]
            if user_session and user_session.is_personal:
                filters["source"] = "upload"
                filters["uploaded_by"] = str(getattr(user, "id", ""))
            results = await rag_service.search(
                query=query,
                top_k=5,
                filters=filters if filters else None,
            )
            return {
                "results": [
                    {
                        "title": r.document_title,
                        "content": r.content[:400],
                        "score": r.score,
                        "source": r.source_url,
                    }
                    for r in results
                ],
                "count": len(results),
                "source": "rag",
            }
        except Exception as e:
            logger.error("search_documents fallback failed: %s", e)
            return {"results": [], "count": 0, "error": str(e)}

    async def _search_graph(
        self,
        args: Dict,
        user: Optional[Any],
        user_session: Optional[UserSession],
    ) -> Dict:
        """Live file search via Microsoft Graph Search API."""
        query = args.get("query", "")
        if not query:
            return {"results": [], "count": 0, "error": "No query provided"}

        if user_session and user_session.is_personal:
            return {
                "results": [],
                "count": 0,
                "error": "Graph Search is not available in Personal mode.",
            }

        try:
            from app.services.connectors.graph_client import GraphClient
            client = GraphClient()  # app-only token
            top = min(int(args.get("top", 5)), 25)
            hits = await client.search_files(query, top=top)
            results = []
            for h in hits:
                results.append({
                    "name": h.get("name", ""),
                    "url": h.get("webUrl", ""),
                    "last_modified": h.get("lastModifiedDateTime", ""),
                    "size": h.get("size", 0),
                    "summary": h.get("_summary", ""),
                    "created_by": (
                        h.get("createdBy", {}).get("user", {}).get("displayName", "")
                    ),
                })
            return {
                "results": results,
                "count": len(results),
                "source": "graph_search",
            }
        except Exception as e:
            logger.error("search_graph failed: %s", e)
            return {"results": [], "count": 0, "error": str(e)}

    # ── Code interpreter ──────────────────────────────────────────────────────

    async def _load_memory_files(self, memory_item_ids):
        """Fetch agent-memory item blobs and return them as sandbox input files.

        Returns list of {"filename": str, "content": bytes} using the item's
        original title so idiomatic pandas code like pd.read_csv('data.csv')
        works without modification.
        """
        if not memory_item_ids:
            return []
        from app.core import database as _db_mod
        from app.core.database import async_session_maker
        from app.models.models import AgentMemoryItem
        from app.services.blob_storage import blob_store
        import os as _os
        if not _db_mod.db_available:
            return []
        out = []
        async with async_session_maker() as db:
            for mid in memory_item_ids[:10]:
                item = await db.get(AgentMemoryItem, mid)
                if item is None or not item.blob_url:
                    continue
                data = await blob_store.download(item.blob_url)
                if data is None:
                    continue
                fname = _os.path.basename(item.title or f"memory_{item.id}")
                fname = fname.replace("\\", "_").replace("/", "_")
                out.append({"filename": fname, "content": data})
        return out

    async def _run_python_code(
        self,
        args: Dict,
        input_files: Optional[List[Dict]] = None,
        user_id: Optional[str] = None,
    ) -> Dict:
        """Execute Python code in the sandbox with auto-fix retry.

        ``user_id`` is forwarded to the interpreter so per-user concurrency
        and daily-quota gates apply (Phase 4 CR-1).
        """
        code = args.get("code", "")
        if not code.strip():
            return {"error": "No code provided"}

        # Auto-load any requested agent-memory items as input files
        memory_ids = args.get("memory_item_ids") or []
        if memory_ids:
            try:
                extra = await self._load_memory_files(memory_ids)
                if extra:
                    input_files = (input_files or []) + extra
                    logger.info(
                        "Loaded %d agent-memory file(s) into sandbox", len(extra)
                    )
            except Exception as exc:
                logger.warning("Failed to load memory files %s: %s",
                               memory_ids, exc)

        logger.info("Running code interpreter: %s", args.get("description", "")[:80])
        ci = _get_code_interpreter()
        # Phase 4 (CR-1): the interpreter may refuse via concurrency/quota
        # gate — surface that as a structured tool result so the LLM can
        # explain the situation to the user.
        from app.services.code_interpreter_service import CodeInterpreterError
        try:
            result = await ci.run(
                code, input_files=input_files or [], user_id=user_id,
            )
        except CodeInterpreterError as gate_exc:
            return {
                "success": False,
                "error": gate_exc.message,
                "status": "rate_limited",
            }

        if result.success or not result.stderr.strip():
            return result.to_dict()

        # One auto-retry: ask the LLM to fix the error
        logger.info("Code execution failed, attempting auto-fix (1 retry)")
        try:
            fix_prompt = (
                f"The following Python code raised an error.\n\n"
                f"Code:\n```python\n{code}\n```\n\n"
                f"Error:\n```\n{result.stderr[:1500]}\n```\n\n"
                "Fix the code and return ONLY the corrected Python code with no "
                "explanation and no markdown fences."
            )
            fixed_code = await _get_openai_service().get_completion(
                messages=[
                    {"role": "system", "content": "You are a Python code fixer. Return only corrected code."},
                    {"role": "user", "content": fix_prompt},
                ],
                model="gpt-4.1",
                max_tokens=2048,
                temperature=0.1,
            )
            if fixed_code and fixed_code.strip():
                cleaned = "\n".join(
                    line for line in fixed_code.strip().splitlines()
                    if not line.strip().startswith("```")
                )
                try:
                    retry = await ci.run(
                        cleaned,
                        input_files=input_files or [],
                        user_id=user_id,
                    )
                except CodeInterpreterError as gate_exc:
                    return {
                        "success": False,
                        "error": gate_exc.message,
                        "status": "rate_limited",
                    }
                if retry.success or retry.files:
                    logger.info("Code auto-fix succeeded")
                return retry.to_dict()
        except Exception as retry_err:
            logger.warning("Code auto-fix failed: %s", retry_err)

        return result.to_dict()

    # ── Onboarding ────────────────────────────────────────────────────────────

    async def _onboard_user(
        self, args: Dict, access_token: Optional[str], user: Optional[Any]
    ) -> Dict:
        """Run the automated onboarding workflow for a new employee."""
        from app.core.database import async_session_maker
        from app.services.onboarding_service import run_onboarding

        # ── Authorization: only admin / HR may onboard new users ──
        # Without this gate, any authenticated user could trigger Graph
        # account creation by simply asking the model.
        _role = (getattr(user, "role", "") or "").lower()
        if _role not in ("admin", "hr"):
            return {
                "error": (
                    "Onboarding new employees is restricted to admins and HR. "
                    "Please ask your administrator to run this workflow."
                ),
                "success": False,
            }

        new_user_email = args.get("new_user_email", "").strip()
        new_user_name = args.get("new_user_name", "").strip()
        if not new_user_email or not new_user_name:
            return {"error": "new_user_email and new_user_name are required"}

        async with async_session_maker() as db:
            result = await run_onboarding(
                db,
                new_user_email=new_user_email,
                new_user_name=new_user_name,
                department=args.get("department"),
                manager_email=args.get("manager_email"),
                initiated_by=getattr(user, "id", "system"),
                initiated_by_email=getattr(user, "email", None),
                send_welcome_email=args.get("send_welcome_email", True),
                schedule_orientation=args.get("schedule_orientation", True),
                create_tasks=args.get("create_tasks", True),
                access_token=None,  # onboarding uses app-only Graph internally
            )

        completed = result.get("steps_completed", [])
        failed = result.get("steps_failed", [])
        status = result.get("status", "unknown")

        summary_lines = [
            f"Onboarding workflow for **{new_user_name}** ({new_user_email}) — status: **{status}**"
        ]
        if completed:
            summary_lines.append("Steps completed: " + ", ".join(completed))
        if failed:
            for f in failed:
                summary_lines.append(f"Step failed — {f['step']}: {f['error']}")

        return {**result, "summary": "\n".join(summary_lines)}


    # ── Agent Memory tools ─────────────────────────────────────────────────────

    async def _apply_template(self, args: Dict, user: Any) -> Dict:
        """Look up a saved Agent Memory template by name and return its schema."""
        template_name = (args.get("template_name") or "").strip()
        data_hint = (args.get("data_hint") or "").strip()
        if not template_name:
            return {"error": "template_name is required"}

        user_id = getattr(user, "id", None)
        tenant_id = getattr(user, "tenant_id", None)
        if not user_id:
            return {"error": "user not authenticated"}

        try:
            from app.core import database as _db_mod
            from app.core.database import async_session_maker
            from app.models.models import AgentMemoryItem
            from app.services.template_service import template_service
            from sqlalchemy import select, and_, or_, func
        except Exception as exc:  # pragma: no cover
            return {"error": f"agent memory unavailable: {exc}"}

        if not _db_mod.db_available:
            return {"error": "database unavailable"}

        async with async_session_maker() as db:
            # Same visibility rule as list_items: own + tenant-shared.
            visibility = AgentMemoryItem.user_id == str(user_id)
            if tenant_id:
                visibility = or_(
                    visibility,
                    and_(
                        AgentMemoryItem.tenant_id == tenant_id,
                        AgentMemoryItem.scope.in_(["workspace", "tenant"]),
                    ),
                )
            q = select(AgentMemoryItem).where(
                and_(
                    AgentMemoryItem.tag == "template",
                    AgentMemoryItem.status == "ready",
                    visibility,
                    func.lower(AgentMemoryItem.title).like(
                        f"%{template_name.lower()}%"
                    ),
                )
            ).order_by(AgentMemoryItem.updated_at.desc()).limit(5)
            rows = list((await db.scalars(q)).all())

        if not rows:
            return {
                "error": (
                    f"No template found matching '{template_name}'. "
                    "Ask the user to upload one in Settings → Agent Memory → Templates."
                )
            }

        item = rows[0]
        schema = item.template_schema_json or {}
        return {
            "matched_template": item.title,
            "template_id": item.id,
            "scope": item.scope,
            "tone": (schema.get("tone_summary") or "neutral"),
            "section_count": len(schema.get("sections") or []),
            "schema": schema,
            "data_hint": data_hint,
            "instruction": (
                "Generate output that follows the section order, headings, and "
                "placeholder names in 'schema' exactly. Replace placeholders with "
                "values inferred from data_hint and prior conversation context. "
                "Cite sources when populating any factual values."
            ),
            "alternatives": [
                {"id": r.id, "title": r.title, "scope": r.scope}
                for r in rows[1:]
            ],
        }


# Singleton instance
tool_executor = ToolExecutor()
