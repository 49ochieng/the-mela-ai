"""
Mela AI — Microsoft Graph REST Endpoints

Provides direct REST access to Graph productivity features:
  - Email (inbox, send, draft)
  - Calendar (events, schedule meeting, free/busy)
  - Planner (list tasks, create task)

All endpoints require a valid Entra bearer token.
Graph operations use the enterprise app (AZURE_CLIENT_ID) via app-only
client credentials, calling /users/{email}/... endpoints.
No OBO or delegated token is needed.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core.security import get_current_user
from app.schemas.auth import UserInfo

logger = logging.getLogger(__name__)

router = APIRouter()


def _gs():
    from app.services.graph_service import graph_service
    return graph_service


def _user_assertion(request: Request) -> Optional[str]:
    """Pull the raw bearer token off request.state (set by get_current_user).

    Used to drive the Microsoft Graph On-Behalf-Of flow when
    USE_OBO_FOR_GRAPH is enabled. Falls back to ``None`` when no bearer
    is on the request (e.g. dev-login token paths), which causes the
    Graph service to use its app-only token instead.
    """
    return getattr(request.state, "access_token", None)


def _require_email(user: UserInfo) -> str:
    """Return the user's email or raise 400."""
    email = getattr(user, "email", None)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "User email not available. "
                "Sign in with your Microsoft work account."
            ),
        )
    return email


# ── Request / Response schemas ────────────────────────────────────────────────

class SendMailRequest(BaseModel):
    to: List[str]
    subject: str
    body: str
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    is_html: bool = True


class CreateEventRequest(BaseModel):
    subject: str
    start: str   # ISO 8601
    end: str     # ISO 8601
    timezone: str = "UTC"
    attendees: Optional[List[str]] = None
    body: Optional[str] = None
    location: Optional[str] = None
    is_online_meeting: bool = True


class CreatePlannerTaskRequest(BaseModel):
    plan_id: str
    title: str
    due_date: Optional[str] = None   # ISO 8601 date
    assigned_to: Optional[str] = None  # Entra OID


class CreateTodoTaskRequest(BaseModel):
    title: str
    due_date: Optional[str] = None
    notes: Optional[str] = None


# ── Email endpoints ───────────────────────────────────────────────────────────

@router.get("/mail/inbox", tags=["Graph - Email"])
async def get_inbox(
    request: Request,
    limit: int = 10,
    filter: Optional[str] = None,
    current_user: UserInfo = Depends(get_current_user),
):
    """Return the signed-in user's inbox messages (OBO or app-only)."""
    user_email = _require_email(current_user)
    gs = _gs()
    try:
        result = await gs.get_emails_for_user(
            user_email=user_email,
            folder="inbox",
            top=min(limit, 50),
            filter_query=filter,
            user_assertion=_user_assertion(request),
        )
        messages = []
        for msg in result.get("value", []):
            sender = msg.get("from", {}).get("emailAddress", {})
            messages.append({
                "id": msg.get("id"),
                "subject": msg.get("subject"),
                "from_name": sender.get("name"),
                "from_address": sender.get("address"),
                "preview": msg.get("bodyPreview", "")[:300],
                "received": msg.get("receivedDateTime"),
                "is_read": msg.get("isRead", True),
                "has_attachments": msg.get("hasAttachments", False),
                "importance": msg.get("importance", "normal"),
            })
        logger.info(
            "[graph/inbox] user=%s count=%d",
            current_user.id, len(messages),
        )
        return {"messages": messages, "count": len(messages)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/inbox] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/mail/send", tags=["Graph - Email"])
async def send_mail(
    payload: SendMailRequest,
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
):
    """Send an email as the signed-in user (OBO or app-only)."""
    user_email = _require_email(current_user)
    gs = _gs()
    try:
        await gs.send_email_for_user(
            user_email=user_email,
            to=payload.to,
            subject=payload.subject,
            body=payload.body,
            cc=payload.cc,
            bcc=payload.bcc,
            is_html=payload.is_html,
            user_assertion=_user_assertion(request),
        )
        logger.info(
            "[graph/send] user=%s to=%s subject=%.60s",
            current_user.id, payload.to, payload.subject,
        )
        return {
            "success": True,
            "message": f"Email sent to {', '.join(payload.to)}",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/send] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/mail/draft", tags=["Graph - Email"])
