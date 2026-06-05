"""
Mela AI - Skills Service

Skills are instruction blocks activated when the user's message matches
certain keywords or categories. They extend the base instructions with
domain-specific guidance (e.g., writing tone, data analysis approach).
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

# ── Built-in skills seeded on first use ───────────────────────────────────────

_BUILTIN_SKILLS = [
    {
        "name": "Technical Writing",
        "description": "Helps produce clear, precise technical documentation and explanations.",
        "category": "writing",
        "trigger_keywords": ["document", "write up", "technical spec", "readme", "documentation", "explain how"],
        "instruction_block": (
            "When writing technical content: use precise language, define acronyms on first use, "
            "prefer active voice, include code examples where helpful, and structure content with "
            "clear headings. Aim for clarity over brevity when precision matters."
        ),
        "rank": 10,
    },
    {
        "name": "Data Analysis",
        "description": "Statistical analysis, data interpretation, and insight generation.",
        "category": "data",
        "trigger_keywords": ["analyze", "analysis", "data", "statistics", "trends", "insights", "numbers", "metrics", "kpi"],
        "instruction_block": (
            "When analyzing data: start with summary statistics, identify patterns and outliers, "
            "explain what the numbers mean in business terms, suggest visualizations where helpful, "
            "and quantify uncertainty where relevant. Use precise numerical language."
        ),
        "rank": 20,
    },
    {
        "name": "Code Review & Development",
        "description": "Software development assistance, code review, and debugging.",
        "category": "coding",
        "trigger_keywords": ["code", "function", "bug", "debug", "implement", "class", "api", "programming", "script", "error", "exception"],
        "instruction_block": (
            "When assisting with code: provide working, tested examples. "
            "Follow language-specific best practices and conventions. "
            "Explain the approach before the code. Point out security concerns, edge cases, "
            "and performance considerations. Prefer readable code over clever code."
        ),
        "rank": 30,
    },
    {
        "name": "Executive Summary",
        "description": "Concise executive-level summaries and briefs.",
        "category": "writing",
        "trigger_keywords": ["executive summary", "exec brief", "tldr", "summarize", "summary", "key points", "highlight"],
        "instruction_block": (
            "For executive summaries: lead with the conclusion and key recommendation. "
            "Use a maximum of 3-5 bullet points for key findings. "
            "Quantify impact where possible. Avoid jargon. "
            "Close with a clear recommended action or decision needed."
        ),
        "rank": 40,
    },
    {
        "name": "SQL & Database",
        "description": "SQL query writing, optimization, and database design assistance.",
        "category": "data",
        "trigger_keywords": ["sql", "query", "database", "table", "join", "select", "insert", "index", "schema"],
        "instruction_block": (
            "When writing SQL: always include comments for complex queries. "
            "Prefer CTEs over nested subqueries for readability. "
            "Consider index usage and query performance. "
            "Validate column and table names match the provided schema. "
            "Include example output format where helpful."
        ),
        "rank": 50,
    },
    {
        "name": "Research & Synthesis",
        "description": "Research compilation, synthesis from multiple sources, and structured reports.",
        "category": "research",
        "trigger_keywords": ["research", "find", "compare", "what is", "how does", "pros and cons", "options", "alternatives"],
        "instruction_block": (
            "When researching or comparing options: structure findings clearly, "
            "cite sources when available, distinguish between facts and opinions, "
            "provide a comparison table or structured breakdown when comparing multiple items, "
            "and conclude with a clear recommendation or summary judgment."
        ),
        "rank": 60,
    },
    {
        "name": "Spreadsheet & Excel",
        "description": "Spreadsheet formulas, data modeling, and Excel/Sheets assistance.",
        "category": "spreadsheet",
        "trigger_keywords": ["excel", "spreadsheet", "formula", "vlookup", "pivot", "chart", "google sheets", "cell", "worksheet"],
        "instruction_block": (
            "When helping with spreadsheets: provide exact formula syntax with examples. "
            "Explain what each function does in plain English. "
            "Suggest alternative approaches when simpler formulas exist. "
            "Include cell reference examples (e.g., =VLOOKUP(A2, B:C, 2, FALSE))."
        ),
        "rank": 70,
    },
    {
        "name": "Compliance & Policy",
        "description": "Policy-aware responses with appropriate caveats and recommendations.",
        "category": "compliance",
        "trigger_keywords": ["policy", "compliance", "regulation", "legal", "gdpr", "hipaa", "risk", "audit", "procedure"],
        "instruction_block": (
            "When addressing compliance or policy matters: "
            "note that responses are informational and not legal or regulatory advice. "
            "Reference specific policy sections when available. "
            "Highlight any ambiguity or areas requiring expert review. "
            "Recommend escalation to appropriate teams for binding decisions."
        ),
        "rank": 80,
    },
    {
        "name": "Employee Onboarding",
        "description": "Automates new-hire onboarding: welcome email, orientation meeting, and Planner tasks.",
        "category": "hr",
        "trigger_keywords": [
            "onboard", "onboarding", "new employee", "new hire", "new staff",
            "welcome", "orientation", "first day", "join the team", "new joiner",
        ],
        "instruction_block": (
            "When helping with employee onboarding: "
            "use the `onboard_user` tool to automate the full workflow (welcome email, "
            "orientation meeting, Planner tasks). "
            "Ask for the new employee's full name, email address, department, and manager's email "
            "if not already provided. "
            "Confirm which steps the user wants to run before executing. "
            "After the tool completes, summarise which steps succeeded and which failed, "
            "and suggest manual follow-up actions for any failures."
        ),
        "rank": 90,
    },
]


class SkillService:
    """Load, match, and apply skills to conversations."""

    async def seed_builtins(self, db) -> None:
        """Insert built-in skills if the table is empty."""
        from app.models.models import Skill
        try:
            result = await db.execute(
                select(Skill).where(Skill.is_builtin == True).limit(1)  # noqa: E712
            )
            if result.scalar_one_or_none() is not None:
                return
            for skill in _BUILTIN_SKILLS:
                db.add(Skill(
                    id=str(uuid.uuid4()),
                    name=skill["name"],
                    description=skill.get("description", ""),
                    category=skill["category"],
                    trigger_keywords=json.dumps(skill.get("trigger_keywords", [])),
                    instruction_block=skill["instruction_block"],
                    is_enabled=True,
                    is_builtin=True,
                    rank=skill.get("rank", 100),
                    visibility="global",
                    created_by="system",
                ))
            await db.commit()
            logger.info("Seeded %d built-in skills", len(_BUILTIN_SKILLS))
        except Exception as e:
            logger.warning("Failed to seed built-in skills: %s", e)

    def _message_matches(self, message: str, keywords: list[str]) -> bool:
        """Simple keyword matching — case-insensitive substring."""
        msg_lower = message.lower()
        return any(kw.lower() in msg_lower for kw in keywords)

    async def match_skills_for_message(
        self,
        db,
        user_message: str,
        user_id: str,
        tenant_id: Optional[str] = None,
        max_skills: int = 3,
    ) -> list[dict]:
        """Return instruction blocks from skills matching the user message."""
        from app.models.models import Skill
        from sqlalchemy import or_, and_

        if not user_message:
            return []

        try:
            result = await db.execute(
                select(Skill)
                .where(Skill.is_enabled == True)  # noqa: E712
                .where(
                    or_(
                        Skill.visibility == "global",
                        and_(Skill.visibility == "org", Skill.tenant_id == tenant_id) if tenant_id else False,
                        and_(Skill.visibility == "user", Skill.user_id == user_id),
                    )
                )
                .order_by(Skill.rank)
            )
            skills = result.scalars().all()
        except Exception as e:
            logger.warning("Failed to load skills: %s", e)
            return []

        matched = []
        for skill in skills:
            if len(matched) >= max_skills:
                break
            keywords = []
            if skill.trigger_keywords:
                try:
                    keywords = json.loads(skill.trigger_keywords)
                except Exception:
                    keywords = []
            if not keywords or self._message_matches(user_message, keywords):
                if keywords and not self._message_matches(user_message, keywords):
                    continue
                matched.append({
                    "id": skill.id,
                    "name": skill.name,
                    "category": skill.category,
                    "instruction_block": skill.instruction_block,
                    "model_preference": skill.model_preference,
                })

        return matched

    async def list_skills(
        self,
        db,
        user_id: str,
        category: Optional[str] = None,
        admin: bool = False,
    ) -> list:
        from app.models.models import Skill
        from sqlalchemy import or_, and_

        try:
            q = select(Skill).order_by(Skill.rank, Skill.category)
            if not admin:
                q = q.where(
                    or_(
                        Skill.visibility == "global",
                        and_(Skill.visibility == "user", Skill.user_id == user_id),
                    )
                )
            if category:
                q = q.where(Skill.category == category)
            result = await db.execute(q)
            return result.scalars().all()
        except Exception as e:
            logger.warning("Failed to list skills: %s", e)
            return []

    async def create_skill(self, db, user_id: str, **kwargs):
        from app.models.models import Skill
        visibility = kwargs.get("visibility", "user")
        skill = Skill(
            id=str(uuid.uuid4()),
            name=kwargs["name"],
            description=kwargs.get("description", ""),
            category=kwargs.get("category", "general"),
            trigger_keywords=json.dumps(kwargs.get("trigger_keywords", [])) if kwargs.get("trigger_keywords") else None,
            instruction_block=kwargs["instruction_block"],
            model_preference=kwargs.get("model_preference"),
            is_enabled=kwargs.get("is_enabled", True),
            is_builtin=False,
            rank=kwargs.get("rank", 100),
            visibility=visibility,
            created_by=user_id,
            user_id=user_id if visibility == "user" else None,
        )
        db.add(skill)
        await db.commit()
        await db.refresh(skill)
        return skill

    async def update_skill(self, db, skill_id: str, user_id: str, admin: bool = False, **kwargs):
        from app.models.models import Skill
        result = await db.execute(select(Skill).where(Skill.id == skill_id))
        skill = result.scalar_one_or_none()
        if not skill:
            return None
        # Non-admins can toggle is_enabled on any skill, but can only edit non-builtin skills they own
        if not admin and skill.is_builtin:
            # Only allow is_enabled toggle for built-in skills
            allowed = {k: v for k, v in kwargs.items() if k == "is_enabled"}
            kwargs = allowed
        elif not admin and skill.user_id != user_id:
            return None
        for k, v in kwargs.items():
            if k == "trigger_keywords" and isinstance(v, list):
                setattr(skill, k, json.dumps(v))
            elif hasattr(skill, k) and v is not None:
                setattr(skill, k, v)
        skill.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(skill)
        return skill

    async def delete_skill(self, db, skill_id: str, user_id: str, admin: bool = False):
        from app.models.models import Skill
        result = await db.execute(select(Skill).where(Skill.id == skill_id))
        skill = result.scalar_one_or_none()
        if not skill:
            return False
        if not admin and (skill.is_builtin or skill.user_id != user_id):
            return False
        await db.delete(skill)
        await db.commit()
        return True


skill_service = SkillService()
