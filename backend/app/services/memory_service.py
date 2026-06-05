"""
Mela AI - Three-Layer Memory System

Implements a three-layer memory architecture:
- Layer 1: Long-term memory (user preferences, corrections, facts)
- Layer 2: Session memory (per-conversation summaries, 30-day expiry)
- Layer 3: Active context (last N messages, handled in chat flow)

Memory is injected into the system prompt using special tags:
- [LONG_TERM_MEMORY] - User's persistent preferences and facts
- [SESSION_MEMORY] - Current conversation's compressed context

The AI can update memory by including [MEMORY_UPDATE] blocks in responses.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import UserMemory, SessionMemory, MemoryType


logger = logging.getLogger(__name__)

# Configuration
LONG_TERM_MEMORY_LIMIT = 20  # Max long-term memories to inject
SESSION_MEMORY_EXPIRY_DAYS = 30
ACTIVE_CONTEXT_MESSAGES = 20  # Last N messages for active context

# Redis TTLs for memory caching
_LTM_CACHE_TTL = 600          # 10 minutes – long-term memories
_SMEM_CACHE_TTL = 30 * 86400  # 30 days   – session memory (matches DB expiry)


def _ltm_key(user_id: str, profile_scope: str, tenant_id: Optional[str]) -> str:
    from app.core.redis_client import key as rkey
    return rkey("ltmem", user_id, profile_scope, tenant_id or "none")


def _smem_key(conversation_id: str) -> str:
    from app.core.redis_client import key as rkey
    return rkey("smem", conversation_id)


class MemoryService:
    """Service for managing the three-layer memory system."""

    # ─────────────────────────────────────────────────────────────────────
    # Layer 1: Long-term Memory
    # ─────────────────────────────────────────────────────────────────────

    async def get_long_term_memories(
        self,
        db: AsyncSession,
        user_id: str,
        profile_mode: str = "personal",
        tenant_id: Optional[str] = None,
        limit: int = LONG_TERM_MEMORY_LIMIT,
        current_query: Optional[str] = None,
    ) -> list[UserMemory]:
        """Retrieve active long-term memories for a user.

        Tries Redis cache first (TTL=600s).  On miss, queries DB and populates
        cache.  Writes / deletes must call ``_invalidate_ltm_cache`` to keep
        the cache coherent.
        """
        from app.core.redis_client import get_redis

        cache_key = _ltm_key(user_id, profile_mode, tenant_id)
        r = await get_redis()
        if r is not None:
            try:
                cached_raw = await r.get(cache_key)
                if cached_raw:
                    cached_list = json.loads(cached_raw)
                    # Reconstruct lightweight dicts as UserMemory-like objects.
                    # We return plain UserMemory objects from DB when available;
                    # cached path returns ORM objects re-fetched by ID to keep
                    # SQLAlchemy happy for any downstream relationship access.
                    # For injection-only callers (build_memory_context) the
                    # content fields are sufficient, so we materialise cheap
                    # namespaces instead of issuing N DB queries.
                    return [_DictMemory(m) for m in cached_list]  # type: ignore[return-value]
            except Exception as exc:
                logger.debug("ltm cache read error (%s); falling back to DB", exc)

        # ── DB query ──────────────────────────────────────────────────────────
        if tenant_id:
            profile_filter = and_(
                UserMemory.profile_scope == profile_mode,
                UserMemory.tenant_id == tenant_id,
            )
        else:
            profile_filter = and_(
                UserMemory.profile_scope == profile_mode,
                UserMemory.tenant_id.is_(None),
            )

        scope_filter = or_(
            UserMemory.profile_scope == "global",
            profile_filter,
        )

        stmt = (
            select(UserMemory)
            .where(
                UserMemory.user_id == user_id,
                UserMemory.is_active.is_(True),
                scope_filter,
            )
            .order_by(
                UserMemory.relevance_score.desc(),
                UserMemory.usage_count.desc(),
                UserMemory.updated_at.desc(),
            )
            .limit(limit)
        )

        result = await db.execute(stmt)
        memories = list(result.scalars().all())

        # Populate cache with serialisable representation.
        if r is not None and memories:
            try:
                serialisable = [
                    {
                        "id": str(m.id),
                        "user_id": str(m.user_id),
                        "content": m.content,
                        "memory_type": m.memory_type.value if hasattr(m.memory_type, "value") else str(m.memory_type),
                        "category": m.category,
                        "profile_scope": m.profile_scope,
                        "tenant_id": str(m.tenant_id) if m.tenant_id else None,
                        "relevance_score": m.relevance_score,
                        "usage_count": m.usage_count,
                        "is_active": bool(m.is_active),
                        "source_conversation_id": str(m.source_conversation_id) if m.source_conversation_id else None,
                        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                    }
                    for m in memories
                ]
                await r.set(cache_key, json.dumps(serialisable), ex=_LTM_CACHE_TTL)
            except Exception as exc:
                logger.debug("ltm cache write error (%s)", exc)

        if current_query:
            memories = _rank_memories_by_query(memories, current_query)

        return memories

    async def add_long_term_memory(
        self,
        db: AsyncSession,
        user_id: str,
        content: str,
        memory_type: MemoryType = MemoryType.FACT,
        category: Optional[str] = None,
        source_conversation_id: Optional[str] = None,
        profile_scope: str = "global",
        tenant_id: Optional[str] = None,
        relevance_score: int = 5,
    ) -> UserMemory:
        """Add a new long-term memory for a user (with dedup)."""
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("Memory content must not be empty")

        # Dedup: check for existing active memory with identical content
        existing_stmt = select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.content == clean_content,
            UserMemory.is_active.is_(True),
        ).limit(1)
        existing_result = await db.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()
        if existing:
            # Bump usage count instead of creating duplicate
            existing.usage_count += 1
            existing.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(existing)
            logger.info("Dedup: incremented usage for existing memory %s", existing.id)
            await self._invalidate_ltm_cache(user_id, profile_scope, tenant_id)
            return existing

        memory = UserMemory(
            user_id=user_id,
            content=clean_content,
            memory_type=memory_type,
            category=category,
            source_conversation_id=source_conversation_id,
            profile_scope=profile_scope,
            tenant_id=tenant_id,
            relevance_score=relevance_score,
            usage_count=1,  # First use counts
        )
        db.add(memory)
        await db.commit()
        await db.refresh(memory)
        logger.info("Added long-term memory for user %s: %s", user_id, clean_content[:50])
        await self._invalidate_ltm_cache(user_id, profile_scope, tenant_id)
        return memory

    async def update_memory_usage(
        self, db: AsyncSession, memory_id: str
    ) -> None:
        """Increment usage count for a memory."""
        stmt = select(UserMemory).where(UserMemory.id == memory_id)
        result = await db.execute(stmt)
        memory = result.scalar_one_or_none()
        if memory:
            memory.usage_count += 1
            memory.updated_at = datetime.utcnow()
            await db.commit()

    async def deactivate_memory(
        self, db: AsyncSession, memory_id: str, user_id: str
    ) -> bool:
        """Deactivate a memory (soft delete)."""
        stmt = select(UserMemory).where(
            UserMemory.id == memory_id,
            UserMemory.user_id == user_id,
        )
        result = await db.execute(stmt)
        memory = result.scalar_one_or_none()
        if memory:
            memory.is_active = False
            memory.updated_at = datetime.utcnow()
            await db.commit()
            await self._invalidate_ltm_cache(user_id, memory.profile_scope, memory.tenant_id)
            return True
        return False

    async def delete_memory(
        self, db: AsyncSession, memory_id: str, user_id: str
    ) -> bool:
        """Hard delete a memory."""
        # Fetch first so we can invalidate the right cache key.
        stmt_fetch = select(UserMemory).where(
            UserMemory.id == memory_id,
            UserMemory.user_id == user_id,
        )
        fetch_result = await db.execute(stmt_fetch)
        memory = fetch_result.scalar_one_or_none()

        stmt = delete(UserMemory).where(
            UserMemory.id == memory_id,
            UserMemory.user_id == user_id,
        )
        result = await db.execute(stmt)
        await db.commit()
        if result.rowcount > 0 and memory:
            await self._invalidate_ltm_cache(user_id, memory.profile_scope, memory.tenant_id)
        return result.rowcount > 0

    # ─────────────────────────────────────────────────────────────────────
    # Layer 2: Session Memory
    # ─────────────────────────────────────────────────────────────────────

    async def get_session_memory(
        self,
        db: AsyncSession,
        conversation_id: str,
    ) -> Optional[SessionMemory]:
        """Retrieve session memory for a conversation.

        Checks Redis cache first (TTL=30 days, sliding).  Falls back to DB on
        cache miss and populates cache on successful DB read.
        """
        from app.core.redis_client import get_redis

        r = await get_redis()
        if r is not None:
            try:
                raw = await r.get(_smem_key(conversation_id))
                if raw:
                    data = json.loads(raw)
                    # Slide TTL on read.
                    await r.expire(_smem_key(conversation_id), _SMEM_CACHE_TTL)
                    return _DictSessionMemory(data)  # type: ignore[return-value]
            except Exception as exc:
                logger.debug("smem cache read error (%s); falling back to DB", exc)

        stmt = select(SessionMemory).where(
            SessionMemory.conversation_id == conversation_id,
            SessionMemory.expires_at > datetime.utcnow(),
        )
        result = await db.execute(stmt)
        mem = result.scalar_one_or_none()

        if mem is not None and r is not None:
            try:
                await r.set(
                    _smem_key(conversation_id),
                    json.dumps(_session_memory_to_dict(mem)),
                    ex=_SMEM_CACHE_TTL,
                )
            except Exception as exc:
                logger.debug("smem cache write error (%s)", exc)

        return mem

    async def update_session_memory(
        self,
        db: AsyncSession,
        conversation_id: str,
        user_id: str,
        summary: str,
        key_facts: Optional[list[str]] = None,
        goals: Optional[list[str]] = None,
        entities: Optional[list[str]] = None,
        last_message_id: Optional[str] = None,
        message_count: int = 0,
        profile_mode: str = "personal",
        tenant_id: Optional[str] = None,
    ) -> SessionMemory:
        """Create or update session memory for a conversation."""
        expires_at = datetime.utcnow() + timedelta(days=SESSION_MEMORY_EXPIRY_DAYS)

        # Check for existing session memory
        stmt = select(SessionMemory).where(
            SessionMemory.conversation_id == conversation_id
        )
        result = await db.execute(stmt)
        session_mem = result.scalar_one_or_none()

        # Estimate token count (rough approximation: 4 chars = 1 token)
        token_count = len(summary) // 4

        if session_mem:
            # Update existing
            session_mem.summary = summary
            session_mem.key_facts = json.dumps(key_facts) if key_facts else None
            session_mem.goals = json.dumps(goals) if goals else None
            session_mem.entities = json.dumps(entities) if entities else None
            session_mem.token_count = token_count
            session_mem.last_message_id = last_message_id
            session_mem.message_count = message_count
            session_mem.updated_at = datetime.utcnow()
            session_mem.expires_at = expires_at
        else:
            # Create new
            session_mem = SessionMemory(
                conversation_id=conversation_id,
                user_id=user_id,
                summary=summary,
                key_facts=json.dumps(key_facts) if key_facts else None,
                goals=json.dumps(goals) if goals else None,
                entities=json.dumps(entities) if entities else None,
                token_count=token_count,
                last_message_id=last_message_id,
                message_count=message_count,
                profile_mode=profile_mode,
                tenant_id=tenant_id,
                expires_at=expires_at,
            )
            db.add(session_mem)

        await db.commit()
        await db.refresh(session_mem)
        logger.info(
            "Updated session memory for conversation %s (%d tokens)",
            conversation_id,
            token_count,
        )
        # Invalidate cache so the next read picks up the fresh row.
        await self._invalidate_smem_cache(conversation_id)
        return session_mem

    async def delete_session_memory(
        self, db: AsyncSession, conversation_id: str
    ) -> bool:
        """Delete session memory for a conversation."""
        stmt = delete(SessionMemory).where(
            SessionMemory.conversation_id == conversation_id
        )
        result = await db.execute(stmt)
        await db.commit()
        await self._invalidate_smem_cache(conversation_id)
        return result.rowcount > 0

    async def cleanup_expired_sessions(self, db: AsyncSession) -> int:
        """Remove expired session memories."""
        stmt = delete(SessionMemory).where(
            SessionMemory.expires_at < datetime.utcnow()
        )
        result = await db.execute(stmt)
        await db.commit()
        count = result.rowcount
        if count > 0:
            logger.info("Cleaned up %d expired session memories", count)
        return count

    # ─────────────────────────────────────────────────────────────────────
    # Memory Injection & Formatting
    # ─────────────────────────────────────────────────────────────────────

    def format_long_term_memory(
        self, memories: list[UserMemory]
    ) -> str:
        """Format long-term memories for injection into system prompt."""
        if not memories:
            return ""

        # Bucket by type so corrections + style come first (highest priority)
        _priority_order = ["correction", "style", "preference", "fact", "context"]
        buckets: dict[str, list] = {t: [] for t in _priority_order}
        for mem in memories:
            t = (mem.memory_type.value if hasattr(mem.memory_type, "value") else str(mem.memory_type)).lower()
            buckets.setdefault(t, []).append(mem)

        lines = ["[LONG_TERM_MEMORY]"]
        for bucket_type in _priority_order:
            for mem in buckets.get(bucket_type, []):
                type_label = bucket_type.upper()
                safe_content = self._sanitize_memory_content(mem.content)
                lines.append(f"- [{type_label}] {safe_content}")
        # Any unknown types
        known = set(_priority_order)
        for t, mems in buckets.items():
            if t not in known:
                for mem in mems:
                    safe_content = self._sanitize_memory_content(mem.content)
                    lines.append(f"- [{t.upper()}] {safe_content}")
        lines.append("[/LONG_TERM_MEMORY]")

        return "\n".join(lines)

    @staticmethod
    def _sanitize_memory_content(content: str) -> str:
        """Strip prompt-injection markers and control sequences from memory content.

        Prevents stored memories from breaking the system prompt structure
        or injecting new instructions.
        """
        import re as _re
        # Remove any attempts to close/open system prompt sections
        dangerous_patterns = [
            r"\[/?SYSTEM\]",
            r"\[/?INST\]",
            r"\[/?LONG_TERM_MEMORY\]",
            r"\[/?SESSION_MEMORY\]",
            r"\[/?MEMORY_UPDATE\]",
            r"</?system>",
            r"</?instructions?>",
        ]
        cleaned = content
        for pattern in dangerous_patterns:
            cleaned = _re.sub(pattern, "", cleaned, flags=_re.IGNORECASE)
        return cleaned.strip()

    def format_session_memory(
        self, session_mem: Optional[SessionMemory]
    ) -> str:
        """Format session memory for injection into system prompt."""
        if not session_mem:
            return ""

        lines = ["[SESSION_MEMORY]"]
        lines.append(f"Summary: {self._sanitize_memory_content(session_mem.summary)}")

        # Parse and include key facts
        if session_mem.key_facts:
            try:
                facts = json.loads(session_mem.key_facts)
                if facts:
                    lines.append("Key facts:")
                    for fact in facts:
                        lines.append(f"  - {fact}")
            except json.JSONDecodeError:
                pass

        # Parse and include goals
        if session_mem.goals:
            try:
                goals = json.loads(session_mem.goals)
                if goals:
                    lines.append("Goals:")
                    for goal in goals:
                        lines.append(f"  - {goal}")
            except json.JSONDecodeError:
                pass

        # Parse and include entities (people, projects, tools, etc.)
        if session_mem.entities:
            try:
                entities = json.loads(session_mem.entities)
                if entities:
                    lines.append("Key entities mentioned:")
                    for entity in entities:
                        lines.append(f"  - {entity}")
            except json.JSONDecodeError:
                pass

        lines.append("[/SESSION_MEMORY]")
        return "\n".join(lines)

    async def build_memory_context(
        self,
        db: AsyncSession,
        user_id: str,
        conversation_id: Optional[str] = None,
        profile_mode: str = "personal",
        tenant_id: Optional[str] = None,
        current_query: Optional[str] = None,
    ) -> str:
        """Build complete memory context for injection into system prompt.

        Returns formatted string containing both long-term and session memory.
        ``current_query`` re-ranks long-term memories by relevance to the
        current user message so the most applicable ones appear first.
        """
        parts = []

        # Layer 1: Long-term memory
        long_term = await self.get_long_term_memories(
            db, user_id, profile_mode, tenant_id, current_query=current_query
        )
        if long_term:
            parts.append(self.format_long_term_memory(long_term))

        # Layer 2: Session memory
        if conversation_id:
            session_mem = await self.get_session_memory(db, conversation_id)
            if session_mem:
                parts.append(self.format_session_memory(session_mem))

        return "\n\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────
    # Memory Update Parsing
    # ─────────────────────────────────────────────────────────────────────

    def parse_memory_updates(self, assistant_response: str) -> list[dict]:
        """Parse [MEMORY_UPDATE] blocks from AI response.

        Expected format:
        [MEMORY_UPDATE]
        action: add|update|remove
        type: preference|correction|fact|context|style
        content: The memory content
        category: optional category
        [/MEMORY_UPDATE]
        """
        updates = []
        pattern = r"\[MEMORY_UPDATE\](.*?)\[/MEMORY_UPDATE\]"
        matches = re.findall(pattern, assistant_response, re.DOTALL)

        for match in matches:
            update = {}
            lines = match.strip().split("\n")
            for line in lines:
                if ":" in line:
                    key, value = line.split(":", 1)
                    update[key.strip().lower()] = value.strip()
            if update.get("action") and update.get("content"):
                updates.append(update)

        return updates

    async def process_memory_updates(
        self,
        db: AsyncSession,
        user_id: str,
        assistant_response: str,
        source_conversation_id: Optional[str] = None,
        profile_scope: str = "global",
        tenant_id: Optional[str] = None,
    ) -> list[UserMemory]:
        """Process [MEMORY_UPDATE] blocks from AI response.

        Extracts memory updates and applies them to the user's long-term memory.
        """
        updates = self.parse_memory_updates(assistant_response)
        added_memories = []

        for update in updates:
            action = update.get("action", "add").lower()
            content = update.get("content", "")
            mem_type_str = update.get("type", "fact").lower()
            category = update.get("category")

            # Map type string to enum
            type_map = {
                "preference": MemoryType.PREFERENCE,
                "correction": MemoryType.CORRECTION,
                "fact": MemoryType.FACT,
                "context": MemoryType.CONTEXT,
                "style": MemoryType.STYLE,
            }
            memory_type = type_map.get(mem_type_str, MemoryType.FACT)

            if action == "add" and content:
                memory = await self.add_long_term_memory(
                    db=db,
                    user_id=user_id,
                    content=content,
                    memory_type=memory_type,
                    category=category,
                    source_conversation_id=source_conversation_id,
                    profile_scope=profile_scope,
                    tenant_id=tenant_id,
                )
                added_memories.append(memory)
                logger.info("Added memory from AI update: %s", content[:50])

            elif action == "update" and content:
                # Find an existing memory whose content is similar and update it
                existing = await self.get_long_term_memories(
                    db, user_id, profile_scope, tenant_id, limit=100,
                )
                target = update.get("target", "").lower()
                matched = None
                for mem in existing:
                    if target and target in mem.content.lower():
                        matched = mem
                        break
                    # Fallback: match by category + type
                    if (
                        mem.memory_type == memory_type
                        and category
                        and mem.category == category
                    ):
                        matched = mem
                        break
                if matched:
                    matched.content = content.strip()
                    matched.memory_type = memory_type
                    matched.updated_at = datetime.utcnow()
                    await db.commit()
                    await db.refresh(matched)
                    logger.info("Updated memory %s: %s", matched.id, content[:50])
                else:
                    # No match found — treat as add
                    memory = await self.add_long_term_memory(
                        db=db,
                        user_id=user_id,
                        content=content,
                        memory_type=memory_type,
                        category=category,
                        source_conversation_id=source_conversation_id,
                        profile_scope=profile_scope,
                        tenant_id=tenant_id,
                    )
                    added_memories.append(memory)
                    logger.info("No match for update — added new memory: %s", content[:50])

            elif action == "remove":
                # Find and deactivate matching memory
                existing = await self.get_long_term_memories(
                    db, user_id, profile_scope, tenant_id, limit=100,
                )
                target = (content or update.get("target", "")).lower()
                for mem in existing:
                    if target and target in mem.content.lower():
                        mem.is_active = False
                        mem.updated_at = datetime.utcnow()
                        await db.commit()
                        logger.info("Deactivated memory %s via AI remove", mem.id)
                        break

        return added_memories

    def strip_memory_blocks(self, response: str) -> str:
        """Remove [MEMORY_UPDATE] blocks from response before showing user."""
        pattern = r"\[MEMORY_UPDATE\].*?\[/MEMORY_UPDATE\]\s*"
        return re.sub(pattern, "", response, flags=re.DOTALL).strip()

    # ─────────────────────────────────────────────────────────────────────
    # Internal cache helpers
    # ─────────────────────────────────────────────────────────────────────

    async def _invalidate_ltm_cache(
        self, user_id: str, profile_scope: str, tenant_id: Optional[str]
    ) -> None:
        from app.core.redis_client import get_redis

        r = await get_redis()
        if r is None:
            return
        try:
            # Cache keys are built with the *query* profile_mode ("personal" /
            # "work"), not the stored profile_scope ("global" / "personal" /
            # "work").  A global-scope memory appears in every profile query, so
            # all profile-mode variants must be invalidated.
            profile_modes = ["personal", "work", "global"]
            tenant_variants = [tenant_id, None] if tenant_id else [None]
            keys = [
                _ltm_key(user_id, mode, t)
                for mode in profile_modes
                for t in tenant_variants
            ]
            # Also invalidate the stored profile_scope key itself (covers
            # the case where scope == "personal" and mode == "personal").
            keys.append(_ltm_key(user_id, profile_scope, tenant_id))
            await r.delete(*dict.fromkeys(keys))  # deduplicate
        except Exception as exc:
            logger.debug("ltm cache invalidate error (%s)", exc)

    async def _invalidate_smem_cache(self, conversation_id: str) -> None:
        from app.core.redis_client import get_redis

        r = await get_redis()
        if r is None:
            return
        try:
            await r.delete(_smem_key(conversation_id))
        except Exception as exc:
            logger.debug("smem cache invalidate error (%s)", exc)


# ── Lightweight objects for cache-hit paths ────────────────────────────────────


class _DictMemory:
    """Mirrors the attributes of UserMemory used by injection and format helpers."""

    __slots__ = (
        "id", "user_id", "content", "memory_type", "category",
        "profile_scope", "tenant_id", "relevance_score", "usage_count",
        "is_active", "source_conversation_id", "updated_at",
    )

    def __init__(self, d: dict) -> None:
        self.id = d.get("id")
        self.user_id = d.get("user_id")
        self.content = d.get("content", "")
        # Re-wrap as a simple string value so callers that do `.value` still work.
        _mt = d.get("memory_type", "fact")
        self.memory_type = _StrEnum(_mt)
        self.category = d.get("category")
        self.profile_scope = d.get("profile_scope", "global")
        self.tenant_id = d.get("tenant_id")
        self.relevance_score = d.get("relevance_score", 5)
        self.usage_count = d.get("usage_count", 0)
        self.is_active = d.get("is_active", True)
        self.source_conversation_id = d.get("source_conversation_id")
        _upd = d.get("updated_at")
        self.updated_at = datetime.fromisoformat(_upd) if _upd else None


class _DictSessionMemory:
    """Mirrors the attributes of SessionMemory used by format_session_memory."""

    __slots__ = (
        "conversation_id", "user_id", "summary", "key_facts", "goals",
        "entities", "token_count", "last_message_id", "message_count",
        "profile_mode", "tenant_id", "expires_at", "updated_at",
    )

    def __init__(self, d: dict) -> None:
        self.conversation_id = d.get("conversation_id")
        self.user_id = d.get("user_id")
        self.summary = d.get("summary", "")
        self.key_facts = d.get("key_facts")
        self.goals = d.get("goals")
        self.entities = d.get("entities")
        self.token_count = d.get("token_count", 0)
        self.last_message_id = d.get("last_message_id")
        self.message_count = d.get("message_count", 0)
        self.profile_mode = d.get("profile_mode", "personal")
        self.tenant_id = d.get("tenant_id")
        _exp = d.get("expires_at")
        self.expires_at = datetime.fromisoformat(_exp) if _exp else None
        _upd = d.get("updated_at")
        self.updated_at = datetime.fromisoformat(_upd) if _upd else None


class _StrEnum:
    """Minimal enum-like wrapper so `.value` works on cached string values."""
    __slots__ = ("value",)

    def __init__(self, v: str) -> None:
        self.value = v

    def __str__(self) -> str:
        return self.value

    def __eq__(self, other) -> bool:  # type: ignore[override]
        if isinstance(other, _StrEnum):
            return self.value == other.value
        return self.value == other


def _session_memory_to_dict(mem) -> dict:
    def _iso(v):
        return v.isoformat() if v and hasattr(v, "isoformat") else v

    return {
        "conversation_id": str(mem.conversation_id),
        "user_id": str(mem.user_id),
        "summary": mem.summary or "",
        "key_facts": mem.key_facts,
        "goals": mem.goals,
        "entities": mem.entities,
        "token_count": mem.token_count or 0,
        "last_message_id": str(mem.last_message_id) if mem.last_message_id else None,
        "message_count": mem.message_count or 0,
        "profile_mode": mem.profile_mode or "personal",
        "tenant_id": str(mem.tenant_id) if mem.tenant_id else None,
        "expires_at": _iso(mem.expires_at),
        "updated_at": _iso(mem.updated_at),
    }


def _rank_memories_by_query(memories: list, current_query: str) -> list:
    """Re-rank memories so the most relevant to the current query come first.

    Strategy (no embedding needed):
    1. Corrections always rank first — they override previous knowledge.
    2. Memories whose content keywords overlap with the query get a boost.
    3. Ties broken by original order (relevance_score * usage_count already
       applied by the DB ORDER BY before this function is called).

    Returns the same list reordered (no items dropped — all memories are
    preserved so corrections and style rules always reach the model).
    """
    import re as _re

    query_words = set(
        w.lower() for w in _re.split(r"\W+", current_query) if len(w) > 3
    )

    def _score(mem) -> tuple:
        mem_type = (
            mem.memory_type.value if hasattr(mem.memory_type, "value") else str(mem.memory_type)
        ).lower()
        # Corrections always float to top
        is_correction = 1 if mem_type == "correction" else 0
        is_style = 1 if mem_type == "style" else 0
        # Keyword overlap with current query
        mem_words = set(
            w.lower() for w in _re.split(r"\W+", mem.content or "") if len(w) > 3
        )
        overlap = len(query_words & mem_words)
        return (is_correction, is_style, overlap)

    return sorted(memories, key=_score, reverse=True)


# Singleton instance
memory_service = MemoryService()
