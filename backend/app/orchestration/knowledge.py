"""
Mela AI - Knowledge Base.

What Mela remembers across conversations.  Worker results land here as
short ``KnowledgeEntry`` rows; raw worker data stays in the worker and
is fetched on demand via ``data_pointer``.  This is what keeps Mela's
context window lean as workers multiply — the planner and synthesiser
read ``summary`` only.

Phase 3 surface
---------------

* ``KnowledgeStore`` ABC with ``ingest``, ``search``, ``get``, ``expire``
* ``SQLKnowledgeStore`` — keyword search over ``title + summary``.
  Vector search via Azure AI Search is Phase 4 (see comment above
  ``search``).
* ``summarise_if_needed(text, source_worker_id)`` — calls
  ``openai_service.create_completion`` (gpt-4o-mini) when text exceeds
  500 chars; truncates + warns on summariser failure.  Never blocks
  ingestion on summarisation errors.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import KnowledgeEntry

logger = logging.getLogger(__name__)


# Max length of the ``summary`` column.  Mirrors the DB column width;
# anything longer is summarised before insert.
SUMMARY_MAX_CHARS = 500
SUMMARISE_TRIGGER_CHARS = 500


# ── Expiry policy (Phase 4) ─────────────────────────────────────────────
# Per-type TTL overrides.  ``None`` means "never expires".  Anything not
# listed falls back to ``settings.KB_DEFAULT_EXPIRY_DAYS``.  This is the
# ONE place these numbers live — change here to retune the policy.
KB_EXPIRY_DAYS_BY_TYPE: dict[str, Optional[int]] = {
    "goal_result":     7,    # high churn — superseded quickly
    "task_summary":    30,
    "meeting_summary": 60,
    "worker_event":    14,
    "user_context":    None,  # never expires
}


def _resolve_expires_at(
    entry_type: str, explicit: Optional[datetime]
) -> Optional[datetime]:
    """Resolve the expires_at timestamp for a freshly-ingested entry.

    Caller-supplied value wins.  Otherwise consult the per-type override
    table; fall back to ``KB_DEFAULT_EXPIRY_DAYS`` for unknown types.
    """
    if explicit is not None:
        return explicit
    days = KB_EXPIRY_DAYS_BY_TYPE.get(
        entry_type, settings.KB_DEFAULT_EXPIRY_DAYS
    )
    if days is None:
        return None
    return datetime.utcnow() + timedelta(days=int(days))


# ── Entry payload ────────────────────────────────────────────────────────


@dataclass
class KBEntry:
    """Inbound payload for ``KnowledgeStore.ingest``.

    Mirrors the ``KnowledgeEntry`` ORM model.  Kept as a separate
    dataclass so callers in services / endpoints don't need to import
    SQLAlchemy types just to construct one.
    """

    user_id: str
    title: str
    summary: str
    entry_type: str  # see KnowledgeEntry.entry_type docstring for enum
    tenant_id: Optional[str] = None
    profile_mode: str = "personal"
    source_worker_id: Optional[str] = None
    trace_id: Optional[str] = None
    data_pointer: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    expires_at: Optional[datetime] = None


# ── Store interface ──────────────────────────────────────────────────────


class KnowledgeStore(ABC):
    """Pluggable persistence for the Knowledge Base."""

    @abstractmethod
    async def ingest(
        self, db: AsyncSession, entry: KBEntry
    ) -> KnowledgeEntry: ...

    @abstractmethod
    async def search(
        self,
        db: AsyncSession,
        *,
        tenant_id: Optional[str],
        user_id: str,
        query: str,
        limit: int = 5,
        entry_types: Optional[list[str]] = None,
    ) -> list[KnowledgeEntry]: ...

    @abstractmethod
    async def get(
        self, db: AsyncSession, entry_id: str
    ) -> Optional[KnowledgeEntry]: ...

    @abstractmethod
    async def expire(self, db: AsyncSession, entry_id: str) -> bool: ...

    @abstractmethod
    async def expire_stale(
        self, db: AsyncSession, *, tenant_id: Optional[str] = None
    ) -> int: ...

    @abstractmethod
    async def stats(self, db: AsyncSession) -> dict: ...


class SQLKnowledgeStore(KnowledgeStore):
    """SQLAlchemy-backed knowledge store.

    SQL is the **source of truth** for relational queries (admin
    stats, expiry, integrity).  When ``AZURE_SEARCH_KB_INDEX`` is set
    every successful ingest also upserts into the KB Search index and
    every ``search()`` delegates to Azure AI Search.  When it isn't,
    everything falls back to the SQL keyword path — the caller never
    sees a difference.
    """

    async def ingest(
        self, db: AsyncSession, entry: KBEntry
    ) -> KnowledgeEntry:
        # Hard cap on summary — DB column is VARCHAR(500) and the LLM
        # only ever reads this field, so don't ship a giant blob.
        title = (entry.title or "")[:500]
        summary = (entry.summary or "")[:SUMMARY_MAX_CHARS]

        # Compute embedding BEFORE the DB write so we can persist the
        # JSON-encoded vector in the same row (Phase 5 may need it).
        # Failure → None → SQL row is still written; Search upsert is
        # skipped.  Never blocks ingestion.
        embedding = await _embed_for_kb(title, summary)
        embedding_text = encode_embedding(embedding) if embedding else None

        # Resolve expires_at with the per-type policy.
        resolved_expires = _resolve_expires_at(
            entry.entry_type, entry.expires_at
        )

        row = KnowledgeEntry(
            tenant_id=entry.tenant_id,
            user_id=entry.user_id,
            profile_mode=entry.profile_mode,
            source_worker_id=entry.source_worker_id,
            trace_id=entry.trace_id,
            entry_type=entry.entry_type,
            title=title,
            summary=summary,
            data_pointer=entry.data_pointer,
            tags=list(entry.tags) if entry.tags else None,
            embedding_vector=embedding_text,
            expires_at=resolved_expires,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        # Azure AI Search upsert — best-effort.  If it raises, the SQL
        # row is the source of truth; admins can re-index from SQL.
        kb_search = _get_kb_search_client()
        if kb_search is not None:
            try:
                from app.orchestration.knowledge_search import (
                    serialise_for_index,
                )
                kb_search.upsert(
                    entry=serialise_for_index(
                        row=row, tags=list(entry.tags or []),
                    ),
                    embedding=embedding,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "KB Search upsert failed (entry persisted to SQL "
                    "only): %s", exc,
                )
        return row

    async def search(
        self,
        db: AsyncSession,
        *,
        tenant_id: Optional[str],
        user_id: str,
        query: str,
        limit: int = 5,
        entry_types: Optional[list[str]] = None,
    ) -> list[KnowledgeEntry]:
        if not query or not query.strip():
            return []

        # Phase 4: delegate entirely to Azure AI Search when configured.
        # SQL falls through only when Search is disabled or the call
        # itself returns nothing (defensive — keeps results flowing if
        # the index is briefly empty after a recreate).
        kb_search = _get_kb_search_client()
        if kb_search is not None:
            embedding = await _embed_for_query(query)
            search_hits = kb_search.search(
                tenant_id=tenant_id,
                user_id=user_id,
                query=query,
                embedding=embedding,
                limit=limit,
                entry_types=entry_types,
            )
            if search_hits:
                # Hydrate ORM rows from SQL by id so callers get the
                # same KnowledgeEntry shape as the SQL fallback path.
                ids = [
                    h.get("entry_id") for h in search_hits if h.get("entry_id")
                ]
                if ids:
                    rows = (
                        await db.execute(
                            select(KnowledgeEntry).where(
                                KnowledgeEntry.entry_id.in_(ids)
                            )
                        )
                    ).scalars().all()
                    # Preserve Search ordering (relevance score) by
                    # re-sorting via the original id list.
                    by_id = {r.entry_id: r for r in rows}
                    ordered = [
                        by_id[i] for i in ids if i in by_id
                    ]
                    if ordered:
                        return ordered

        # ── SQL fallback (Phase 3 path; also handles the never-indexed
        #    early state and Search outages).
        return await self._search_sql(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            query=query,
            limit=limit,
            entry_types=entry_types,
        )

    async def _search_sql(
        self,
        db: AsyncSession,
        *,
        tenant_id: Optional[str],
        user_id: str,
        query: str,
        limit: int,
        entry_types: Optional[list[str]],
    ) -> list[KnowledgeEntry]:
        """Original Phase 3 keyword-match implementation, kept for
        fallback when Search is unavailable."""
        # Tenant-aware visibility: prefer tenant entries when present;
        # always include the user's own entries.  Mirrors AgentMemory.
        if tenant_id:
            owner_clause = or_(
                KnowledgeEntry.tenant_id == tenant_id,
                KnowledgeEntry.user_id == user_id,
            )
        else:
            owner_clause = KnowledgeEntry.user_id == user_id

        stmt = select(KnowledgeEntry).where(owner_clause)
        if entry_types:
            stmt = stmt.where(KnowledgeEntry.entry_type.in_(entry_types))

        now = datetime.utcnow()
        stmt = stmt.where(
            or_(
                KnowledgeEntry.expires_at.is_(None),
                KnowledgeEntry.expires_at > now,
            )
        )

        tokens = [t.strip() for t in query.split() if t.strip()]
        token_clauses = []
        for tok in tokens[:6]:
            like = f"%{tok.lower()}%"
            token_clauses.append(KnowledgeEntry.title.ilike(like))
            token_clauses.append(KnowledgeEntry.summary.ilike(like))
        if token_clauses:
            stmt = stmt.where(or_(*token_clauses))

        stmt = stmt.order_by(KnowledgeEntry.created_at.desc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
        return list(rows)

    async def get(
        self, db: AsyncSession, entry_id: str
    ) -> Optional[KnowledgeEntry]:
        return await db.get(KnowledgeEntry, entry_id)

    async def expire(self, db: AsyncSession, entry_id: str) -> bool:
        row = await db.get(KnowledgeEntry, entry_id)
        if row is None:
            return False
        row.expires_at = datetime.utcnow()
        await db.commit()
        # Also hard-delete from Search so it stops appearing in queries.
        kb_search = _get_kb_search_client()
        if kb_search is not None:
            try:
                kb_search.delete(entry_id)
            except Exception:
                pass
        return True

    async def expire_stale(
        self, db: AsyncSession, *, tenant_id: Optional[str] = None
    ) -> int:
        """Hard-delete every row whose ``expires_at`` is in the past.

        Returns the row count.  Idempotent — running on a clean DB is a
        no-op.  Best-effort with the Search client: if Search delete
        fails the SQL deletion still wins.
        """
        now = datetime.utcnow()
        stmt = delete(KnowledgeEntry).where(
            KnowledgeEntry.expires_at.is_not(None),
            KnowledgeEntry.expires_at < now,
        )
        if tenant_id is not None:
            stmt = stmt.where(KnowledgeEntry.tenant_id == tenant_id)
        result = await db.execute(stmt)
        await db.commit()
        deleted = int(result.rowcount or 0)

        kb_search = _get_kb_search_client()
        if kb_search is not None:
            try:
                kb_search.delete_stale(now=now)
            except Exception as exc:
                logger.warning("KB search delete_stale failed: %s", exc)

        if deleted:
            logger.info("KB expire_stale removed %d row(s)", deleted)
        return deleted

    async def stats(self, db: AsyncSession) -> dict:
        """Admin-friendly summary of the KB.

        Counts by entry_type, total entries, entries expiring within 7
        days, and the age of the oldest entry.  Used by the
        ``/orchestration/kb/stats`` admin endpoint.
        """
        try:
            total = int(
                (
                    await db.execute(
                        select(func.count(KnowledgeEntry.entry_id))
                    )
                ).scalar_one_or_none() or 0
            )
            by_type_rows = (
                await db.execute(
                    select(
                        KnowledgeEntry.entry_type,
                        func.count(KnowledgeEntry.entry_id),
                    ).group_by(KnowledgeEntry.entry_type)
                )
            ).all()
            entries_by_type = {row[0]: int(row[1]) for row in by_type_rows}

            now = datetime.utcnow()
            soon = now + timedelta(days=7)
            expiring_soon = int(
                (
                    await db.execute(
                        select(func.count(KnowledgeEntry.entry_id)).where(
                            KnowledgeEntry.expires_at.is_not(None),
                            KnowledgeEntry.expires_at >= now,
                            KnowledgeEntry.expires_at <= soon,
                        )
                    )
                ).scalar_one_or_none() or 0
            )

            oldest = (
                await db.execute(
                    select(func.min(KnowledgeEntry.created_at))
                )
            ).scalar_one_or_none()
            oldest_age_days = (
                int((now - oldest).total_seconds() // 86400)
                if oldest else 0
            )

            return {
                "total_entries": total,
                "entries_by_type": entries_by_type,
                "entries_expiring_within_7_days": expiring_soon,
                "oldest_entry_age_days": oldest_age_days,
            }
        except Exception as exc:
            logger.warning("KB stats failed: %s", exc)
            return {
                "total_entries": 0,
                "entries_by_type": {},
                "entries_expiring_within_7_days": 0,
                "oldest_entry_age_days": 0,
                "error": str(exc),
            }


# ── Embedding + search-client helpers ────────────────────────────────────


def _get_kb_search_client():
    """Lazy import to avoid a hard dep on the Azure Search SDK at module
    load.  Returns the singleton or None when unconfigured / failed."""
    try:
        from app.orchestration.knowledge_search import kb_search_client
        return kb_search_client
    except Exception as exc:
        logger.debug("KB search client import failed: %s", exc)
        return None


async def _embed_for_kb(title: str, summary: str) -> Optional[list[float]]:
    """Embed the (title + summary) text for KB ingest.  Never raises."""
    text = (title + "\n\n" + summary).strip()
    return await _embed(text)


async def _embed_for_query(query: str) -> Optional[list[float]]:
    """Embed a search query.  Never raises."""
    return await _embed(query)


async def _embed(text: str) -> Optional[list[float]]:
    if not text:
        return None
    try:
        from app.services.openai_service import openai_service
    except Exception:
        return None
    if openai_service is None:
        return None
    try:
        return await openai_service.get_embedding(text)
    except Exception as exc:  # noqa: BLE001 — get_embedding shouldn't raise
        logger.debug("embedding fetch failed: %s", exc)
        return None


# ── Summariser hook ──────────────────────────────────────────────────────


async def summarise_if_needed(
    text: str, source_worker_id: Optional[str] = None
) -> str:
    """Collapse worker output to a 2-sentence summary if it's over the
    ``SUMMARISE_TRIGGER_CHARS`` threshold.

    Hard rules:
      * Never raise.  Summariser failure → truncate to SUMMARY_MAX_CHARS
        and log a warning.  Ingestion must not be held hostage by the
        LLM.
      * If ``openai_service`` is unavailable, fall through to truncation.
      * Output is hard-capped at SUMMARY_MAX_CHARS regardless of what
        the LLM returns.
    """
    if not text:
        return ""
    if len(text) <= SUMMARISE_TRIGGER_CHARS:
        return text

    try:
        from app.services.openai_service import openai_service
    except Exception:
        openai_service = None  # type: ignore[assignment]

    if openai_service is None:
        logger.warning(
            "knowledge.summariser: openai_service unavailable, "
            "truncating worker=%s text_len=%d",
            source_worker_id,
            len(text),
        )
        return text[:SUMMARY_MAX_CHARS]

    sys_prompt = (
        "You compress worker results into a 2-sentence summary that "
        "the planner LLM can read.  Preserve key facts, numbers, names, "
        "and dates.  No preamble, no markdown — just the summary text."
    )
    user_prompt = (
        f"Source: {source_worker_id or 'unknown'}\n\n"
        f"Original output:\n{text[:8000]}"
    )
    try:
        completion = await openai_service.get_completion(
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model="gpt-4o-mini",
            max_tokens=160,
            temperature=0.1,
        )
    except Exception as exc:  # noqa: BLE001 — never raise from the KB
        logger.warning(
            "knowledge.summariser: LLM call failed (%s) — truncating",
            exc,
        )
        return text[:SUMMARY_MAX_CHARS]

    if not completion or not completion.strip():
        return text[:SUMMARY_MAX_CHARS]
    return completion.strip()[:SUMMARY_MAX_CHARS]


# ── Convenience: serialise the embedding column ─────────────────────────


def encode_embedding(vec: list[float]) -> str:
    """JSON-encode an embedding vector for the Phase-3 TEXT column.

    Phase 4 will move embeddings into Azure AI Search and this helper
    becomes a no-op.  Kept centralised so call sites don't grow ad-hoc
    serialisation."""
    return json.dumps(vec)


def decode_embedding(s: Optional[str]) -> Optional[list[float]]:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


# Module-level singleton — one knowledge store per process.
knowledge_store: KnowledgeStore = SQLKnowledgeStore()
