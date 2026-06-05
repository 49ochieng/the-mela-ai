"""
Mela AI - Enterprise Employee Offboarding Service
Full workflow: disable sign-in → revoke sessions → remove licenses →
              remove groups → notifications → delete (if explicitly approved)
"""

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)


def _ok(step: str, detail: str = "") -> dict:
    return {"step": step, "status": "ok", "detail": detail}

def _fail(step: str, error: str) -> dict:
    return {"step": step, "status": "failed", "error": error}

def _skip(step: str, reason: str = "not requested") -> dict:
    return {"step": step, "status": "skipped", "reason": reason}


async def resolve_offboarding_target(email_or_upn: str) -> Optional[dict]:
    """
    Resolve the exact target user from Entra. Returns user dict or None.
    Never returns ambiguous results.
    """
    from app.services.graph_service import GraphAPIService
    gs = GraphAPIService()
    user = await gs.get_user_app(email_or_upn)
    return user


async def build_offboarding_preview(payload: dict) -> dict:
    """Validate and build a dry-run summary. Does NOT execute anything."""
    errors = []
    warnings = []

    target = payload.get("target_email", "").strip()
    if not target:
        errors.append("Target user email or UPN is required.")
        return {"valid": False, "errors": errors}

    # Resolve the exact user
    from app.services.graph_service import GraphAPIService
    gs = GraphAPIService()
    user = await gs.get_user_app(target)
    if not user:
        errors.append(f"User '{target}' was not found in Entra ID.")
        return {"valid": False, "errors": errors}

    user_id = user.get("id")
    display_name = user.get("displayName", target)
    account_enabled = user.get("accountEnabled", True)

    if not account_enabled:
        warnings.append("Account is already disabled in Entra ID.")

    # Get current licenses and groups for preview
    licenses = await gs.get_user_licenses_app(user_id)
    groups = await gs.get_user_groups_app(user_id)
    # Filter to actual Groups only (not roles)
    group_list = [g for g in groups if g.get("@odata.type") == "#microsoft.graph.group"]

    actions_planned = []
    if payload.get("disable_sign_in", True):
        actions_planned.append("disable_sign_in (accountEnabled → false)")
    if payload.get("revoke_sessions", True):
        actions_planned.append("revoke_active_sessions")
    if payload.get("remove_licenses", True) and licenses:
        actions_planned.append(f"remove_licenses ({len(licenses)} license(s))")
    if payload.get("remove_groups", True) and group_list:
        actions_planned.append(f"remove_from_groups ({len(group_list)} group(s))")
    if payload.get("send_notifications"):
        actions_planned.append("send_offboarding_notification")
    if payload.get("delete_account"):
        actions_planned.append("⚠️  DELETE ACCOUNT — IRREVERSIBLE")
        warnings.append("Account deletion is PERMANENT and cannot be undone. Double confirmation required.")

    return {
        "valid": True,
        "errors": [],
        "warnings": warnings,
        "target_entra_id": user_id,
        "target_display_name": display_name,
        "target_upn": user.get("userPrincipalName"),
        "account_enabled": account_enabled,
        "current_license_count": len(licenses),
        "current_group_count": len(group_list),
        "licenses": [{"id": lic.get("id"), "skuId": lic.get("skuId"), "name": lic.get("skuPartNumber", "?")} for lic in licenses],
        "groups": [{"id": g.get("id"), "displayName": g.get("displayName", "?")} for g in group_list],
        "actions_planned": actions_planned,
        "delete_account": payload.get("delete_account", False),
    }