async def create_draft(
    payload: SendMailRequest,
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
):
    """Save a draft email to the signed-in user's Drafts folder (OBO or app-only)."""
    user_email = _require_email(current_user)
    gs = _gs()
    try:
        result = await gs.create_draft_for_user(
            user_email=user_email,
            to=payload.to,
            subject=payload.subject,
            body=payload.body,
            is_html=payload.is_html,
            user_assertion=_user_assertion(request),
        )
        logger.info(
            "[graph/draft] user=%s draft_id=%s",
            current_user.id, result.get("id"),
        )
        return {
            "success": True,
            "draft_id": result.get("id"),
            "message": "Draft saved",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/draft] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


class SendDraftRequest(BaseModel):
    draft_id: str


@router.post("/mail/send-draft", tags=["Graph - Email"])
async def send_draft(
    payload: SendDraftRequest,
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
):
    """Send an existing draft email by its Graph message ID (OBO or app-only)."""
    draft_id = payload.draft_id.strip()
    if not draft_id:
        raise HTTPException(status_code=400, detail="draft_id is required")
    user_email = _require_email(current_user)
    gs = _gs()
    try:
        await gs.send_draft_for_user(
            user_email=user_email,
            draft_id=draft_id,
            user_assertion=_user_assertion(request),
        )
        logger.info(
            "[graph/send-draft] user=%s draft_id=%s",
            current_user.id, draft_id[:20],
        )
        return {"success": True, "message": "Email sent successfully"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/send-draft] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


# ── Calendar endpoints ────────────────────────────────────────────────────────

@router.get("/calendar/events", tags=["Graph - Calendar"])
async def get_calendar_events(
    request: Request,
    days_ahead: int = 7,
    current_user: UserInfo = Depends(get_current_user),
):
    """Return the signed-in user's upcoming calendar events (OBO or app-only)."""
    user_email = _require_email(current_user)
    gs = _gs()
    try:
        start = datetime.utcnow()
        end = start + timedelta(days=max(1, min(days_ahead, 90)))
        result = await gs.get_calendar_events_for_user(
            user_email, start, end,
            user_assertion=_user_assertion(request),
        )
        events = []
        for ev in result.get("value", []):
            attendees = [
                (a.get("emailAddress") or {}).get("address", "")
                for a in (ev.get("attendees") or [])
                if (a.get("emailAddress") or {}).get("address")
            ]
            events.append({
                "id": ev.get("id"),
                "subject": ev.get("subject"),
                "start": (ev.get("start") or {}).get("dateTime"),
                "end": (ev.get("end") or {}).get("dateTime"),
                "timezone": (ev.get("start") or {}).get("timeZone"),
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
                "web_link": ev.get("webLink"),
            })
        logger.info(
            "[graph/calendar] user=%s count=%d days=%d",
            current_user.id, len(events), days_ahead,
        )
        return {"events": events, "count": len(events)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/calendar] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/calendar/events", tags=["Graph - Calendar"])
