"""Microsoft Planner Graph operations.

Supports:
- Listing plans (across user's groups) and buckets
- Creating tasks with priority, bucket, categories, checklist, references
- Updating existing tasks (PATCH with ETag) including marking complete
- Auto-bucketing by priority when bucket not explicitly specified
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .client import GraphClient


# Planner numeric priority: 1 = urgent, 3 = important, 5 = medium, 9 = low
_PRIORITY_MAP = {"high": 1, "medium": 5, "low": 9}


def _to_planner_priority(p: str) -> int:
    return _PRIORITY_MAP.get((p or "").lower(), 5)


def _format_due(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── reads ─────────────────────────────────────────────────────────────
async def list_plans(client: GraphClient) -> list[dict[str, Any]]:
    groups = await client.paged("/me/memberOf")
    plans: list[dict[str, Any]] = []
    for g in groups:
        if g.get("@odata.type") != "#microsoft.graph.group":
            continue
        gid = g["id"]
        try:
            data = await client.get(f"/groups/{gid}/planner/plans")
        except Exception:
            continue
        for p in data.get("value", []):
            plans.append({"id": p["id"], "title": p["title"], "group_id": gid})
    return plans


async def list_buckets(client: GraphClient, plan_id: str) -> list[dict[str, Any]]:
    data = await client.get(f"/planner/plans/{plan_id}/buckets")
    return [{"id": b["id"], "name": b["name"]} for b in data.get("value", [])]


async def get_plan_categories(client: GraphClient, plan_id: str) -> dict[str, str]:
    """Return {category1: label, ...} for the plan's category labels (1..25)."""
    try:
        details = await client.get(f"/planner/plans/{plan_id}/details")
    except Exception:
        return {}
    return dict(details.get("categoryDescriptions") or {})


# ── auto-bucket helper ───────────────────────────────────────────────
async def resolve_bucket(
    client: GraphClient,
    plan_id: str,
    explicit_bucket_id: Optional[str],
    priority: str,
) -> Optional[str]:
    """Pick a bucket: explicit > priority-named bucket > first available."""
    if explicit_bucket_id:
        return explicit_bucket_id
    buckets = await list_buckets(client, plan_id)
    if not buckets:
        return None
    target_names = {
        "high": ("high priority", "urgent", "high"),
        "medium": ("medium priority", "in progress", "medium"),
        "low": ("low priority", "later", "low"),
    }.get((priority or "").lower(), ())
    for b in buckets:
        if b["name"].strip().lower() in target_names:
            return b["id"]
    return buckets[0]["id"]


# ── writes ────────────────────────────────────────────────────────────
async def get_my_id(client: GraphClient) -> Optional[str]:
    """Return the signed-in user's Entra (AAD) object id. Cached on client."""
    cached = getattr(client, "_me_id_cache", None)
    if cached:
        return cached
    try:
        me = await client.get("/me?$select=id")
        uid = me.get("id")
        if uid:
            try:
                setattr(client, "_me_id_cache", uid)
            except Exception:  # noqa: BLE001
                pass
        return uid
    except Exception:  # noqa: BLE001
        return None


async def create_task(
    client: GraphClient,
    plan_id: str,
    bucket_id: Optional[str],
    title: str,
    due_date: Optional[datetime] = None,
    description: Optional[str] = None,
    *,
    priority: str = "medium",
    checklist: Optional[list[str]] = None,
    references: Optional[list[tuple[str, str]]] = None,  # [(label, url), ...]
    category_indices: Optional[list[int]] = None,        # 1..25
    assignee_user_id: Optional[str] = None,              # Entra object id
) -> dict[str, Any]:
    # Auto-resolve to the signed-in user when no explicit assignee provided.
    # Without an assignment the task never appears in Planner's "My Tasks"
    # or "My Day" views — only in the plan's bucket.
    if assignee_user_id is None:
        assignee_user_id = await get_my_id(client)

    body: dict[str, Any] = {
        "planId": plan_id,
        "title": title[:255],
        "priority": _to_planner_priority(priority),
    }
    if bucket_id:
        body["bucketId"] = bucket_id
    if due_date:
        body["dueDateTime"] = _format_due(due_date)
    if category_indices:
        body["appliedCategories"] = {f"category{i}": True for i in category_indices}
    if assignee_user_id:
        body["assignments"] = {
            assignee_user_id: {
                "@odata.type": "#microsoft.graph.plannerAssignment",
                "orderHint": " !",
            }
        }

    task = await client.post("/planner/tasks", json=body)
    task_id = task["id"]

    # Patch details (description / checklist / references) if any
    if description or checklist or references:
        details_url = f"/planner/tasks/{task_id}/details"
        details = await client.get(details_url)
        etag = details.get("@odata.etag")
        patch: dict[str, Any] = {}
        if description:
            patch["description"] = description[:32000]
        if checklist:
            patch["checklist"] = {
                _stable_key(i): {
                    "@odata.type": "#microsoft.graph.plannerChecklistItem",
                    "title": item[:100], "isChecked": False, "orderHint": " !",
                }
                for i, item in enumerate(checklist[:20])
            }
        if references:
            patch["references"] = {
                _url_key(url): {
                    "@odata.type": "#microsoft.graph.plannerExternalReference",
                    "alias": (label or url)[:255],
                    "type": "Other",
                }
                for label, url in references[:10]
                if url
            }
        if etag and patch:
            await client.request(
                "PATCH",
                details_url,
                json=patch,
                headers={"If-Match": etag, "Prefer": "return=representation"},
            )
    return task