async def run_offboarding(
    db: AsyncSession,
    payload: dict,
    actor_user_id: str,
    actor_email: str,
) -> dict:
    """
    Execute the offboarding workflow. Each step is best-effort.
    Delete requires explicit confirm_delete=True AND confirm_delete_second=True.
    """
    from app.services.graph_service import GraphAPIService
    from app.models import HRWorkflowRun, AuditLog

    gs = GraphAPIService()
    step_results: list = []
    steps_completed: list = []
    steps_failed_names: list = []

    target = payload.get("target_email", "").strip()

    # Resolve user first
    user = await gs.get_user_app(target)
    if not user:
        raise ValueError(f"Target user '{target}' not found in Entra ID. Offboarding aborted.")

    user_id = user["id"]
    display_name = user.get("displayName", target)
    upn = user.get("userPrincipalName", target)

    # Save run row
    run = HRWorkflowRun(
        workflow_type="offboarding",
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        target_email=target,
        target_upn=upn,
        target_entra_id=user_id,
        target_display_name=display_name,
        payload_json=json.dumps({k: v for k, v in payload.items() if k not in ("confirm_delete", "confirm_delete_second")}),
        step_results_json="[]",
        status="running",
        started_at=datetime.utcnow(),
        approval_reference=payload.get("approval_reference"),
    )
    db.add(run)
    await db.flush()

    async def _run_step(step_name: str, coro):
        nonlocal step_results, steps_completed, steps_failed_names
        try:
            result = await coro
            step_results.append(_ok(step_name, str(result)[:200] if result else ""))
            steps_completed.append(step_name)
            logger.info("Offboarding step OK: %s for %s", step_name, upn)
        except Exception as e:
            step_results.append(_fail(step_name, str(e)[:300]))
            steps_failed_names.append(step_name)
            logger.warning("Offboarding step FAILED: %s — %s", step_name, e)

    # ── Step 1: Disable sign-in ────────────────────────────────────────────────
    if payload.get("disable_sign_in", True):
        await _run_step("disable_sign_in", gs.set_account_enabled(user_id, False))
    else:
        step_results.append(_skip("disable_sign_in"))

    # ── Step 2: Revoke sessions ────────────────────────────────────────────────
    if payload.get("revoke_sessions", True):
        await _run_step("revoke_sessions", gs.revoke_sign_in_sessions(user_id))
    else:
        step_results.append(_skip("revoke_sessions"))

    # ── Step 3: Remove licenses ────────────────────────────────────────────────
    if payload.get("remove_licenses", True):
        licenses = await gs.get_user_licenses_app(user_id)
        sku_ids = [lic["skuId"] for lic in licenses if lic.get("skuId")]
        if sku_ids:
            await _run_step("remove_licenses", gs.remove_licenses(user_id, sku_ids))
        else:
            step_results.append(_skip("remove_licenses", "no licenses assigned"))
    else:
        step_results.append(_skip("remove_licenses"))

    # ── Step 4: Remove from groups ────────────────────────────────────────────
    if payload.get("remove_groups", True):
        groups = await gs.get_user_groups_app(user_id)
        group_list = [g for g in groups if g.get("@odata.type") == "#microsoft.graph.group"]
        if group_list:
            for g in group_list:
                await _run_step(f"remove_from_group:{g['id']}", gs.remove_from_group(user_id, g["id"]))
        else:
            step_results.append(_skip("remove_from_groups", "no group memberships"))
    else:
        step_results.append(_skip("remove_from_groups"))

    # ── Step 5: Offboarding notification ──────────────────────────────────────
    if payload.get("send_notifications"):
        sender_email = (
            getattr(settings, "GRAPH_SENDER_EMAIL", None)
            or actor_email
            or (settings.bootstrap_admin_email_list[0] if settings.bootstrap_admin_email_list else None)
        )
        notify_recipients = payload.get("notification_recipients") or [actor_email]
        notify_recipients = [r for r in notify_recipients if r]
        if sender_email and notify_recipients:
            reason = payload.get("reason", "No reason provided")
            effective_date = payload.get("effective_date", datetime.utcnow().strftime("%Y-%m-%d"))
            body = (
                f"<h3>Offboarding Notification: {display_name}</h3>"
                f"<p>The following user has been offboarded from Armely systems:</p>"
                f"<ul>"
                f"<li><strong>Name:</strong> {display_name}</li>"
                f"<li><strong>UPN:</strong> {upn}</li>"
                f"<li><strong>Effective Date:</strong> {effective_date}</li>"
                f"<li><strong>Reason:</strong> {reason}</li>"
                f"<li><strong>Executed By:</strong> {actor_email}</li>"
                f"</ul>"
                f"<p>Actions taken: {', '.join(steps_completed)}</p>"
                f"<p style='color:#888;font-size:12px;'>Sent automatically by Mela AI HR Automation.</p>"
            )
            await _run_step("send_notification",
                gs.send_email_app_only(
                    sender_email=sender_email,
                    to=notify_recipients,
                    subject=f"[HR] Offboarding Completed: {display_name}",
                    body=body,
                    is_html=True,
                ))
        else:
            step_results.append(_fail("send_notification", "No sender or recipients configured"))
            steps_failed_names.append("send_notification")
    else:
        step_results.append(_skip("send_notification"))

    # ── Step 6: Delete account (REQUIRES double confirmation) ─────────────────
    if payload.get("delete_account"):
        if payload.get("confirm_delete") and payload.get("confirm_delete_second"):
            await _run_step("delete_account", gs.delete_entra_user(user_id))
        else:
            step_results.append(_fail("delete_account",
                "Delete requires both confirm_delete=true AND confirm_delete_second=true"))
            steps_failed_names.append("delete_account")
    else:
        step_results.append(_skip("delete_account"))

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

    audit = AuditLog(
        user_id=actor_user_id,
        action="offboarding_executed",
        resource_type="user",
        resource_id=user_id,
        details={
            "target_email": target,
            "target_upn": upn,
            "target_entra_id": user_id,
            "status": status,
            "steps_completed": steps_completed,
            "steps_failed": steps_failed_names,
            "delete_executed": payload.get("delete_account") and "delete_account" in steps_completed,
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
        "target_email": target,
        "target_display_name": display_name,
        "target_upn": upn,
        "steps": step_results,
        "steps_completed": steps_completed,
        "steps_failed": steps_failed_names,
        "account_deleted": "delete_account" in steps_completed,
    }
