"""
Mela AI - System Instructions Service

Layered instruction system. Instructions are loaded in scope priority order:
  global (10) → org (20) → team (30) → user (100)

Within each scope, lower `priority` value = applied first (higher precedence).
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

# ── Built-in global instructions seeded on first use ─────────────────────────

_BUILTIN_INSTRUCTIONS = [
    {
        "name": "Core identity",
        "content": (
            "You are Mela AI, an intelligent enterprise assistant built by Armely. "
            "You are helpful, accurate, professional, and concise. "
            "Always ground answers in provided documents and knowledge sources when available. "
            "If you do not know something, say so clearly rather than guessing."
        ),
        "scope": "global",
        "priority": 1,
        "is_builtin": True,
    },
    {
        "name": "Citation and grounding policy",
        "content": (
            "When you reference information from documents, SharePoint, or knowledge sources, "
            "always cite your sources using [1], [2], etc. markers. "
            "Do not fabricate facts, statistics, or document content. "
            "If asked about something not in the provided context, say clearly: "
            '\"I don\'t have information about that in the available knowledge base.\"'
        ),
        "scope": "global",
        "priority": 2,
        "is_builtin": True,
    },
    {
        "name": "Response formatting",
        "content": (
            "Format responses clearly using markdown. "
            "Use headers, bullet points, and numbered lists where they improve readability. "
            "Keep responses concise unless detail is specifically requested. "
            "For code, always use code blocks with the appropriate language identifier."
        ),
        "scope": "global",
        "priority": 3,
        "is_builtin": True,
    },
]


class InstructionService:
    """Load and compose layered system instructions."""

    async def seed_builtins(self, db) -> None:
        """Insert built-in global instructions if the table is empty."""
        from app.models.models import SystemInstruction
        try:
            result = await db.execute(
                select(SystemInstruction).where(SystemInstruction.scope == "global").limit(1)
            )
            if result.scalar_one_or_none() is not None:
                return
            for instr in _BUILTIN_INSTRUCTIONS:
                db.add(SystemInstruction(
                    id=str(uuid.uuid4()),
                    name=instr["name"],
                    content=instr["content"],
                    scope=instr["scope"],
                    priority=instr["priority"],
                    is_enabled=True,
                    created_by="system",
                ))
            await db.commit()
            logger.info("Seeded %d built-in global instructions", len(_BUILTIN_INSTRUCTIONS))
        except Exception as e:
            logger.warning("Failed to seed built-in instructions: %s", e)

    async def get_instructions_for_user(
        self,
        db,
        user_id: str,
        tenant_id: Optional[str] = None,
    ) -> list[dict]:
        """Return enabled instructions applicable to this user, ordered by scope+priority."""
        from app.models.models import SystemInstruction
        from sqlalchemy import or_, and_

        try:
            # Load: global scope + (org scope matching tenant) + user's personal instructions
            result = await db.execute(
                select(SystemInstruction)
                .where(SystemInstruction.is_enabled == True)  # noqa: E712
                .where(
                    or_(
                        SystemInstruction.scope == "global",
                        and_(
                            SystemInstruction.scope == "org",
                            SystemInstruction.tenant_id == tenant_id,
                        ) if tenant_id else False,
                        and_(
                            SystemInstruction.scope == "user",
                            SystemInstruction.user_id == user_id,
                        ),
                    )
                )
                .order_by(SystemInstruction.scope, SystemInstruction.priority)
            )
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "content": r.content,
                    "scope": r.scope,
                    "priority": r.priority,
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("Failed to load instructions: %s", e)
            return []

    def compose_system_prompt(self, instructions: list[dict], base_prompt: str = "") -> str:
        """Combine instruction blocks into a single system prompt string."""
        parts = []
        if instructions:
            for instr in instructions:
                parts.append(instr["content"])
        if base_prompt:
            parts.append(base_prompt)
        return "\n\n".join(p.strip() for p in parts if p.strip())

    async def list_instructions(
        self,
        db,
        user_id: str,
        scope: Optional[str] = None,
        admin: bool = False,
    ) -> list:
        """List instructions visible to this user (with admin override)."""
        from app.models.models import SystemInstruction
        from sqlalchemy import or_

        try:
            q = select(SystemInstruction).order_by(SystemInstruction.scope, SystemInstruction.priority)
            if not admin:
                q = q.where(
                    or_(
                        SystemInstruction.scope == "global",
                        SystemInstruction.user_id == user_id,
                    )
                )
            if scope:
                q = q.where(SystemInstruction.scope == scope)
            result = await db.execute(q)
            return result.scalars().all()
        except Exception as e:
            logger.warning("Failed to list instructions: %s", e)
            return []

    async def create_instruction(
        self,
        db,
        user_id: str,
        name: str,
        content: str,
        scope: str = "user",
        priority: int = 100,
        tenant_id: Optional[str] = None,
    ):
        """Create a new instruction. Non-admins can only create user-scope."""
        from app.models.models import SystemInstruction
        instr = SystemInstruction(
            id=str(uuid.uuid4()),
            name=name,
            content=content,
            scope=scope,
            priority=priority,
            is_enabled=True,
            created_by=user_id,
            user_id=user_id if scope == "user" else None,
            tenant_id=tenant_id if scope in ("org", "team") else None,
        )
        db.add(instr)
        await db.commit()
        await db.refresh(instr)
        return instr

    async def update_instruction(self, db, instruction_id: str, user_id: str, **kwargs):
        from app.models.models import SystemInstruction
        result = await db.execute(
            select(SystemInstruction).where(SystemInstruction.id == instruction_id)
        )
        instr = result.scalar_one_or_none()
        if not instr:
            return None
        for k, v in kwargs.items():
            if hasattr(instr, k) and v is not None:
                setattr(instr, k, v)
        instr.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(instr)
        return instr

    async def delete_instruction(self, db, instruction_id: str, user_id: str, admin: bool = False):
        from app.models.models import SystemInstruction
        result = await db.execute(
            select(SystemInstruction).where(SystemInstruction.id == instruction_id)
        )
        instr = result.scalar_one_or_none()
        if not instr:
            return False
        if not admin and instr.user_id != user_id:
            return False
        if instr.scope == "global" and not admin:
            return False  # global instructions protected
        await db.delete(instr)
        await db.commit()
        return True


instruction_service = InstructionService()