async def update_task(
    client: GraphClient,
    planner_task_id: str,
    *,
    title: Optional[str] = None,
    due_date: Optional[datetime] = None,
    priority: Optional[str] = None,
    percent_complete: Optional[int] = None,
    assign_to_me: bool = False,
) -> dict[str, Any]:
    """PATCH a Planner task. Fetches current ETag automatically."""
    current = await client.get(f"/planner/tasks/{planner_task_id}")
    etag = current.get("@odata.etag")
    patch: dict[str, Any] = {}
    if title is not None:
        patch["title"] = title[:255]
    if due_date is not None:
        patch["dueDateTime"] = _format_due(due_date)
    if priority is not None:
        patch["priority"] = _to_planner_priority(priority)
    if percent_complete is not None:
        patch["percentComplete"] = max(0, min(100, int(percent_complete)))
    if assign_to_me:
        my_id = await get_my_id(client)
        if my_id and my_id not in (current.get("assignments") or {}):
            patch["assignments"] = {
                my_id: {
                    "@odata.type": "#microsoft.graph.plannerAssignment",
                    "orderHint": " !",
                }
            }
    if not patch or not etag:
        return current
    await client.request(
        "PATCH",
        f"/planner/tasks/{planner_task_id}",
        json=patch,
        headers={"If-Match": etag},
    )
    return await client.get(f"/planner/tasks/{planner_task_id}")


def get_planner_task_url(task_id: str) -> str:
    return f"https://tasks.office.com/Home/Task/{task_id}"


# ── plan / bucket creation ────────────────────────────────────────────
async def get_user_default_group(client: GraphClient) -> Optional[str]:
    """Return the first M365 unified group the user is a member of (good
    candidate for hosting an auto-created plan). Returns None if the user
    isn't in any group."""
    groups = await client.paged("/me/memberOf")
    for g in groups:
        if g.get("@odata.type") != "#microsoft.graph.group":
            continue
        # Prefer unified groups (which can host plans)
        types = g.get("groupTypes") or []
        if "Unified" in types:
            return g["id"]
    # Fallback: first group of any kind
    for g in groups:
        if g.get("@odata.type") == "#microsoft.graph.group":
            return g["id"]
    return None


async def create_plan(
    client: GraphClient, group_id: str, title: str,
) -> dict[str, Any]:
    """Create a new Planner plan inside a group."""
    body = {"owner": group_id, "title": title[:255]}
    return await client.post("/planner/plans", json=body)


async def create_bucket(
    client: GraphClient, plan_id: str, name: str, order_hint: str = " !",
) -> dict[str, Any]:
    """Create a bucket inside a plan."""
    body = {"name": name[:100], "planId": plan_id, "orderHint": order_hint}
    return await client.post("/planner/buckets", json=body)


async def ensure_default_plan_with_buckets(
    client: GraphClient,
    *,
    plan_title: str = "Mela Task Radar",
    bucket_names: tuple[str, ...] = ("Email Tasks", "Teams Tasks"),
) -> dict[str, Any]:
    """Ensure the user has a usable Plan with the required buckets.

    Returns: {"plan_id": str, "buckets": {bucket_name: bucket_id, ...}}

    Raises if the user has no eligible group to host the plan in.
    """
    # 1. Find an existing plan with our title across user's groups
    existing = await list_plans(client)
    target_plan = next((p for p in existing if p["title"] == plan_title), None)

    if target_plan:
        plan_id = target_plan["id"]
    else:
        group_id = await get_user_default_group(client)
        if not group_id:
            raise RuntimeError(
                "User is not a member of any Microsoft 365 Group — "
                "cannot create a Planner plan. Ask an admin to add the user "
                "to a group, or pick an existing plan in Settings."
            )
        plan = await create_plan(client, group_id, plan_title)
        plan_id = plan["id"]

    # 2. Ensure each required bucket exists
    current_buckets = await list_buckets(client, plan_id)
    name_to_id = {b["name"]: b["id"] for b in current_buckets}
    result_buckets: dict[str, str] = {}
    for name in bucket_names:
        if name in name_to_id:
            result_buckets[name] = name_to_id[name]
        else:
            new_b = await create_bucket(client, plan_id, name)
            result_buckets[name] = new_b["id"]

    return {"plan_id": plan_id, "buckets": result_buckets}


# ── helpers ───────────────────────────────────────────────────────────
def _stable_key(i: int) -> str:
    # Planner expects opaque keys for checklist items; use a deterministic UUID-ish.
    return f"checklist{i:02d}"


def _url_key(url: str) -> str:
    # Planner expects a URL-shaped key where ':' and '.' are escaped
    # (e.g. "http%3A//www%2Ebing%2Ecom").
    return (
        (url or "")
        .replace(":", "%3A")
        .replace(".", "%2E")
        .replace("@", "%40")
    )