async def create_calendar_event(
    payload: CreateEventRequest,
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
):
    """Create a calendar event in the signed-in user's calendar (OBO or app-only)."""
    user_email = _require_email(current_user)
    gs = _gs()
    try:
        start = datetime.fromisoformat(payload.start.replace("Z", "+00:00"))
        end = datetime.fromisoformat(payload.end.replace("Z", "+00:00"))
        result = await gs.create_event_for_user(
            user_email=user_email,
            subject=payload.subject,
            start=start,
            end=end,
            attendees=payload.attendees,
            body=payload.body,
            location=payload.location,
            is_online_meeting=payload.is_online_meeting,
            timezone=payload.timezone,
            user_assertion=_user_assertion(request),
        )
        meeting_link = None
        if result.get("onlineMeeting"):
            meeting_link = result["onlineMeeting"].get("joinUrl")
        logger.info(
            "[graph/event] user=%s event_id=%s subject=%.60s",
            current_user.id, result.get("id"), payload.subject,
        )
        return {
            "success": True,
            "event_id": result.get("id"),
            "subject": result.get("subject"),
            "start": result.get("start", {}).get("dateTime"),
            "end": result.get("end", {}).get("dateTime"),
            "meeting_link": meeting_link,
            "web_link": result.get("webLink"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/event] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/calendar/free-busy", tags=["Graph - Calendar"])
async def check_free_busy(
    emails: List[str],
    date: str,
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
):
    """Check free/busy availability for a list of users (OBO or app-only)."""
    user_email = _require_email(current_user)
    gs = _gs()
    try:
        day = datetime.fromisoformat(date)
        start = day.replace(hour=8, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=10)
        result = await gs.get_free_busy_for_user(
            user_email=user_email,
            schedules=emails,
            start=start,
            end=end,
            user_assertion=_user_assertion(request),
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
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/free-busy] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


# ── Planner endpoints ─────────────────────────────────────────────────────────

@router.get("/planner/tasks", tags=["Graph - Planner"])
async def list_planner_tasks(
    request: Request,
    plan_id: Optional[str] = None,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    List Planner tasks (OBO or app-only).
    If plan_id is given, returns tasks in that plan.
    Falls back to GRAPH_DEFAULT_PLANNER_PLAN_ID if set.
    """
    from app.core.config import settings
    gs = _gs()
    resolved_plan_id = plan_id or settings.GRAPH_DEFAULT_PLANNER_PLAN_ID
    try:
        result = await gs.get_planner_tasks_for_user(
            plan_id=resolved_plan_id,
            user_assertion=_user_assertion(request),
        )
        tasks = []
        for t in result.get("value", []):
            assignments = list(t.get("assignments", {}).keys())
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
        logger.info(
            "[graph/planner] user=%s plan=%s count=%d",
            current_user.id, resolved_plan_id or "none", len(tasks),
        )
        return {"tasks": tasks, "count": len(tasks)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/planner] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/planner/tasks", tags=["Graph - Planner"])
async def create_planner_task(
    payload: CreatePlannerTaskRequest,
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
):
    """Create a Planner task in a specific plan (OBO or app-only)."""
    gs = _gs()
    try:
        due_dt = None
        if payload.due_date:
            due_dt = datetime.fromisoformat(
                payload.due_date.replace("Z", "+00:00")
            )
        result = await gs.create_planner_task_for_user(
            plan_id=payload.plan_id,
            title=payload.title,
            due_date=due_dt,
            assigned_to=payload.assigned_to,
            user_assertion=_user_assertion(request),
        )
        logger.info(
            "[graph/planner/create] user=%s task_id=%s plan=%s",
            current_user.id, result.get("id"), payload.plan_id,
        )
        return {
            "success": True,
            "task_id": result.get("id"),
            "title": payload.title,
            "plan_id": payload.plan_id,
            "due_date": payload.due_date,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/planner/create] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/todo/tasks", tags=["Graph - Tasks"])
async def create_todo_task(
    payload: CreateTodoTaskRequest,
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
):
    """Create a Microsoft To Do task in the user's default list (OBO or app-only)."""
    user_email = _require_email(current_user)
    gs = _gs()
    try:
        result = await gs.create_todo_task_for_user(
            user_email=user_email,
            title=payload.title,
            due_date=payload.due_date,
            notes=payload.notes,
            user_assertion=_user_assertion(request),
        )
        task_id = result.get("id") if isinstance(result, dict) else None
        logger.info(
            "[graph/todo] user=%s task_id=%s title=%.60s",
            current_user.id, task_id, payload.title,
        )
        return {
            "success": True,
            "task_id": task_id,
            "title": payload.title,
            "due_date": payload.due_date,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[graph/todo] error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


# ── Health / debug ────────────────────────────────────────────────────────────

@router.get("/status", tags=["Graph - Status"])
async def graph_status(
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Check whether the enterprise app can acquire a Graph token.
    Verifies AZURE_CLIENT_ID + AZURE_CLIENT_SECRET are configured and working.
    """
    from app.core.config import settings
    from app.services.obo_service import get_graph_token_app_only

    app_configured = bool(
        settings.effective_client_id and settings.effective_client_secret
    )
    graph_token = None
    graph_error = None
    if app_configured:
        try:
            graph_token = await get_graph_token_app_only()
        except Exception as exc:
            graph_error = str(exc)

    return {
        "app_only_configured": app_configured,
        "data_client_id": settings.effective_client_id or None,
        "graph_token_ok": graph_token is not None,
        "graph_error": graph_error,
        "user_email": getattr(current_user, "email", None),
        "user_id": current_user.id,
        "instructions": (
            "If graph_token_ok is false, ensure AZURE_CLIENT_ID and "
            "AZURE_CLIENT_SECRET are set, and that the enterprise app has "
            "application permissions: Mail.Read, Mail.Send, "
            "Calendars.ReadWrite, Tasks.ReadWrite, Group.Read.All."
        ) if not graph_token else None,
    }
