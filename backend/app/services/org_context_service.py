"""
Mela AI - Enterprise Org Context Service

Fetches and caches (30-minute TTL per user) the organisational context for a
user: their job title, department, manager, direct reports, group memberships,
and frequent contacts from Microsoft People API.

This context is injected into the Work-mode system prompt so the AI can give
org-aware answers (e.g. "your manager is Sarah Chen — CC her on escalations").

All Graph calls use app-only (client-credentials) token so no delegated token
is required.  Requires the following application permissions:
  User.Read.All, Directory.Read.All, People.Read.All
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Per-user-per-tenant cache: {"tenant:user": {"data": ..., "exp": unix_timestamp}}
_cache: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS = 30 * 60  # 30 minutes

# Lock to prevent concurrent fetches for the same cache key
_locks: Dict[str, asyncio.Lock] = {}


def _cache_key(user_id: str, tenant_id: Optional[str] = None) -> str:
    safe_user = (user_id or "").strip()
    safe_tenant = (tenant_id or "").strip()
    return f"{safe_tenant}:{safe_user}" if safe_tenant else safe_user


def _get_lock(cache_key: str) -> asyncio.Lock:
    if cache_key not in _locks:
        _locks[cache_key] = asyncio.Lock()
    return _locks[cache_key]


class OrgContextService:
    """Fetch and cache Microsoft 365 organisational context for Work mode."""

    async def get_context(self, user_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return cached org context, refreshing if expired.

        Returns None if Graph is not configured or the fetch fails.
        Never raises — all errors are caught and logged.
        """
        if not user_id:
            return None

        key = _cache_key(user_id, tenant_id)

        entry = _cache.get(key)
        if entry and time.time() < entry["exp"]:
            return entry["data"]

        async with _get_lock(key):
            # Re-check after acquiring the lock (another coroutine may have filled it)
            entry = _cache.get(key)
            if entry and time.time() < entry["exp"]:
                return entry["data"]

            data = await self._fetch(user_id)
            if data:
                _cache[key] = {"data": data, "exp": time.time() + _CACHE_TTL_SECONDS}
            return data

    def invalidate(self, user_id: str, tenant_id: Optional[str] = None) -> None:
        """Remove a user's cached context (e.g. on profile update)."""
        _cache.pop(_cache_key(user_id, tenant_id), None)

    async def _fetch(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            from app.services.connectors.graph_client import GraphClient
            gc = GraphClient()  # app-only

            # Fetch concurrently — cap at 10s so we never block a chat request.
            profile_task = asyncio.create_task(self._get_profile(gc, user_id))
            manager_task = asyncio.create_task(self._get_manager(gc, user_id))
            reports_task = asyncio.create_task(self._get_direct_reports(gc, user_id))
            groups_task = asyncio.create_task(self._get_groups(gc, user_id))
            people_task = asyncio.create_task(self._get_people(gc, user_id))

            try:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        profile_task, manager_task, reports_task, groups_task, people_task,
                        return_exceptions=True,
                    ),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning("OrgContextService._fetch timed out for user %s", user_id[:8])
                for t in (profile_task, manager_task, reports_task, groups_task, people_task):
                    t.cancel()
                return None
            profile, manager, direct_reports, groups, people = results

            # Replace exceptions with None / empty list
            if isinstance(profile, Exception):
                logger.debug("OrgContext: profile fetch failed for %s: %s", user_id[:8], profile)
                profile = {}
            if isinstance(manager, Exception):
                logger.debug("OrgContext: manager fetch failed for %s: %s", user_id[:8], manager)
                manager = None
            if isinstance(direct_reports, Exception):
                direct_reports = []
            if isinstance(groups, Exception):
                groups = []
            if isinstance(people, Exception):
                people = []

            return {
                "display_name": profile.get("displayName", ""),
                "job_title": profile.get("jobTitle", ""),
                "department": profile.get("department", ""),
                "office_location": profile.get("officeLocation", ""),
                "email": profile.get("mail") or profile.get("userPrincipalName", ""),
                "manager": manager,
                "direct_reports": direct_reports,
                "groups": groups,
                "frequent_contacts": people,
            }
        except Exception as exc:
            logger.warning("OrgContextService._fetch failed for user %s: %s", user_id[:8], exc)
            return None

    async def _get_profile(self, gc, user_id: str) -> Dict:
        data = await gc._get(
            f"/users/{user_id}",
            params={"$select": "displayName,jobTitle,department,officeLocation,mail,userPrincipalName"},
        )
        return data

    async def _get_manager(self, gc, user_id: str) -> Optional[Dict]:
        try:
            data = await gc._get(
                f"/users/{user_id}/manager",
                params={"$select": "displayName,jobTitle,mail,userPrincipalName"},
            )
            return {
                "display_name": data.get("displayName", ""),
                "job_title": data.get("jobTitle", ""),
                "email": data.get("mail") or data.get("userPrincipalName", ""),
            }
        except Exception:
            # 404 is normal for top-level executives who have no manager
            return None

    async def _get_direct_reports(self, gc, user_id: str) -> List[Dict]:
        try:
            items = await gc.get_all_pages(
                f"/users/{user_id}/directReports",
                params={"$select": "displayName,jobTitle,mail,userPrincipalName"},
            )
            return [
                {
                    "display_name": p.get("displayName", ""),
                    "job_title": p.get("jobTitle", ""),
                    "email": p.get("mail") or p.get("userPrincipalName", ""),
                }
                for p in items[:10]  # Cap at 10
            ]
        except Exception:
            return []

    async def _get_groups(self, gc, user_id: str) -> List[str]:
        """Return the display names of the user's group memberships (max 20)."""
        try:
            items = await gc.get_all_pages(
                f"/users/{user_id}/memberOf",
                params={"$select": "displayName,groupTypes"},
            )
            return [
                g.get("displayName", "")
                for g in items
                if g.get("displayName")
            ][:20]
        except Exception:
            return []

    async def _get_people(self, gc, user_id: str) -> List[Dict]:
        """Return the user's most relevant frequent contacts via the People API.

        Requires People.Read.All application permission.
        """
        try:
            data = await gc._get(
                f"/users/{user_id}/people",
                params={
                    "$top": "10",
                    "$select": "displayName,jobTitle,scoredEmailAddresses",
                },
            )
            out = []
            for p in data.get("value", [])[:10]:
                emails = p.get("scoredEmailAddresses", [])
                email = emails[0].get("address", "") if emails else ""
                out.append({
                    "display_name": p.get("displayName", ""),
                    "job_title": p.get("jobTitle", ""),
                    "email": email,
                })
            return out
        except Exception:
            # People.Read.All may not be granted — silently skip
            return []

    def build_prompt_block(self, ctx: Dict[str, Any]) -> str:
        """Render the org context dict as a structured text block for the system prompt."""
        if not ctx:
            return ""

        lines = ["## Your Organisational Context"]

        if ctx.get("display_name"):
            title_str = f", {ctx['job_title']}" if ctx.get("job_title") else ""
            dept_str = f" ({ctx['department']})" if ctx.get("department") else ""
            lines.append(f"You are {ctx['display_name']}{title_str}{dept_str}.")

        if ctx.get("office_location"):
            lines.append(f"Office: {ctx['office_location']}")

        if ctx.get("manager"):
            m = ctx["manager"]
            mgr_title = f", {m['job_title']}" if m.get("job_title") else ""
            lines.append(f"Your manager: {m['display_name']}{mgr_title} ({m.get('email', '')})")

        if ctx.get("direct_reports"):
            reports = ctx["direct_reports"]
            names = ", ".join(
                f"{r['display_name']}" + (f" ({r['job_title']})" if r.get("job_title") else "")
                for r in reports[:5]
            )
            lines.append(f"Your direct reports: {names}")

        if ctx.get("groups"):
            grp_str = ", ".join(ctx["groups"][:8])
            lines.append(f"Your groups / teams: {grp_str}")

        if ctx.get("frequent_contacts"):
            contacts = ctx["frequent_contacts"]
            contact_str = ", ".join(
                c["display_name"] for c in contacts[:5] if c.get("display_name")
            )
            if contact_str:
                lines.append(f"Frequent collaborators: {contact_str}")

        lines.append(
            "\nUse this context to give relevant, personalised answers. "
            "When referencing colleagues, use their names and titles. "
            "When asked about team structure, org chart, or reporting lines, answer from this context."
        )

        return "\n".join(lines)


# Singleton
org_context_service = OrgContextService()
