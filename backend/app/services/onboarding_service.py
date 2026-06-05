"""
Mela AI - Enterprise Employee Onboarding Service
Full workflow: Entra user creation → manager → groups → licenses → meeting → email → tasks → audit
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import OnboardingLog

logger = logging.getLogger(__name__)


# ── Step result helpers ────────────────────────────────────────────────────────

def _ok(step: str, detail: str = "") -> dict:
    return {"step": step, "status": "ok", "detail": detail}

def _fail(step: str, error: str) -> dict:
    return {"step": step, "status": "failed", "error": error}

def _skip(step: str, reason: str = "not requested") -> dict:
    return {"step": step, "status": "skipped", "reason": reason}


# ── Welcome email template ─────────────────────────────────────────────────────

def _welcome_body(name: str, department: Optional[str], manager_email: Optional[str], notes: Optional[str]) -> str:
    dept = f"<p>You'll be joining the <strong>{department}</strong> team.</p>" if department else ""
    mgr = (f"<p>Your manager is <a href='mailto:{manager_email}'>{manager_email}</a>.</p>" if manager_email else "")
    note = f"<p><em>{notes}</em></p>" if notes else ""
    return (
        f"<h2>Welcome to Armely, {name}!</h2>"
        f"<p>We're thrilled to have you join us. Your Entra ID account has been provisioned. "
        f"Please sign in at <a href='https://myaccount.microsoft.com'>myaccount.microsoft.com</a> "
        f"and change your temporary password on first login.</p>"
        f"{dept}{mgr}{note}"
        f"<ul>"
        f"<li>Check your calendar for your orientation meeting</li>"
        f"<li>Log into <a href='https://teams.microsoft.com'>Microsoft Teams</a></li>"
        f"<li>Access Mela AI at <a href='{settings.FRONTEND_URL or '#'}'>mela-ai</a></li>"
        f"</ul>"
        f"<p style='color:#888;font-size:12px;'>This message was sent automatically by Mela AI HR Automation.</p>"
    )


# ── Preview builder ────────────────────────────────────────────────────────────

async def build_onboarding_preview(payload: dict) -> dict:
    """Validate fields and build a preview summary. Does NOT execute anything."""
    errors = []
    warnings = []

    # Required field validation
    for field in ["first_name", "last_name", "display_name", "upn", "mail_nickname"]:
        if not payload.get(field, "").strip():
            errors.append(f"Required field missing: {field}")

    upn = payload.get("upn", "")
    if upn and "@" not in upn:
        errors.append("UPN must contain @domain (e.g. jsmith@armely.com)")

    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings}

    # Check if UPN already exists
    from app.services.graph_service import GraphAPIService
    gs = GraphAPIService()
    exists = await gs.user_exists(upn)
    if exists:
        errors.append(f"UPN '{upn}' already exists in the directory.")
        return {"valid": False, "errors": errors, "warnings": warnings}

    if not payload.get("usage_location"):
        warnings.append("Usage location not set — defaulting to 'US'. Required for license assignment.")

    steps_planned = ["create_entra_user"]
    if payload.get("manager_email"):
        steps_planned.append("assign_manager")
    if payload.get("group_ids"):
        steps_planned.append(f"add_to_groups ({len(payload['group_ids'])} group(s))")
    if payload.get("sku_ids"):
        steps_planned.append(f"assign_licenses ({len(payload['sku_ids'])} license(s))")
    if payload.get("schedule_orientation"):
        steps_planned.append("create_orientation_meeting")
    if payload.get("send_welcome_email"):
        steps_planned.append("send_welcome_email")
    if payload.get("create_tasks"):
        steps_planned.append("create_onboarding_tasks")

    return {
        "valid": True,
        "errors": [],
        "warnings": warnings,
        "steps_planned": steps_planned,
        "target_upn": upn,
        "display_name": payload.get("display_name"),
        "department": payload.get("department"),
        "job_title": payload.get("job_title"),
        "usage_location": payload.get("usage_location", "US"),
        "group_count": len(payload.get("group_ids", [])),
        "license_count": len(payload.get("sku_ids", [])),
    }


# ── Main onboarding orchestrator ───────────────────────────────────────────────

async def run_onboarding(
    db: AsyncSession,
    payload: dict,
    actor_user_id: str,
    actor_email: str,
    access_token: Optional[str] = None,
) -> dict:
    """
    Execute the full onboarding workflow. Each step is best-effort.
    Returns structured result with step_results, status, run_id.
    """
    from app.services.graph_service import GraphAPIService
    from app.models import HRWorkflowRun, AuditLog

    gs = GraphAPIService()
    step_results: list = []
    entra_id = None
    temp_password = None

    # Save run row upfront
    run = HRWorkflowRun(
        workflow_type="onboarding",
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        target_email=payload.get("work_email") or payload.get("upn", ""),
        target_upn=payload.get("upn", ""),
        target_display_name=payload.get("display_name"),
        payload_json=json.dumps(payload),
        step_results_json="[]",
        status="running",
        started_at=datetime.utcnow(),
        approval_reference=payload.get("approval_reference"),
    )
    db.add(run)
    await db.flush()

    # ── Also create backward-compat OnboardingLog row ─────────────────────────
    log = OnboardingLog(
        new_user_email=payload.get("work_email") or payload.get("upn", ""),
        new_user_name=payload.get("display_name", ""),
        department=payload.get("department"),
        manager_email=payload.get("manager_email"),
        initiated_by=actor_user_id,
        initiated_by_email=actor_email,
        steps_requested=json.dumps(["create_user", "assign_manager", "assign_licenses", "add_groups",
                                    "orientation_meeting", "welcome_email", "onboarding_tasks"]),
        steps_completed=json.dumps([]),
        steps_failed=json.dumps([]),
        status="in_progress",
        notes=payload.get("notes"),
    )
    db.add(log)
    await db.flush()

    steps_completed = []
    steps_failed_names = []

    async def _run_step(step_name: str, coro):
        nonlocal step_results, steps_completed, steps_failed_names
        try:
            result = await coro
            step_results.append(_ok(step_name, str(result)[:200] if result else ""))
            steps_completed.append(step_name)
            logger.info("Onboarding step OK: %s", step_name)
        except Exception as e:
            step_results.append(_fail(step_name, str(e)[:300]))
            steps_failed_names.append(step_name)
            logger.warning("Onboarding step FAILED: %s — %s", step_name, e)

    # ── Step 1: Create Entra user ──────────────────────────────────────────────
    try:
        user_data = await gs.create_entra_user(
            display_name=payload["display_name"],
            user_principal_name=payload["upn"],
            mail_nickname=payload["mail_nickname"],
            given_name=payload["first_name"],
            surname=payload["last_name"],
            job_title=payload.get("job_title"),
            department=payload.get("department"),
            usage_location=payload.get("usage_location", "US"),
            force_change_password=True,
        )
        entra_id = user_data.get("id")
        temp_password = user_data.get("_temp_password")
        step_results.append(_ok("create_entra_user", f"id={entra_id}"))
        steps_completed.append("create_entra_user")
        run.target_entra_id = entra_id
    except Exception as e:
        step_results.append(_fail("create_entra_user", str(e)[:300]))
        steps_failed_names.append("create_entra_user")
        logger.error("Onboarding: create_entra_user failed — %s", e)
        # If user creation fails, abort remaining Graph steps
        entra_id = None

    # ── Step 2: Assign manager ─────────────────────────────────────────────────
    if entra_id and payload.get("manager_email"):
        await _run_step("assign_manager",
            gs.set_user_manager(entra_id, payload["manager_email"]))
    elif not payload.get("manager_email"):
        step_results.append(_skip("assign_manager"))

    # ── Step 3: Add to groups ──────────────────────────────────────────────────
    group_ids = payload.get("group_ids") or []
    if entra_id and group_ids:
        for gid in group_ids:
            await _run_step(f"add_to_group:{gid}",
                gs.add_user_to_group(entra_id, gid))
    elif not group_ids:
        step_results.append(_skip("add_to_groups"))

    # ── Step 4: Assign licenses ────────────────────────────────────────────────
    sku_ids = payload.get("sku_ids") or []
    if entra_id and sku_ids:
        await _run_step("assign_licenses",
            gs.assign_licenses(entra_id, sku_ids))
    elif not sku_ids:
        step_results.append(_skip("assign_licenses"))

    # ── Step 5: Orientation meeting ────────────────────────────────────────────
    if payload.get("schedule_orientation") and access_token:
        orientation_dt = payload.get("orientation_datetime")
        if orientation_dt:
            try:
                start_dt = datetime.fromisoformat(orientation_dt.replace("Z", "+00:00"))
                end_dt = start_dt + timedelta(hours=1)
            except Exception:
                start_dt = datetime.utcnow() + timedelta(days=3)
                start_dt = start_dt.replace(hour=9, minute=0, second=0)
                end_dt = start_dt + timedelta(hours=1)
        else:
            start_dt = datetime.utcnow() + timedelta(days=3)
            start_dt = start_dt.replace(hour=9, minute=0, second=0)
            end_dt = start_dt + timedelta(hours=1)

        attendees = [payload.get("work_email") or payload["upn"]]
        if payload.get("manager_email"):
            attendees.append(payload["manager_email"])

        await _run_step("create_orientation_meeting",
            gs.schedule_meeting(
                access_token=access_token,
                subject=f"Orientation – Welcome {payload['display_name']}!",
                start=start_dt.isoformat() + "Z",
                end=end_dt.isoformat() + "Z",
                attendees=attendees,
                body=f"Orientation meeting for {payload['display_name']}.",
                is_online_meeting=True,
            ))
    elif payload.get("schedule_orientation") and not access_token:
        step_results.append(_fail("create_orientation_meeting", "Delegated access token required to schedule meetings"))
        steps_failed_names.append("create_orientation_meeting")
    else:
        step_results.append(_skip("create_orientation_meeting"))

    # ── Step 6: Welcome email ──────────────────────────────────────────────────
    if payload.get("send_welcome_email"):
        sender_email = (
            getattr(settings, "GRAPH_SENDER_EMAIL", None)
            or actor_email
            or (settings.bootstrap_admin_email_list[0] if settings.bootstrap_admin_email_list else None)
        )
        target_email = payload.get("work_email") or payload["upn"]
        welcome_recipients = payload.get("welcome_recipients") or [target_email]
        if sender_email:
            await _run_step("send_welcome_email",
                gs.send_email_app_only(
                    sender_email=sender_email,
                    to=welcome_recipients,
                    subject=f"Welcome to Armely, {payload['display_name']}!",
                    body=_welcome_body(
                        payload["display_name"],
                        payload.get("department"),
                        payload.get("manager_email"),
                        payload.get("notes"),
                    ),
                    is_html=True,
                ))
        else:
            step_results.append(_fail("send_welcome_email", "No sender email configured"))
            steps_failed_names.append("send_welcome_email")
    else:
        step_results.append(_skip("send_welcome_email"))

    # ── Step 7: Onboarding tasks ───────────────────────────────────────────────
    if payload.get("create_tasks") and access_token:
        tasks_titles = [
            f"Set up workstation and install required tools — {payload['display_name']}",
            f"Complete security & compliance training — {payload['display_name']}",
            f"Request access to team SharePoint and project sites — {payload['display_name']}",
            f"1:1 introduction with direct manager — {payload['display_name']}",
            f"Review team charter and Q roadmap — {payload['display_name']}",
        ]
        due = (datetime.utcnow() + timedelta(days=14)).isoformat() + "Z"
        tasks_ok = []
        for t in tasks_titles:
            try:
                await gs.create_task(access_token=access_token, title=t, due_date=due,
                                     notes=f"Onboarding task for {payload['display_name']}")
                tasks_ok.append(t[:50])
            except Exception as e:
                logger.warning("Task creation failed: %s — %s", t, e)
        if tasks_ok:
            step_results.append(_ok("create_onboarding_tasks", f"{len(tasks_ok)}/{len(tasks_titles)} tasks created"))
            steps_completed.append("create_onboarding_tasks")
        else:
            step_results.append(_fail("create_onboarding_tasks", "All task creations failed"))
            steps_failed_names.append("create_onboarding_tasks")
    elif payload.get("create_tasks") and not access_token:
        step_results.append(_fail("create_onboarding_tasks", "Delegated access token required"))
        steps_failed_names.append("create_onboarding_tasks")
    else:
        step_results.append(_skip("create_onboarding_tasks"))

    # ── Finalise ───────────────────────────────────────────────────────────────
    if steps_failed_names and not steps_completed:
        status = "failed"
    elif steps_failed_names:
        status = "partial"
    else:
        status = "completed"

    run.status = status
    run.step_results_json = json.dumps(step_results)
    run.completed_at = datetime.utcnow()
    if steps_failed_names:
        run.error_summary = "; ".join(steps_failed_names)

    log.steps_completed = json.dumps(steps_completed)
    log.steps_failed = json.dumps([s for s in step_results if s.get("status") == "failed"])
    log.status = status
    log.completed_at = datetime.utcnow()

    # Audit log
    audit = AuditLog(
        user_id=actor_user_id,
        action="onboarding_executed",
        resource_type="user",
        resource_id=entra_id or payload.get("upn", ""),
        details={
            "target_email": payload.get("work_email") or payload.get("upn"),
            "target_upn": payload.get("upn"),
            "status": status,
            "steps_completed": steps_completed,
            "steps_failed": steps_failed_names,
            "run_id": run.id,
        },
        success=(status in ("completed", "partial")),
    )
    db.add(audit)
    run.audit_log_id = audit.id
    await db.commit()

    return {
        "run_id": run.id,
        "status": status,
        "target_upn": payload.get("upn"),
        "target_display_name": payload.get("display_name"),
        "entra_id": entra_id,
        "temp_password": temp_password,
        "steps": step_results,
        "steps_completed": steps_completed,
        "steps_failed": steps_failed_names,
    }
