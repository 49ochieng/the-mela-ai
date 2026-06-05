"""
Sprint 3.2 — Idempotent seed for ``enabled_tools`` rows.

Run from lifespan. Inserts a row per known tool with the default
``allowed_roles`` recommendation from the audit. Never overwrites an existing
row — once an admin tunes a tool's allowed_roles, the seed leaves it alone.
"""

from __future__ import annotations

import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import EnabledTool

logger = logging.getLogger(__name__)


# (tool_name, display_name, allowed_roles, requires_confirmation, description)
_SEED: list[tuple[str, str, list[str], bool, str]] = [
    # Communications — all human roles
    ("send_email", "Send Email",
     ["admin", "platform_admin", "tenant_admin", "power_user",
      "user", "standard_user"],
     True, "Send an email via Microsoft Graph"),
    ("create_draft_email", "Create Draft Email",
     ["admin", "platform_admin", "tenant_admin", "power_user",
      "user", "standard_user"],
     False, "Create a draft email"),
    ("schedule_meeting", "Schedule Meeting",
     ["admin", "platform_admin", "tenant_admin", "power_user",
      "user", "standard_user"],
     True, "Create a calendar meeting"),

    # Code & images — gated above standard user
    ("run_python_code", "Run Python Code",
     ["admin", "platform_admin", "tenant_admin", "power_user"],
     True, "Execute Python in the sandbox"),
    ("generate_image", "Generate Image",
     ["admin", "platform_admin", "tenant_admin", "power_user"],
     False, "Generate an image (DALL-E/FLUX)"),

    # Web search — gated above standard user
    ("web_search", "Web Search",
     ["admin", "platform_admin", "tenant_admin", "power_user"],
     False, "Public web search"),

    # Read-only / safe — everyone
    ("search_documents", "Search Documents",
     [], False,  # empty allowed_roles = everyone
     "Search the enterprise knowledge base"),
    ("get_emails", "Get Emails",
     [], False, "Read inbox messages"),
    ("get_calendar_events", "Get Calendar Events",
     [], False, "Read calendar entries"),
]


async def seed_enabled_tools(db: AsyncSession) -> int:
    """Insert default rows for any tool not already present.

    Returns the number of rows inserted.
    """
    inserted = 0
    for name, display, roles, require_conf, desc in _SEED:
        existing = await db.scalar(
            select(EnabledTool).where(EnabledTool.tool_name == name)
        )
        if existing is not None:
            continue
        db.add(EnabledTool(
            tool_name=name,
            display_name=display,
            description=desc,
            is_enabled=True,
            requires_confirmation=require_conf,
            allowed_roles=roles,
        ))
        inserted += 1

    if inserted:
        await db.commit()
        logger.info("Seeded %d enabled_tools rows", inserted)
    return inserted
