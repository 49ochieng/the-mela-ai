"""Tamper-evident audit logging.

Each audit entry is hashed and chained to the previous entry's hash so
that any silent edit, deletion, or reorder of historical rows breaks the
chain. The hash covers the canonical JSON of the row's payload (action,
tenant, user, entity, redacted details, timestamp, sequence number) plus
the prior row's hash.

This is *not* a substitute for write-once storage — a determined attacker
with database access could re-compute the chain. But it raises the cost
significantly and gives us a deterministic verifier to surface tampering
during incident response. For full WORM semantics, ship the chain to an
append-only sink (Azure Monitor / Log Analytics) — we already emit every
audit entry as a structured log line so a SIEM can co-store the chain.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import AuditLog
from ...middleware.request_context import current as _current_request_ctx

_audit_logger = logging.getLogger("audit")


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: str, payload: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update((prev_hash or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(_canonical(payload).encode("utf-8"))
    return h.hexdigest()


async def _last_chain_state(session: AsyncSession) -> tuple[str, int]:
    row = (
        await session.execute(
            select(AuditLog.entry_hash, AuditLog.seq)
            .order_by(desc(AuditLog.seq))
            .limit(1)
        )
    ).first()
    if row is None:
        return "", 0
    return (row[0] or ""), (row[1] or 0)


async def log(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    ctx = _current_request_ctx()
    request_id = ctx.request_id if ctx else None
    ip = ctx.ip if ctx else None
    ua = ctx.user_agent if ctx else None

    prev_hash, prev_seq = await _last_chain_state(session)
    seq = prev_seq + 1
    created = datetime.utcnow()
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "details": details or {},
        "created_at": created.isoformat(timespec="microseconds"),
        "seq": seq,
        "ip": ip,
        "user_agent": ua,
        "request_id": request_id,
    }
    entry_hash = _hash(prev_hash, payload)
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details_json=details or {},
            created_at=created,
            prev_hash=prev_hash or None,
            entry_hash=entry_hash,
            seq=seq,
            ip=ip,
            user_agent=ua,
            request_id=request_id,
        )
    )
    # Flush so a second audit.log() in the same transaction reads our seq
    # and entry_hash when computing its own prev_hash. Without this, two
    # entries in the same request would fork the chain.
    await session.flush()
    _audit_logger.info(
        "audit",
        extra={
            "audit": {**payload, "entry_hash": entry_hash, "prev_hash": prev_hash or None},
        },
    )


async def verify_chain(session: AsyncSession) -> dict[str, Any]:
    """Replay the chain and report the first divergence, if any."""
    rows = (
        await session.execute(select(AuditLog).order_by(AuditLog.seq.asc()))
    ).scalars().all()
    prev_hash = ""
    expected_seq = 1
    for row in rows:
        if row.seq != expected_seq:
            return {"ok": False, "checked": expected_seq - 1,
                    "broken_at": row.seq, "reason": f"seq gap (expected {expected_seq})"}
        if (row.prev_hash or "") != prev_hash:
            return {"ok": False, "checked": expected_seq - 1,
                    "broken_at": row.seq, "reason": "prev_hash mismatch"}
        payload = {
            "tenant_id": row.tenant_id,
            "user_id": row.user_id,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "details": row.details_json or {},
            "created_at": row.created_at.isoformat(timespec="microseconds"),
            "seq": row.seq,
            "ip": row.ip,
            "user_agent": row.user_agent,
            "request_id": row.request_id,
        }
        if _hash(prev_hash, payload) != (row.entry_hash or ""):
            return {"ok": False, "checked": expected_seq - 1,
                    "broken_at": row.seq, "reason": "entry_hash mismatch"}
        prev_hash = row.entry_hash or ""
        expected_seq += 1
    return {"ok": True, "checked": expected_seq - 1, "broken_at": None, "reason": None}
