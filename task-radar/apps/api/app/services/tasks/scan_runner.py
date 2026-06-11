"""Scan orchestration — the worker entrypoint per scan_run.

This module is intentionally verbose about *what happened* during a scan.
Each non-trivial step writes a `ScanEvent` row and increments a per-stage
counter on the parent `ScanRun`. The UI uses these to explain to the user
why a scan ended with N errors / 0 tasks instead of presenting an opaque
"completed" status.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...enums import (
    ScanEventStatus,
    ScanStage,
    ScanStatus,
    ScanType,
    SourceType,
    StorageStatus,
    TaskStatus,
)
from ...models import (
    ScanEvent,
    ScanRun,
    ScanSettings,
    SourceMessage,
    TaskAttachment,
    User,
)
from ...schemas import ExtractionResult
from ...utils.noise import is_noise_email, is_noise_teams
from ..ai.extractor import (
    ExtractionDiagnostics,
    ExtractorConfigError,
    extract_with_diagnostics,
)
from ..graph import outlook as outlook_svc
from ..graph import teams as teams_svc
from ..graph.client import GraphClient, GraphHTTPError, NeedsReconnect
from ..storage.storage import get_storage
from .audit import log
from .dedup import message_already_seen
from .normalize import normalize_email, normalize_teams, normalize_teams_chat
from .persistence import LOW_CONFIDENCE_THRESHOLD, persist_extraction

logger = logging.getLogger(__name__)


# ── public entrypoint ─────────────────────────────────────────────────
async def run_scan(session: AsyncSession, scan_run_id: str) -> None:
    scan: ScanRun | None = await session.get(ScanRun, scan_run_id)
    if scan is None:
        logger.error("Scan %s not found", scan_run_id)
        return
    user = await session.get(User, scan.user_id)
    if user is None:
        await _fail(session, scan, "user not found", category="config")
        return
    settings_res = await session.execute(
        select(ScanSettings).where(
            ScanSettings.tenant_id == scan.tenant_id,
            ScanSettings.user_id == scan.user_id,
        )
    )
    settings = settings_res.scalar_one_or_none()
    if settings is None:
        await _fail(session, scan, "scan settings missing", category="config")
        return

    scan.status = ScanStatus.RUNNING.value
    scan.started_at = datetime.utcnow()
    await session.commit()

    cats: Counter[str] = Counter()
    created_task_ids: list[str] = []
    config_error: Optional[str] = None

    try:
        client = await GraphClient.for_user(session, user.id, scan.tenant_id)
    except NeedsReconnect as exc:
        await _record_event(
            session, scan, source=SourceType.EMAIL.value, graph_message_id=None,
            stage=ScanStage.CONFIG, status=ScanEventStatus.ERROR,
            category="needs_reconnect", message=str(exc), retryable=False,
        )
        cats["needs_reconnect"] += 1
        await _finalize(
            session, scan, settings, cats, created_task_ids,
            config_error="needs_reconnect",
        )
        return

    try:
        if (
            scan.scan_type in (ScanType.EMAIL.value, ScanType.ALL.value)
            and settings.email_scan_enabled
        ):
            try:
                await _scan_email(
                    session, client, user, scan, settings, cats, created_task_ids,
                )
            except ExtractorConfigError as exc:
                config_error = f"ai_config: {exc}"
                await _record_event(
                    session, scan, source=SourceType.EMAIL.value,
                    graph_message_id=None, stage=ScanStage.CONFIG,
                    status=ScanEventStatus.ERROR, category="ai_config",
                    message=str(exc)[:300], retryable=False,
                )
                cats["ai_config"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Email scan crashed")
                cats["email_stage_crash"] += 1
                await _record_event(
                    session, scan, source=SourceType.EMAIL.value,
                    graph_message_id=None, stage=ScanStage.GRAPH_FETCH,
                    status=ScanEventStatus.ERROR, category="email_stage_crash",
                    message=str(exc)[:300], retryable=True,
                )

        if (
            scan.scan_type in (ScanType.TEAMS.value, ScanType.ALL.value)
            and settings.teams_scan_enabled
        ):
            try:
                await _scan_teams(
                    session, client, user, scan, settings, cats, created_task_ids,
                )
            except ExtractorConfigError as exc:
                config_error = config_error or f"ai_config: {exc}"
                await _record_event(
                    session, scan, source=SourceType.TEAMS.value,
                    graph_message_id=None, stage=ScanStage.CONFIG,
                    status=ScanEventStatus.ERROR, category="ai_config",
                    message=str(exc)[:300], retryable=False,
                )
                cats["ai_config"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Teams scan crashed")
                cats["teams_stage_crash"] += 1
                await _record_event(
                    session, scan, source=SourceType.TEAMS.value,
                    graph_message_id=None, stage=ScanStage.GRAPH_FETCH,
                    status=ScanEventStatus.ERROR, category="teams_stage_crash",
                    message=str(exc)[:300], retryable=True,
                )

            # Scan 1:1 and group chats (requires Chat.Read scope)
            try:
                await _scan_teams_chats(
                    session, client, user, scan, settings, cats, created_task_ids,
                )
            except ExtractorConfigError as exc:
                config_error = config_error or f"ai_config: {exc}"
                await _record_event(
                    session, scan, source=SourceType.TEAMS.value,
                    graph_message_id=None, stage=ScanStage.CONFIG,
                    status=ScanEventStatus.ERROR, category="ai_config",
                    message=str(exc)[:300], retryable=False,
                )
                cats["ai_config"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Teams chat scan crashed")
                cats["teams_chat_crash"] += 1
                await _record_event(
                    session, scan, source=SourceType.TEAMS.value,
                    graph_message_id=None, stage=ScanStage.GRAPH_FETCH,
                    status=ScanEventStatus.ERROR, category="teams_chat_crash",
                    message=str(exc)[:300], retryable=True,
                )
    finally:
        await client.aclose()

    await _finalize(
        session, scan, settings, cats, created_task_ids, config_error=config_error,
    )


# ── stage runners ─────────────────────────────────────────────────────
async def _scan_email(
    session: AsyncSession,
    client: GraphClient,
    user: User,
    scan: ScanRun,
    settings: ScanSettings,
    cats: Counter[str],
    created_task_ids: list[str],
) -> None:
    since = settings.last_email_scan_at or (
        datetime.utcnow() - timedelta(hours=_lookback_hours(scan, settings))
    )

    use_delta = bool(settings.email_delta_link)
    new_delta_link: str | None = None
    try:
        if use_delta:
            raw, new_delta_link = await outlook_svc.get_inbox_messages_delta(
                client, settings.email_delta_link,
            )
        else:
            raw = await outlook_svc.get_messages_since(client, since)
            # Prime a fresh delta cycle in parallel so subsequent scans
            # are cheap. Failure here is non-fatal.
            try:
                _, primed = await outlook_svc.get_inbox_messages_delta(client, None)
                new_delta_link = primed
            except Exception:  # noqa: BLE001
                new_delta_link = None
    except GraphHTTPError as exc:
        # Common case: a stale deltaLink returns 410 Gone — drop it and
        # fall back to a time-window fetch so the scan still succeeds.
        if use_delta and exc.status in (400, 410):
            settings.email_delta_link = None
            try:
                raw = await outlook_svc.get_messages_since(client, since)
                new_delta_link = None
            except GraphHTTPError as exc2:
                await _record_event(
                    session, scan, source=SourceType.EMAIL.value, graph_message_id=None,
                    stage=ScanStage.GRAPH_FETCH, status=ScanEventStatus.ERROR,
                    category=f"graph_{exc2.status}",
                    message=f"Outlook fetch failed (post-delta-reset): {exc2.status}",
                    retryable=exc2.status in (429,) or exc2.status >= 500,
                )
                cats[f"graph_{exc2.status}"] += 1
                scan.errors_count += 1
                return
        else:
            await _record_event(
                session, scan, source=SourceType.EMAIL.value, graph_message_id=None,
                stage=ScanStage.GRAPH_FETCH, status=ScanEventStatus.ERROR,
                category=f"graph_{exc.status}", message=f"Outlook fetch failed: {exc.status}",
                retryable=exc.status in (429,) or exc.status >= 500,
            )
            cats[f"graph_{exc.status}"] += 1
            scan.errors_count += 1
            return

    storage = get_storage()
    new_high_water = since
    ai_budget = max(0, settings.max_ai_calls_per_scan - scan.ai_attempted_count)
    msg_budget = max(0, settings.max_messages_per_scan)
    include_attachments = bool((scan.source_scope or {}).get("include_attachments", True))

    for msg in raw:
        if scan.messages_scanned >= msg_budget:
            cats["budget_messages"] += 1
            await _record_event(
                session, scan, source=SourceType.EMAIL.value,
                graph_message_id=str(msg.get("id")), stage=ScanStage.GRAPH_FETCH,
                status=ScanEventStatus.SKIPPED, category="budget_messages",
                message="max_messages_per_scan reached", retryable=False,
            )
            break
        scan.messages_scanned += 1

        try:
            norm = normalize_email(msg)
        except Exception as exc:  # noqa: BLE001
            cats["normalize"] += 1
            scan.errors_count += 1
            await _record_event(
                session, scan, source=SourceType.EMAIL.value,
                graph_message_id=str(msg.get("id")), stage=ScanStage.NORMALIZE,
                status=ScanEventStatus.ERROR, category="normalize",
                message=str(exc)[:200], retryable=False,
            )
            continue

        if norm["received_at"] and norm["received_at"] > new_high_water:
            new_high_water = norm["received_at"]

        if is_noise_email(norm["sender_email"], norm["subject_or_channel"], norm["body_excerpt"]):
            scan.messages_skipped += 1
            scan.noise_skipped_count += 1
            continue

        if await message_already_seen(
            session,
            tenant_id=scan.tenant_id, user_id=scan.user_id,
            source_type=SourceType.EMAIL.value,
            graph_message_id=norm["graph_message_id"],
            internet_message_id=norm["internet_message_id"],
            body_hash=norm["body_hash"],
            received_at=norm["received_at"],
        ):
            scan.messages_skipped += 1
            scan.duplicate_skipped_count += 1
            continue

        sm = _save_source_message(
            session, tenant_id=scan.tenant_id, user_id=scan.user_id, norm=norm,
        )

        if ai_budget <= 0:
            cats["budget_ai"] += 1
            await _record_event(
                session, scan, source=SourceType.EMAIL.value,
                graph_message_id=norm["graph_message_id"], stage=ScanStage.AI_EXTRACT,
                status=ScanEventStatus.SKIPPED, category="budget_ai",
                message="max_ai_calls_per_scan reached", retryable=False,
            )
            continue
        ai_budget -= 1
        scan.ai_attempted_count += 1

        extraction, diag = await extract_with_diagnostics(norm["ai_payload"])
        await _handle_extraction(
            session, scan, sm, extraction, diag,
            source=SourceType.EMAIL.value, cats=cats,
            created_task_ids=created_task_ids,
        )

        if include_attachments and norm["has_attachments"] and extraction.has_task:
            await _archive_email_attachments(
                client, session, scan, sm, msg["id"], storage, cats,
            )
        sm.processed_at = datetime.utcnow()
        await session.flush()

    settings.last_email_scan_at = new_high_water
    if new_delta_link:
        settings.email_delta_link = new_delta_link
    await session.commit()


async def _scan_teams(
    session: AsyncSession,
    client: GraphClient,
    user: User,
    scan: ScanRun,
    settings: ScanSettings,
    cats: Counter[str],
    created_task_ids: list[str],
) -> None:
    if not settings.selected_channel_ids:
        # Auto-discover: fan out to every channel in every joined team
        # (capped to keep Graph cost bounded). Users can still narrow scope
        # later in Settings → Teams.
        try:
            auto_entries = await _default_channel_entries(client)
        except Exception as exc:  # noqa: BLE001
            await _record_event(
                session, scan, source=SourceType.TEAMS.value, graph_message_id=None,
                stage=ScanStage.CONFIG, status=ScanEventStatus.ERROR,
                category="teams_autodiscover",
                message=f"Failed to auto-discover Teams channels: {exc}"[:200],
                retryable=True,
            )
            return
        if not auto_entries:
            await _record_event(
                session, scan, source=SourceType.TEAMS.value, graph_message_id=None,
                stage=ScanStage.CONFIG, status=ScanEventStatus.SKIPPED,
                category="no_channels",
                message="No Teams channels found for this user.",
                retryable=False,
            )
            return
        channel_entries = auto_entries
    else:
        channel_entries = list(settings.selected_channel_ids)

    since = settings.last_teams_scan_at or (
        datetime.utcnow() - timedelta(hours=_lookback_hours(scan, settings))
    )
    new_high_water = since
    ai_budget = max(0, settings.max_ai_calls_per_scan - scan.ai_attempted_count)
    msg_budget = max(0, settings.max_messages_per_scan)

    for entry in channel_entries:
        try:
            team_id, channel_id, channel_name, team_name = _parse_channel_entry(entry)
        except ValueError:
            cats["bad_channel_entry"] += 1
            await _record_event(
                session, scan, source=SourceType.TEAMS.value, graph_message_id=None,
                stage=ScanStage.CONFIG, status=ScanEventStatus.ERROR,
                category="bad_channel_entry",
                message=f"Invalid channel entry: {entry[:80]}", retryable=False,
            )
            continue

        try:
            messages = await teams_svc.get_channel_messages_since(
                client, team_id, channel_id, since,
            )
        except GraphHTTPError as exc:
            # 400 / 403 / 404 on individual channels are common (private chats,
            # archived channels, RSC not granted). Just count them silently
            # rather than spamming a scan_event per channel.
            if exc.status in (400, 403, 404):
                cats[f"teams_channel_skipped_{exc.status}"] += 1
                logger.debug("Skipping channel %s (%s) team=%s: %s",
                             channel_name, channel_id, team_name, exc.status)
                continue
            cat = (
                "graph_permission_missing" if exc.status == 401
                else f"graph_{exc.status}"
            )
            cats[cat] += 1
            scan.errors_count += 1
            await _record_event(
                session, scan, source=SourceType.TEAMS.value, graph_message_id=None,
                stage=ScanStage.GRAPH_FETCH, status=ScanEventStatus.ERROR,
                category=cat,
                message=(
                    "Teams permission missing or admin consent required"
                    if exc.status == 401
                    else f"Teams channel {channel_id} fetch failed: {exc.status}"
                ),
                retryable=exc.status in (429,) or exc.status >= 500,
                details={"team_id": team_id, "channel_id": channel_id, "status": exc.status},
            )
            continue
        except Exception as exc:  # noqa: BLE001
            cats["teams_fetch"] += 1
            scan.errors_count += 1
            await _record_event(
                session, scan, source=SourceType.TEAMS.value, graph_message_id=None,
                stage=ScanStage.GRAPH_FETCH, status=ScanEventStatus.ERROR,
                category="teams_fetch", message=str(exc)[:200], retryable=True,
                details={"team_id": team_id, "channel_id": channel_id},
            )
            continue

        for msg in messages:
            if scan.messages_scanned >= msg_budget:
                cats["budget_messages"] += 1
                break
            scan.messages_scanned += 1

            try:
                # Optionally fetch parent / replies for thread context
                thread_context = ""
                if settings.include_thread_context and msg.get("replyToId"):
                    try:
                        replies = await teams_svc.get_channel_message_replies(
                            client, team_id, channel_id, msg["replyToId"],
                        )
                        # tiny excerpt only — never full bodies
                        thread_context = " | ".join(
                            ((r.get("body") or {}).get("content") or "")[:160]
                            for r in (replies or [])[:3]
                        )
                    except Exception:  # noqa: BLE001
                        thread_context = ""

                norm = normalize_teams(
                    msg, team_id=team_id, channel_id=channel_id,
                    channel_name=channel_name, team_name=team_name,
                    user_entra_id=user.entra_user_id,
                    user_upn=getattr(user, "upn", None) or user.email,
                    user_email=user.email,
                    user_display_name=user.display_name,
                    thread_context=thread_context,
                )
            except Exception as exc:  # noqa: BLE001
                cats["normalize"] += 1
                scan.errors_count += 1
                await _record_event(
                    session, scan, source=SourceType.TEAMS.value,
                    graph_message_id=str(msg.get("id")),
                    stage=ScanStage.NORMALIZE, status=ScanEventStatus.ERROR,
                    category="normalize", message=str(exc)[:200], retryable=False,
                )
                continue

            if is_noise_teams(norm["body_excerpt"], norm.get("is_mention", False)):
                scan.messages_skipped += 1
                scan.noise_skipped_count += 1
                continue

            if norm["received_at"] and norm["received_at"] > new_high_water:
                new_high_water = norm["received_at"]

            if await message_already_seen(
                session, tenant_id=scan.tenant_id, user_id=scan.user_id,
                source_type=SourceType.TEAMS.value,
                graph_message_id=norm["graph_message_id"],
                internet_message_id=None,
                body_hash=norm["body_hash"],
                received_at=norm["received_at"],
            ):
                scan.messages_skipped += 1
                scan.duplicate_skipped_count += 1
                continue

            sm = _save_source_message(
                session, tenant_id=scan.tenant_id, user_id=scan.user_id, norm=norm,
            )

            if ai_budget <= 0:
                cats["budget_ai"] += 1
                continue
            ai_budget -= 1
            scan.ai_attempted_count += 1

            extraction, diag = await extract_with_diagnostics(norm["ai_payload"])
            await _handle_extraction(
                session, scan, sm, extraction, diag,
                source=SourceType.TEAMS.value, cats=cats,
                created_task_ids=created_task_ids,
            )

            for f in norm.get("files") or []:
                session.add(
                    TaskAttachment(
                        tenant_id=scan.tenant_id, user_id=scan.user_id,
                        task_id=sm.id, source_message_id=sm.id,
                        file_name=f["name"], content_type=f.get("content_type"),
                        source_url=f.get("source_url"),
                        storage_status=StorageStatus.LINKED.value,
                    )
                )
            sm.processed_at = datetime.utcnow()
            await session.flush()

    settings.last_teams_scan_at = new_high_water
    await session.commit()


async def _scan_teams_chats(
    session: AsyncSession,
    client: GraphClient,
    user: User,
    scan: ScanRun,
    settings: ScanSettings,
    cats: Counter[str],
    created_task_ids: list[str],
) -> None:
    """Scan 1:1 and group chats via /me/chats (requires Chat.Read)."""
    try:
        chats = await teams_svc.list_chats(client)
    except GraphHTTPError as exc:
        cat = "graph_permission_missing" if exc.status in (401, 403) else f"graph_{exc.status}"
        cats[cat] += 1
        scan.errors_count += 1
        await _record_event(
            session, scan, source=SourceType.TEAMS.value, graph_message_id=None,
            stage=ScanStage.GRAPH_FETCH, status=ScanEventStatus.ERROR,
            category=cat,
            message=(
                "Chat.Read permission missing — re-connect Microsoft 365 to grant consent"
                if exc.status in (401, 403)
                else f"Chat list failed: {exc.status}"
            ),
            retryable=exc.status in (429,) or exc.status >= 500,
        )
        return
    except Exception as exc:  # noqa: BLE001
        cats["teams_chat_list"] += 1
        scan.errors_count += 1
        await _record_event(
            session, scan, source=SourceType.TEAMS.value, graph_message_id=None,
            stage=ScanStage.GRAPH_FETCH, status=ScanEventStatus.ERROR,
            category="teams_chat_list", message=str(exc)[:200], retryable=True,
        )
        return

    since = settings.last_teams_scan_at or (
        datetime.utcnow() - timedelta(hours=_lookback_hours(scan, settings))
    )
    new_high_water = since
    ai_budget = max(0, settings.max_ai_calls_per_scan - scan.ai_attempted_count)
    msg_budget = max(0, settings.max_messages_per_scan - scan.messages_scanned)

    for chat in chats:
        chat_id = chat.get("id")
        if not chat_id:
            continue
        chat_type = chat.get("chatType") or "group"  # oneOnOne | group | meeting

        # Build a human-readable label for the chat
        if chat_type == "oneOnOne":
            # Fetch members to label as "Chat with <Name>"
            members = await teams_svc.get_chat_members(client, chat_id)
            other = next(
                (
                    m.get("displayName") or m.get("email") or "someone"
                    for m in members
                    if (m.get("userId") or m.get("id")) != user.entra_user_id
                ),
                "someone",
            )
            chat_label = f"Chat with {other}"
        else:
            chat_label = (chat.get("topic") or "").strip() or "Group chat"

        try:
            messages = await teams_svc.get_chat_messages_since(client, chat_id, since)
        except GraphHTTPError as exc:
            # Some meeting chats are not readable via delegated scope even when
            # the chat list itself is visible. Treat as skipped noise.
            if exc.status == 403 and chat_type in ("meeting", "unknownFutureValue"):
                cats["teams_chat_skipped_403"] += 1
                continue
            cat = "graph_permission_missing" if exc.status in (401, 403) else f"graph_{exc.status}"
            cats[cat] += 1
            scan.errors_count += 1
            await _record_event(
                session, scan, source=SourceType.TEAMS.value, graph_message_id=None,
                stage=ScanStage.GRAPH_FETCH, status=ScanEventStatus.ERROR,
                category=cat,
                message=f"Chat {chat_id} messages failed: {exc.status}",
                retryable=exc.status in (429,) or exc.status >= 500,
                details={"chat_id": chat_id, "chat_type": chat_type},
            )
            continue
        except Exception as exc:  # noqa: BLE001
            cats["teams_chat_fetch"] += 1
            scan.errors_count += 1
            await _record_event(
                session, scan, source=SourceType.TEAMS.value, graph_message_id=None,
                stage=ScanStage.GRAPH_FETCH, status=ScanEventStatus.ERROR,
                category="teams_chat_fetch", message=str(exc)[:200], retryable=True,
                details={"chat_id": chat_id},
            )
            continue

        for msg in messages:
            # Skip system messages (member additions, call ended, etc.)
            if msg.get("messageType") not in (None, "message"):
                continue

            if scan.messages_scanned >= msg_budget + scan.messages_scanned:
                cats["budget_messages"] += 1
                break
            if scan.messages_scanned >= settings.max_messages_per_scan:
                cats["budget_messages"] += 1
                break
            scan.messages_scanned += 1

            try:
                norm = normalize_teams_chat(
                    msg,
                    chat_id=chat_id,
                    chat_type=chat_type,
                    chat_label=chat_label,
                    user_entra_id=user.entra_user_id,
                    user_upn=getattr(user, "upn", None) or user.email,
                    user_email=user.email,
                    user_display_name=user.display_name,
                )
            except Exception as exc:  # noqa: BLE001
                cats["normalize"] += 1
                scan.errors_count += 1
                await _record_event(
                    session, scan, source=SourceType.TEAMS.value,
                    graph_message_id=str(msg.get("id")),
                    stage=ScanStage.NORMALIZE, status=ScanEventStatus.ERROR,
                    category="normalize", message=str(exc)[:200], retryable=False,
                )
                continue

            if is_noise_teams(norm["body_excerpt"], norm.get("is_mention", False)):
                scan.messages_skipped += 1
                scan.noise_skipped_count += 1
                continue

            if norm["received_at"] and norm["received_at"] > new_high_water:
                new_high_water = norm["received_at"]

            if await message_already_seen(
                session, tenant_id=scan.tenant_id, user_id=scan.user_id,
                source_type=SourceType.TEAMS.value,
                graph_message_id=norm["graph_message_id"],
                internet_message_id=None,
                body_hash=norm["body_hash"],
                received_at=norm["received_at"],
            ):
                scan.messages_skipped += 1
                scan.duplicate_skipped_count += 1
                continue

            sm = _save_source_message(
                session, tenant_id=scan.tenant_id, user_id=scan.user_id, norm=norm,
            )

            if ai_budget <= 0:
                cats["budget_ai"] += 1
                continue
            ai_budget -= 1
            scan.ai_attempted_count += 1

            extraction, diag = await extract_with_diagnostics(norm["ai_payload"])
            await _handle_extraction(
                session, scan, sm, extraction, diag,
                source=SourceType.TEAMS.value, cats=cats,
                created_task_ids=created_task_ids,
            )

            for f in norm.get("files") or []:
                session.add(
                    TaskAttachment(
                        tenant_id=scan.tenant_id, user_id=scan.user_id,
                        task_id=sm.id, source_message_id=sm.id,
                        file_name=f["name"], content_type=f.get("content_type"),
                        source_url=f.get("source_url"),
                        storage_status=StorageStatus.LINKED.value,
                    )
                )
            sm.processed_at = datetime.utcnow()
            await session.flush()

    # Advance high-water only if we saw newer messages
    if new_high_water > since:
        settings.last_teams_scan_at = new_high_water
    await session.commit()



def _lookback_hours(scan: ScanRun, settings: ScanSettings) -> int:
    h = (scan.source_scope or {}).get("lookback_hours")
    if isinstance(h, int) and h > 0:
        return h
    return settings.lookback_hours_first_scan


def _parse_channel_entry(entry: str) -> tuple[str, str, str, str | None]:
    """Parse ``team_id|channel_id|channel_name[|team_name]``.

    Returns (team_id, channel_id, channel_name, team_name | None). The
    fourth component is optional for backward compatibility with entries
    saved before Teams metadata was preserved.
    """
    parts = entry.split("|", 3)
    if len(parts) < 2:
        raise ValueError("expected team_id|channel_id[|channel_name][|team_name]")
    team_id, channel_id = parts[0].strip(), parts[1].strip()
    channel_name = parts[2].strip() if len(parts) >= 3 else channel_id
    team_name = parts[3].strip() if len(parts) >= 4 and parts[3].strip() else None
    if not team_id or not channel_id:
        raise ValueError("empty team_id or channel_id")
    return team_id, channel_id, channel_name, team_name


# Cap auto-discovery to bound Graph cost per scan.
# Generous defaults — the user wants ALL teams scanned. Permission-denied
# channels are silently skipped via the GraphHTTPError handler in _scan_teams.
_AUTO_TEAMS_MAX = 100
_AUTO_CHANNELS_MAX = 500


async def _default_channel_entries(client: GraphClient) -> list[str]:
    """When the user hasn't picked specific channels, scan everything they're
    in (capped). Returns a list of pipe-encoded entries usable by
    ``_parse_channel_entry``."""
    teams = await teams_svc.list_joined_teams(client)
    teams = (teams or [])[:_AUTO_TEAMS_MAX]
    logger.info("Teams auto-discovery: %d joined team(s)", len(teams))
    out: list[str] = []
    for t in teams:
        team_id = t.get("id")
        team_name = (t.get("displayName") or "").strip() or team_id
        if not team_id:
            continue
        try:
            channels = await teams_svc.list_channels(client, team_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping team %s (%s): channel list failed: %s",
                           team_name, team_id, exc)
            continue
        ch_count = 0
        for ch in channels or []:
            if len(out) >= _AUTO_CHANNELS_MAX:
                logger.info("Reached _AUTO_CHANNELS_MAX=%d", _AUTO_CHANNELS_MAX)
                return out
            ch_id = ch.get("id")
            ch_name = (ch.get("displayName") or "").strip() or ch_id
            if not ch_id:
                continue
            out.append(f"{team_id}|{ch_id}|{ch_name}|{team_name}")
            ch_count += 1
        logger.debug("Team %s: %d channel(s)", team_name, ch_count)
    logger.info("Teams auto-discovery: %d total channel(s) queued", len(out))
    return out


async def _handle_extraction(
    session: AsyncSession,
    scan: ScanRun,
    sm: SourceMessage,
    extraction: ExtractionResult,
    diag: ExtractionDiagnostics,
    *,
    source: str,
    cats: Counter[str],
    created_task_ids: list[str] | None = None,
) -> None:
    if diag.error_category:
        scan.ai_failed_count += 1
        scan.errors_count += 1
        cats[f"ai_{diag.error_category}"] += 1
        await _record_event(
            session, scan, source=source, graph_message_id=sm.graph_message_id,
            stage=ScanStage.AI_EXTRACT, status=ScanEventStatus.ERROR,
            category=diag.error_category, message=diag.error_message,
            retryable=diag.retryable, details=_safe_diag_details(diag),
        )
        return

    if not extraction.has_task:
        scan.ai_no_task_count += 1
        scan.ai_success_count += 1
        await _record_event(
            session, scan, source=source, graph_message_id=sm.graph_message_id,
            stage=ScanStage.AI_EXTRACT, status=ScanEventStatus.NO_TASK,
            category=None, message=None, retryable=False,
            details=_safe_diag_details(diag),
        )
        return

    scan.ai_success_count += 1

    try:
        created, deduped = await persist_extraction(
            session, tenant_id=scan.tenant_id, user_id=scan.user_id,
            source_message=sm, extraction=extraction,
        )
    except Exception as exc:  # noqa: BLE001
        scan.errors_count += 1
        cats["persist"] += 1
        await _record_event(
            session, scan, source=source, graph_message_id=sm.graph_message_id,
            stage=ScanStage.PERSIST, status=ScanEventStatus.ERROR,
            category="persist", message=str(exc)[:200], retryable=True,
        )
        return

    scan.tasks_found += len(extraction.tasks)
    scan.tasks_created += len(created)
    scan.tasks_deduped += deduped
    if created_task_ids is not None:
        created_task_ids.extend(t.id for t in created)

    needs_review = sum(
        1 for t in created if t.status == TaskStatus.NEEDS_REVIEW.value
    )
    if needs_review:
        scan.needs_review_count += needs_review
        await _record_event(
            session, scan, source=source, graph_message_id=sm.graph_message_id,
            stage=ScanStage.PERSIST, status=ScanEventStatus.NEEDS_REVIEW,
            category="low_confidence",
            message=f"{needs_review} task(s) below confidence {LOW_CONFIDENCE_THRESHOLD}",
            retryable=False, details=_safe_diag_details(diag),
        )
    else:
        await _record_event(
            session, scan, source=source, graph_message_id=sm.graph_message_id,
            stage=ScanStage.PERSIST, status=ScanEventStatus.SUCCESS,
            category=None, message=None, retryable=False,
            details=_safe_diag_details(diag),
        )


def _safe_diag_details(diag: ExtractionDiagnostics) -> dict[str, Any]:
    """Diagnostics safe to persist (no message body, no PII)."""
    return {
        "prompt_version": diag.prompt_version,
        "model_deployment": diag.model_deployment,
        "input_chars": diag.input_chars,
        "output_chars": diag.output_chars,
        "finish_reason": diag.finish_reason,
        "task_count": diag.task_count,
        "prompt_tokens": diag.prompt_tokens,
        "completion_tokens": diag.completion_tokens,
        "total_tokens": diag.total_tokens,
    }


def _save_source_message(
    session: AsyncSession, *, tenant_id: str, user_id: str, norm: dict,
) -> SourceMessage:
    sm = SourceMessage(
        tenant_id=tenant_id, user_id=user_id,
        source_type=norm["source_type"], graph_message_id=norm["graph_message_id"],
        internet_message_id=norm.get("internet_message_id"),
        conversation_id=norm.get("conversation_id"),
        reply_to_id=norm.get("reply_to_id"),
        sender_name=norm.get("sender_name"),
        sender_email=norm.get("sender_email"),
        recipients_json=norm.get("recipients_json") or {},
        subject_or_channel=norm.get("subject_or_channel"),
        body_excerpt=norm.get("body_excerpt"),
        body_hash=norm.get("body_hash"),
        source_link=norm.get("source_link"),
        received_at=norm.get("received_at"),
        has_attachments=norm.get("has_attachments", False),
        raw_metadata_json=norm.get("raw_metadata_json") or {},
    )
    session.add(sm)
    return sm


async def _archive_email_attachments(
    client: GraphClient, session: AsyncSession, scan: ScanRun, sm: SourceMessage,
    message_id: str, storage, cats: Counter[str],
) -> None:
    try:
        atts = await outlook_svc.get_message_attachments(client, message_id)
    except Exception as exc:  # noqa: BLE001
        scan.attachment_failed_count += 1
        cats["attachment_list"] += 1
        await _record_event(
            session, scan, source=SourceType.EMAIL.value,
            graph_message_id=sm.graph_message_id,
            stage=ScanStage.ATTACHMENT_ARCHIVE, status=ScanEventStatus.ERROR,
            category="attachment_list", message=str(exc)[:200], retryable=True,
        )
        return
    for att in atts:
        if att.get("isInline"):
            continue
        if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        try:
            data = await outlook_svc.download_email_attachment(
                client, message_id, att["id"],
            )
            key = f"{scan.tenant_id}/{scan.user_id}/{sm.id}/{att['name']}"
            url = await storage.put(key, data, att.get("contentType"))
            status = StorageStatus.ARCHIVED.value
        except Exception as exc:  # noqa: BLE001
            scan.attachment_failed_count += 1
            cats["attachment_download"] += 1
            url = None
            status = StorageStatus.FAILED.value
            await _record_event(
                session, scan, source=SourceType.EMAIL.value,
                graph_message_id=sm.graph_message_id,
                stage=ScanStage.ATTACHMENT_ARCHIVE, status=ScanEventStatus.ERROR,
                category="attachment_download", message=str(exc)[:200], retryable=True,
            )
        session.add(
            TaskAttachment(
                tenant_id=scan.tenant_id, user_id=scan.user_id,
                task_id=sm.id, source_message_id=sm.id,
                source_attachment_id=att.get("id"),
                file_name=att.get("name") or "attachment",
                content_type=att.get("contentType"), size_bytes=att.get("size"),
                archive_url=url, storage_status=status,
            )
        )


async def _record_event(
    session: AsyncSession,
    scan: ScanRun,
    *,
    source: Optional[str],
    graph_message_id: Optional[str],
    stage: ScanStage,
    status: ScanEventStatus,
    category: Optional[str] = None,
    message: Optional[str] = None,
    retryable: bool = False,
    details: Optional[dict[str, Any]] = None,
) -> None:
    session.add(
        ScanEvent(
            tenant_id=scan.tenant_id, user_id=scan.user_id,
            scan_run_id=scan.id, source_type=source,
            graph_message_id=graph_message_id,
            stage=stage.value, status=status.value,
            category=category, message=message, retryable=retryable,
            details_json=details or {},
        )
    )


async def _finalize(
    session: AsyncSession,
    scan: ScanRun,
    settings: ScanSettings | None,
    cats: Counter[str],
    created_task_ids: list[str] | None = None,
    *,
    config_error: Optional[str] = None,
) -> None:
    # Auto-sync hooks (best-effort; failures recorded but don't crash scan)
    if settings is not None and created_task_ids:
        await _auto_sync(session, scan, settings, created_task_ids, cats)

    scan.completed_at = datetime.utcnow()
    scan.error_categories_json = dict(cats)

    if config_error and scan.tasks_created == 0:
        scan.status = ScanStatus.FAILED.value
    elif scan.errors_count > 0:
        scan.status = ScanStatus.COMPLETED_WITH_ERRORS.value
    else:
        scan.status = ScanStatus.COMPLETED.value

    if cats:
        top = ", ".join(
            f"{k}={v}" for k, v in cats.most_common(5)
        )
        scan.error_summary = (
            f"{config_error + '; ' if config_error else ''}top: {top}"
        )[:2000]
    elif config_error:
        scan.error_summary = config_error[:2000]

    await log(
        session, tenant_id=scan.tenant_id, user_id=scan.user_id,
        action="scan.completed", entity_type="scan_run", entity_id=scan.id,
        details={
            "status": scan.status,
            "messages_scanned": scan.messages_scanned,
            "tasks_created": scan.tasks_created,
            "errors_count": scan.errors_count,
            "ai_attempted": scan.ai_attempted_count,
            "ai_success": scan.ai_success_count,
            "ai_failed": scan.ai_failed_count,
            "ai_no_task": scan.ai_no_task_count,
            "needs_review": scan.needs_review_count,
        },
    )
    await session.commit()


async def _fail(
    session: AsyncSession, scan: ScanRun, reason: str, *, category: str = "config",
) -> None:
    scan.status = ScanStatus.FAILED.value
    scan.error_summary = reason
    scan.completed_at = datetime.utcnow()
    scan.error_categories_json = {category: 1}
    await session.commit()


# ── auto-sync hook ────────────────────────────────────────────────────
async def _auto_sync(
    session: AsyncSession,
    scan: ScanRun,
    settings: ScanSettings,
    created_task_ids: list[str],
    cats: Counter[str],
) -> None:
    """Push newly-created tasks to Excel and/or Planner per user policy."""
    from ...enums import Priority
    from ...models import Task
    from ..excel.sync import sync_tasks_to_excel
    from ..planner.sync import sync_tasks_to_planner, task_eligible_for_auto_planner

    # Excel: archive everything when enabled
    if (
        getattr(settings, "auto_archive_to_excel", True)
        and settings.excel_sync_enabled
    ):
        try:
            await sync_tasks_to_excel(
                session,
                tenant_id=scan.tenant_id,
                user_id=scan.user_id,
                task_ids=created_task_ids,
                scan_run_id=scan.id,
            )
            await _record_event(
                session, scan, source=None, graph_message_id=None,
                stage=ScanStage.EXCEL_SYNC, status=ScanEventStatus.SUCCESS,
                category=None,
                message=f"Synced {len(created_task_ids)} task(s) to Excel",
                retryable=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Auto Excel sync failed")
            scan.excel_failed_count += 1
            cats["excel_sync"] += 1
            await _record_event(
                session, scan, source=None, graph_message_id=None,
                stage=ScanStage.EXCEL_SYNC, status=ScanEventStatus.ERROR,
                category="excel_sync", message=str(exc)[:300], retryable=True,
            )

    # Planner: only when policy != "none" and a plan is configured
    policy = getattr(settings, "auto_sync_to_planner_priority", "none")
    if (
        settings.planner_sync_enabled
        and policy != "none"
        and not settings.planner_plan_id
        and created_task_ids
    ):
        await _record_event(
            session, scan, source=None, graph_message_id=None,
            stage=ScanStage.PLANNER_SYNC, status=ScanEventStatus.SKIPPED,
            category="no_plan_id",
            message=(
                "Planner auto-sync is enabled but no Plan is selected. "
                "Pick a Plan in Settings -> Planner to start syncing."
            ),
            retryable=False,
        )
    if (
        settings.planner_sync_enabled
        and settings.planner_plan_id
        and policy != "none"
    ):
        try:
            res = await session.execute(
                select(Task).where(Task.id.in_(created_task_ids))
            )
            tasks = res.scalars().all()
            eligible = [
                t.id for t in tasks
                if task_eligible_for_auto_planner(t, policy)
            ]
            if eligible:
                try:
                    out = await sync_tasks_to_planner(
                        session,
                        tenant_id=scan.tenant_id,
                        user_id=scan.user_id,
                        task_ids=eligible,
                        plan_id=settings.planner_plan_id,
                        bucket_id=settings.planner_bucket_id,
                    )
                    await _record_event(
                        session, scan, source=None, graph_message_id=None,
                        stage=ScanStage.PLANNER_SYNC,
                        status=ScanEventStatus.SUCCESS,
                        category=None,
                        message=(
                            f"Planner: synced {out.get('synced', 0)} / "
                            f"failed {out.get('failed', 0)} of "
                            f"{len(eligible)} eligible (policy={policy})"
                        ),
                        retryable=False,
                    )
                    if out.get("failed"):
                        scan.planner_failed_count += int(out["failed"])
                        cats["planner_sync"] += int(out["failed"])
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Auto Planner sync failed")
                    scan.planner_failed_count += len(eligible)
                    cats["planner_sync"] += 1
                    await _record_event(
                        session, scan, source=None, graph_message_id=None,
                        stage=ScanStage.PLANNER_SYNC,
                        status=ScanEventStatus.ERROR,
                        category="planner_sync",
                        message=str(exc)[:300], retryable=True,
                    )
        except Exception:  # noqa: BLE001
            logger.exception("Planner auto-sync setup failed")
