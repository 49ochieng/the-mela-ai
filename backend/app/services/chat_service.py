"""
Mela AI - Chat Service
"""

import asyncio
import logging
from typing import AsyncGenerator, List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update as sa_update
import json
import uuid

from app.core.config import settings
from app.core.database import MockSession, get_db
from app.models import Conversation, Message, ModelUsage, Project, ProjectMemory, GeneratedFileLog
from app.schemas.chat import (
    ChatRequest, StreamChunk, Citation,
    ConversationCreate, ConversationResponse,
)
from app.schemas.auth import UserInfo
from app.services.openai_service import openai_service
from app.services.rag_service import rag_service
from app.agents.tool_executor import tool_executor
from app.core.mode import UserSession

logger = logging.getLogger(__name__)

# In-memory storage for when database is not available
_in_memory_conversations: Dict[str, Dict] = {}
# _in_memory_messages is kept as the fallback dict passed into context_cache
# functions.  When Redis is available it acts as a no-op secondary store;
# when Redis is unavailable it is the sole store (original behaviour).
_in_memory_messages: Dict[str, List[Dict]] = {}

# Context cache (Redis-backed, falls back to _in_memory_messages)
from app.core.context_cache import (
    append_message_to_context as _ctx_append,
    get_active_context as _ctx_get,
    invalidate_context as _ctx_invalidate,
    set_context as _ctx_set,
)


# ─────────────────────────────────────────────────────────────────────────────
# System prompts — Personal vs Work are strictly separated.
# Personal: no org data, no connectors, no SharePoint mentions.
# Work: full enterprise grounding, citations, connector access.
# ─────────────────────────────────────────────────────────────────────────────

PERSONAL_SYSTEM_PROMPT = """You are Mela AI, an intelligent personal AI assistant.

You can help with:
- Answering questions and providing detailed explanations
- Writing, editing, summarizing, and comparing documents
- Code assistance and technical problem-solving
- Data analysis, statistics, and visualization
- Generating downloadable files: Word (.docx), Excel (.xlsx), PDF, CSV, etc.
- Image generation on request
- General productivity and creative tasks

You are running in **Personal mode**.
- You do not have access to organizational data, SharePoint, OneDrive, or \
enterprise knowledge bases.
- You can work with files the user uploads in this session.
- Keep responses clear, helpful, and focused on what the user needs.

## Code Interpreter — File Generation (MANDATORY)
You have `run_python_code` — a Python sandbox that produces downloadable files.

RULE: For ANY request that involves creating or generating a file (Word document, Excel
spreadsheet, PDF, CSV, chart, report, analysis, table, or any data transformation),
you MUST call `run_python_code` and produce the actual file. This applies to ALL models.
- Do NOT show file content as a code block in the chat.
- Do NOT say "here's what the file would contain" — produce the real file.
- Do NOT ask the user if they want a file — if they ask for a document, generate it.

Available libraries: pandas, numpy, matplotlib, seaborn, openpyxl, xlsxwriter,
fpdf2, python-docx, fitz (PyMuPDF), scipy, Pillow, zipfile, csv, json, re, math.

File guidance:
- Excel: xlsxwriter for formatted reports; openpyxl for transforms
- PDF: use the pre-loaded `PDF` class and `safe_text()` helper — they handle
  Unicode encoding automatically. Example:
  ```python
  pdf = PDF(); pdf.add_page()
  pdf.heading('Title', size=16)
  pdf.para('Body text that wraps automatically.')
  pdf.save('report.pdf')
  ```
  Always use `pdf.para()` or `pdf.multi_cell()` for body text, never `cell()`
  with long strings. Call `safe_text()` on any string with special characters.
- Word: python-docx for .docx creation
- Charts: plt.savefig('chart.png', dpi=150, bbox_inches='tight')
- Always use explicit filenames (e.g. 'report.xlsx', not 'output')

## Document Handling
When a user uploads documents, their full text content is included in the
conversation. Compare, summarise, extract, or transform across multiple files.

## Response Style
- Be clear and direct.
- If you are not sure, say what you checked and what is missing.
- In voice mode, keep responses conversational.

## Agentic Behavior
- Never stop silently — always give a final result, a clear blocker, or a follow-up question.
- Ask one targeted question when a task is ambiguous before proceeding.
- For multi-step tasks, continue through all steps without interrupting the user unless
  confirmation is required (e.g. sending or deleting).
- Final answers must be complete and ready to act on.

## Accuracy (NON-NEGOTIABLE)
- When a tool returns `"success": true` or returns a result without an `"error"` key,
  the action SUCCEEDED. You MUST report it as successful.
- NEVER say "there was an issue", "it failed", or "there was a problem" when the tool
  result shows success. Reporting failure on a successful action is misleading.
- If a tool returns `"error"`, report the exact error accurately — do not invent reasons.

## Persistent Memory System
You have a three-layer memory system. Use it proactively — it is what makes you
genuinely intelligent rather than a stateless chatbot.

**Memory context blocks** (injected above the conversation):
- `[LONG_TERM_MEMORY]` — Durable preferences, corrections, facts, style rules.
  Corrections and style entries are listed first and override your defaults.
- `[SESSION_MEMORY]` — Compressed summary of this conversation: goals, key facts,
  entities (people, projects, tools). Use it to avoid re-asking answered questions.

### How to use memory (MANDATORY)
1. **Apply corrections immediately and silently.** If a correction says "call me Alex",
   use Alex everywhere without acknowledgment.
2. **Apply style memories to every response.** If a style says "concise bullets",
   always use bullets without asking.
3. **Personalize proactively.** Apply preference and fact memories before the user
   has to repeat themselves.
4. **Use session entities and goals** to give context-aware answers.
5. **Never say "Based on my memory..."** — apply memories naturally, as if you know.

### Writing new memories
Append a `[MEMORY_UPDATE]` block (invisible to user) when you learn something worth
persisting. Keep entries specific and actionable.

```
[MEMORY_UPDATE]
action: add|update|remove
type: preference|correction|fact|context|style
content: One concrete, reusable fact or rule (third-person declarative)
category: optional tag (e.g., "communication", "coding", "role")
[/MEMORY_UPDATE]
```

Types (priority order — corrections applied first):
- **correction** — User corrected you; always apply, highest priority.
- **style** — Formatting/tone rules (e.g., "always use metric units").
- **preference** — Workflow or tool preferences.
- **fact** — Stable facts about the user or environment.
- **context** — Project or business context shaping answers.

Rules: persist only significant reusable facts; use `update` + `target: <keyword>`
to replace outdated facts; use `remove` when user asks to forget.

Current user: {user_name} ({user_email})
"""

SYSTEM_PROMPT = """You are Mela AI, an intelligent AI assistant powered by Armely.

You can help with:
- Answering questions and providing detailed explanations
- Analyzing images, documents, spreadsheets, presentations, and code files
- Writing, editing, summarizing, and comparing multiple documents
- Generating images and visuals on request
- Code assistance and technical problem-solving
- Data analysis, statistics, and visualization
- Generating downloadable documents: Word (.docx), Excel (.xlsx), PDF, CSV, etc.
- Searching the company knowledge base (SharePoint, OneDrive, armely.com)
- Sending emails and drafting messages
- Managing calendar and scheduling meetings
- Creating tasks in Planner and searching SharePoint

## Connected Knowledge Sources
When the user asks "what data are you connected to" or "where did you get this", answer:
- SharePoint sites: Test-team, ZapManufacturing, LearningResources, ArmelyLLC (all under armely.sharepoint.com)
- OneDrive: armely-my.sharepoint.com (user files, requires login)
- Website: armely.com (full site, depth 3)
- Any files the user has uploaded in this session

## Grounding and Citation Policy (NON-NEGOTIABLE)
1. **Never fabricate facts, citations, or sources.** Every factual claim that depends on
   retrieved documents must be supported by an actual retrieved chunk.
2. **Always cite.** When your answer depends on retrieved content, include citations in this format:
   - Text mode: `[Document Title](url)` or `[Document Title — SharePoint]`
   - Voice mode: "I found this in the [Site Name] document titled [File Name]."
3. **Never cite a file that was not retrieved in this session.** Only cite sources that appear
   in the "Retrieved Knowledge Base Content" or "Uploaded Documents" sections above.
4. **When retrieval returns nothing:** Say clearly that you could not find supporting documents,
   explain what you searched, and suggest where the user might look or what to ask next.
   Do NOT guess or fill the gap with plausible-sounding information.
5. **When sources conflict:** Present both perspectives, cite each source, and recommend which
   to trust based on recency and authority — without inventing additional facts.
6. **Confidence signals:** Use language that matches your certainty:
   - Strong evidence → answer normally with citations
   - Partial evidence → note what is missing, still cite what you found
   - No evidence → explicitly say retrieval returned nothing, ask clarifying question

## Enterprise Knowledge Base
You have access to Armely's enterprise knowledge base containing SharePoint documents,
OneDrive files, and content from the armely.com website.
- If knowledge base content is provided in this prompt (see "Retrieved Knowledge Base Content"),
  **use it directly** to answer the question — do NOT claim you cannot access SharePoint.
- If no retrieved content is present and the user asks about Armely (services, team, policies,
  documents, projects), call the `search_documents` tool to find relevant information.
- Never say "I don't have access to SharePoint" when document content is already provided above.

## Code Interpreter — File Generation (MANDATORY)
You have `run_python_code` — a Python sandbox that runs code and returns downloadable files.

RULE: For ANY request that involves creating or generating a file (Word document, Excel
spreadsheet, PDF, CSV, chart, report, analysis, table, or any data transformation),
you MUST call `run_python_code` and produce the actual file. This applies to ALL models.
- Do NOT show file content as a code block in the chat.
- Do NOT say "here's what the file would contain" — produce the real file.
- Do NOT ask the user if they want a file — if they ask for a document, generate it.
- This is non-negotiable regardless of which model you are running on.

Available libraries: pandas, numpy, matplotlib, seaborn, openpyxl, xlsxwriter, fpdf2,
python-docx, fitz (PyMuPDF), scipy, Pillow, zipfile, csv, json, re, math, datetime.

File guidance:
- Excel: xlsxwriter for formatted reports with charts; openpyxl for transforms/reads
- PDF: use the pre-loaded `PDF` class and `safe_text()` helper — they handle
  Unicode encoding automatically. Example:
  ```python
  pdf = PDF(); pdf.add_page()
  pdf.heading('Title', size=16)
  pdf.para('Body text that wraps automatically.')
  pdf.save('report.pdf')
  ```
  Always use `pdf.para()` or `pdf.multi_cell()` for body text, never `cell()`
  with long strings. Call `safe_text()` on any string with special characters.
- Word: python-docx for .docx creation and editing
- Charts: plt.savefig('chart.png', dpi=150, bbox_inches='tight')
- Multi-file output: zipfile.ZipFile → output.zip
- Always use explicit filenames (e.g. 'report.xlsx', not 'output')

Input files from user uploads are pre-loaded in the sandbox by filename — use the exact
filename to open them. Every file written to the working directory is automatically returned
as a download button in the chat.

## Document Handling
When a user uploads documents, their full text content is included in the conversation.
You can compare, summarise, extract, or transform content across multiple documents.
For images, OCR text is extracted when available.
Treat uploaded files as first-class sources — prefer them when the user asks questions
clearly about that file, and always cite them by filename.

## Response Style
- Be clear and direct. Separate "Answer" from "Sources" when citing multiple documents.
- If you are not sure, say what you checked and what is missing. Do not overpromise.
- In voice mode, do not read raw citation markup — speak citations naturally.
- Keep voice responses conversational with short initial acknowledgement, then the full answer.

## Daily Task Assistance
For common tasks (summaries, action items, emails, meeting notes, drafts, trackers):
- Ask minimal follow-up questions — prefer doing the work using available data.
- Keep outputs clean and ready to use.
- For file generation, always produce actual downloadable artifacts via the code interpreter.

## Agentic Behaviour (IMPORTANT)
You are an agent that can take multi-step actions using tools. Follow these rules:

1. **Never stop silently.** Always report back with a final result, a clear blocker, or
   a follow-up question. Never leave the user wondering what happened.
2. **Ask before acting on ambiguous instructions.** If a task is unclear (e.g. "send that
   email" with no prior email visible, or "update the report" with no file), ask one
   targeted question before proceeding.
3. **Continue until done.** For multi-step tasks (e.g. search → summarise → draft →
   send), run each step without prompting the user between steps, unless a step
   requires confirmation (e.g. sending or deleting).
4. **Report tool outcomes accurately.** After each tool call, briefly report what the
   tool returned (e.g. "Found 5 emails from last week. Here's a summary:") before
   continuing. CRITICAL: if the tool result contains `"success": true` or has no
   `"error"` key, the action succeeded — report it as successful. NEVER say "there was
   an issue" or "it failed" when the tool returned success. This is a mandatory accuracy
   rule — misreporting a successful action as failed is unacceptable.
5. **Handle tool errors gracefully.** If a tool returns an `"error"` key, say what
   failed and why (in plain language), then offer alternatives (retry, different
   approach, manual workaround).
6. **Confirm before irreversible actions.** Always ask "Shall I go ahead?" before sending
   emails, creating calendar events, or deleting data.
7. **Final answers must be complete.** Do not end with "I'll now do X" — do X first,
   then present the result. The final message to the user must be ready to act on.

## Microsoft Graph Productivity Skills

You have three connected productivity areas. Use these tools proactively
when the user asks about their email, calendar, or tasks.

### Email Assistant — Two paths, clearly separated

**PATH 1 — Human-in-the-loop (mandatory for all normal chat requests):**
1. Call create_draft_email → composes and saves the draft, returns draft_id.
2. Present the draft clearly: show subject, recipients, and full body text.
3. Offer the user three explicit choices:
   - **Send now** — call send_draft_email with the draft_id
   - **Keep as draft** — draft is already saved in Outlook, nothing more to do
   - **Edit / make changes** — ask what to change, then update and re-present
4. Only proceed to send after the user makes an explicit choice.
This path is required for ANY request that comes from normal conversation.
A user saying "send an email to Alice" is always PATH 1.

**PATH 2 — Automated workflow (approved automation only, all 4 conditions required):**
You may call send_email directly (skipping draft review) ONLY when ALL of the following
are true simultaneously:
  (a) workflow_type is explicitly set to an approved value: "onboarding", "offboarding",
      "system_notification", or "automated_report".
  (b) The action originates from a trusted backend workflow or structured system event —
      not from a conversational user request.
  (c) Recipients, template content, and full context are already validated before the
      call is made.
  (d) You include workflow_type in the send_email call so the send is logged as an
      automated system send.
Do NOT classify a normal chat request as an automated workflow — if the user typed it
in the chat window, it is PATH 1 regardless of what they say the purpose is.

- Read inbox: Use get_inbox when asked "what emails do I have?", "show my inbox",
  "any new messages?". Display sender, subject, time, preview.
- Compose / reply: ALWAYS start with create_draft_email (PATH 1) unless PATH 2
  conditions are explicitly met. Never send in one shot without showing the draft first.
- After draft is saved, always present the full email content and the three action choices.
- If a tool returns requires_consent=true, tell the user they need to grant email
  permissions by signing in again to accept Mail.Read/Mail.Send.

### Meeting Scheduler (get_calendar, schedule_meeting, check_availability)
- View calendar: Use get_calendar when asked about upcoming meetings or schedule.
  Show events grouped by date with time and location.
- Schedule meeting: Use schedule_meeting only after confirming subject, date/time,
  attendees, and duration. Default to Teams online meeting. Always report the link.
- Check availability: Use check_availability before suggesting a time when the user
  provides multiple attendees and asks "when are they free?".
- Interpret relative times ("tomorrow 2pm") using context clues from the user.

### Planner Assistant (list_planner_tasks, create_task)
- List tasks: Use list_planner_tasks when asked "what tasks do I have?", "show my
  Planner", or "what's on my to-do list?". Show title, due date, completion %.
- Create task: Use create_task when asked to add a task. Provide plan_id for Planner
  (enterprise) tasks; omit it for To Do (personal) tasks.
  Always confirm title and due date before creating.
- Never create tasks in plans the user hasn't specified.

### Graph Security Rules (NON-NEGOTIABLE)
- Never send email without presenting it to the user first, unless all 4 PATH 2
  automation conditions above are met simultaneously.
- Never use send_email as a silent first step for any conversational request.
- Never create meetings without confirming subject, time, and attendees.
- If a Graph error mentions "consent" or "permissions", explain what is needed.
- Do not repeat or expose access tokens or client secrets in any response.
- After any successful send, confirm: "Your email has been sent to [recipients]."
- After a draft is saved, always present the full content and the available choices.

## Persistent Memory System
You have a three-layer memory system. Use it proactively — it is what makes you
genuinely intelligent rather than a stateless chatbot.

**Memory context blocks** (injected above the conversation):
- `[LONG_TERM_MEMORY]` — Durable preferences, corrections, facts, style rules.
  Corrections and style entries are listed first and override your defaults.
- `[SESSION_MEMORY]` — Compressed summary of this conversation: goals, key facts,
  entities (people, projects, tools). Use it to avoid re-asking answered questions.

### How to use memory (MANDATORY)
1. **Apply corrections immediately and silently.** If a correction says "call me Alex",
   use Alex everywhere without acknowledgment.
2. **Apply style memories to every response.** If a style says "concise bullets",
   always use bullets without asking.
3. **Personalize proactively.** Apply preference and fact memories before the user
   has to repeat themselves.
4. **Use session entities and goals** to give context-aware answers.
5. **Never say "Based on my memory..."** — apply memories naturally, as if you know.

### Writing new memories
Append a `[MEMORY_UPDATE]` block (invisible to user) when you learn something worth
persisting. Keep entries specific and actionable.

```
[MEMORY_UPDATE]
action: add|update|remove
type: preference|correction|fact|context|style
content: One concrete, reusable fact or rule (third-person declarative)
category: optional tag (e.g., "communication", "coding", "role")
[/MEMORY_UPDATE]
```

Types (priority order — corrections applied first):
- **correction** — User corrected you; always apply, highest priority.
- **style** — Formatting/tone rules (e.g., "always use metric units").
- **preference** — Workflow or tool preferences.
- **fact** — Stable facts about the user or environment.
- **context** — Project or business context shaping answers.

Rules: persist only significant reusable facts; use `update` + `target: <keyword>`
to replace outdated facts; use `remove` when user asks to forget.

Current user: {user_name} ({user_email})
Department: {department}
"""


class ChatService:
    """Service for chat operations."""

    def _is_mock_session(self, db) -> bool:
        """Check if we're using a mock session (no DB)."""
        return isinstance(db, MockSession)

    # ─────────────────────────────────────────────────────────────────────────
    # Conversation helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def get_or_create_conversation(
        self,
        db: AsyncSession,
        user: UserInfo,
        conversation_id: Optional[str] = None,
        create_data: Optional[ConversationCreate] = None,
    ) -> Conversation:
        """Get existing or create new conversation."""

        if self._is_mock_session(db):
            if conversation_id and conversation_id in _in_memory_conversations:
                conv_data = _in_memory_conversations[conversation_id]
                return Conversation(
                    id=conversation_id,
                    user_id=user.id,
                    title=conv_data.get("title", "New Chat"),
                    model=conv_data.get("model", "gpt-5.2-chat"),
                    system_prompt=conv_data.get("system_prompt"),
                    created_at=conv_data.get("created_at", datetime.utcnow()),
                    updated_at=conv_data.get("updated_at", datetime.utcnow()),
                    is_archived=conv_data.get("is_archived", False),
                )

            data = create_data or ConversationCreate()
            # Reuse conversation_id if provided (keeps frontend/backend in sync after restart)
            conv_id = conversation_id if conversation_id else str(uuid.uuid4())
            now = datetime.utcnow()
            private_expires = now + timedelta(days=20) if data.is_private else None
            _in_memory_conversations[conv_id] = {
                "id": conv_id,
                "user_id": user.id,
                "title": data.title or "New Conversation",
                "model": data.model or "gpt-5.2-chat",
                "system_prompt": data.system_prompt,
                "is_archived": False,
                "is_private": data.is_private,
                "private_expires_at": private_expires,
                "project_id": data.project_id,
                "created_at": now,
                "updated_at": now,
            }
            _in_memory_messages[conv_id] = []
            # Prime an empty Redis list for the new conversation.
            import asyncio as _asyncio
            _asyncio.ensure_future(
                _ctx_set(conv_id, [], _fallback=_in_memory_messages)
            )

            return Conversation(
                id=conv_id,
                user_id=user.id,
                title=data.title or "New Conversation",
                model=data.model or "gpt-5.2-chat",
                system_prompt=data.system_prompt,
                is_archived=False,
                is_private=data.is_private,
                private_expires_at=private_expires,
                project_id=data.project_id,
                created_at=now,
                updated_at=now,
            )

        # Normal DB flow
        if conversation_id:
            result = await db.execute(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user.id,
                )
            )
            conversation = result.scalar_one_or_none()
            if conversation:
                return conversation

        data = create_data or ConversationCreate()
        now = datetime.utcnow()
        private_expires = now + timedelta(days=20) if data.is_private else None
        # Resolve profile_mode: normalise 'org' → 'work' for legacy compat
        raw_ctx = getattr(data, "context_type", "personal") or "personal"
        profile_mode = "work" if raw_ctx == "org" else raw_ctx
        tenant_id = getattr(data, "tenant_id", None)
        # Validate: work requires tenant_id; personal must not have one
        if profile_mode == "work" and not tenant_id:
            raise ValueError(
                "Work profile conversations require a tenant_id. "
                "Please sign in with your organization account."
            )
        if profile_mode == "personal":
            tenant_id = None
        conversation = Conversation(
            id=str(uuid.uuid4()),
            user_id=user.id,
            title=data.title or "New Conversation",
            model=data.model or "gpt-5.2-chat",
            system_prompt=data.system_prompt,
            is_private=data.is_private,
            private_expires_at=private_expires,
            project_id=data.project_id,
            # Authoritative namespace fields
            profile_mode=profile_mode,
            tenant_id=tenant_id,
            # Legacy compat field
            context_type=raw_ctx,
            workspace_id=getattr(data, "workspace_id", None),
            created_at=now,
            updated_at=now,
        )
        db.add(conversation)
        await db.flush()
        return conversation

    async def get_conversation_messages(
        self,
        db: AsyncSession,
        conversation_id: str,
        limit: int = 100,
    ) -> List[Message]:
        """Get messages for a conversation (oldest first)."""

        if self._is_mock_session(db):
            messages_data = await _ctx_get(
                conversation_id, _fallback=_in_memory_messages
            )
            return [
                Message(
                    id=m.get("id", str(uuid.uuid4())),
                    conversation_id=conversation_id,
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    model=m.get("model"),
                    created_at=m.get("created_at", datetime.utcnow()),
                )
                for m in messages_data[-limit:]
            ]

        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # ─────────────────────────────────────────────────────────────────────────
    # Image-generation detection
    # ─────────────────────────────────────────────────────────────────────────

    def _is_image_generation_request(self, message: str) -> bool:
        """Heuristically detect whether the user wants an image generated."""
        msg_lower = message.lower().strip()
        triggers = [
            # Direct generation requests
            "generate image", "generate an image", "generate a image",
            "create image", "create an image", "create a image",
            "make an image", "make a image", "make image",
            "generate photo", "create photo", "make a photo",
            "generate picture", "generate a picture", "create a picture", "create picture",
            # Visuals / designs
            "generate visual", "generate a visual", "create visual", "create a visual",
            "generate design", "create design", "create a design", "make a design",
            "generate a poster", "create a poster", "make a poster",
            "generate a banner", "create a banner",
            "generate a logo", "create a logo",
            "generate artwork", "create artwork", "generate art", "create art",
            "generate a render", "create a render", "render an image",
            "generate illustration", "create illustration", "create an illustration",
            # Action verbs
            "draw ", "illustrate ", "paint ", "sketch ",
            "design an image", "design a ", "visualize ",
            "show me an image", "show me a picture", "show me a visual",
            # Direct AI image tool mentions
            "dalle ", "dall-e ", "imagine ",
        ]
        return any(msg_lower.startswith(t) or f" {t}" in msg_lower for t in triggers)

    # ─────────────────────────────────────────────────────────────────────────
    # Message builder (multimodal)
    # ─────────────────────────────────────────────────────────────────────────

    def build_messages(
        self,
        user: UserInfo,
        conversation: Conversation,
        history: List[Message],
        user_message: str,
        context: Optional[str] = None,
        inline_attachments=None,
        model: str = "gpt-5.2-chat",
    ) -> List[Dict[str, Any]]:
        """Build messages array for OpenAI, including multimodal content."""
        messages: List[Dict[str, Any]] = []

        # Determine whether the model can accept image_url content parts
        vision_ok = openai_service.model_supports_vision(model) if openai_service else False

        # System prompt — personal mode gets a clean prompt with no org data
        # references; work mode gets the full enterprise-aware prompt.
        _conv_profile = getattr(conversation, "profile_mode", "personal")
        _base_prompt = (
            SYSTEM_PROMPT if _conv_profile == "work" else PERSONAL_SYSTEM_PROMPT
        )
        system_prompt = conversation.system_prompt or _base_prompt.format(
            user_name=user.name,
            user_email=user.email,
            department=user.department or "Unknown",
        )

        if context:
            system_prompt += (
                "\n\n## Retrieved Knowledge Base Content\n"
                "The following content was automatically retrieved from SharePoint documents "
                "and the Armely website to answer the user's query. "
                "**You MUST use this content to answer the user's question directly. "
                "Do NOT say you cannot access SharePoint — the document content is provided below.** "
                "Cite the source titles when referencing this information.\n\n"
                + context
            )

        messages.append({"role": "system", "content": system_prompt})

        # History (keep last 100 messages to stay within context)
        for msg in history[-100:]:
            messages.append({"role": msg.role, "content": msg.content})

        # Current message – multimodal if attachments present
        if inline_attachments:
            content_parts: List[Dict[str, Any]] = []
            extra_text_parts: List[str] = []

            for idx, att in enumerate(inline_attachments, 1):
                ct = att.content_type or ""
                label = att.filename or f"attachment {idx}"

                if ct.startswith("image/") and att.base64_data:
                    if vision_ok:
                        # Model supports vision — send the image inline
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": att.base64_data, "detail": "high"},
                        })
                        if att.ocr_text:
                            extra_text_parts.append(
                                f"[OCR text from {label}]:\n{att.ocr_text}"
                            )
                    else:
                        # Model does NOT support vision — fall back to OCR text
                        ocr = att.ocr_text or att.text_content or ""
                        if ocr:
                            extra_text_parts.append(
                                f"[Image {label} — OCR extracted text]:\n{ocr}"
                            )
                        else:
                            extra_text_parts.append(
                                f"[Image {label} attached — no text could be extracted]"
                            )
                elif att.text_content or att.raw_base64:
                    ct_lower = ct.lower()
                    is_spreadsheet = (
                        "spreadsheet" in ct_lower
                        or "excel" in ct_lower
                        or ct_lower in (
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            "application/vnd.ms-excel",
                            "text/csv",
                            "application/csv",
                        )
                        or label.lower().endswith((".xlsx", ".xls", ".csv"))
                    )
                    if att.text_content:
                        # Documents arrive pre-wrapped with [BEGIN FILE: …]
                        # safety markers from the upload endpoint.  Use
                        # them as-is to avoid double-labelling.  Audio
                        # transcripts and other plain text get a simple
                        # header instead.
                        if att.text_content.startswith("[BEGIN FILE:"):
                            extra_text_parts.append(
                                att.text_content[:25000]
                            )
                        else:
                            extra_text_parts.append(
                                f"[Content of {label}]:\n"
                                f"{att.text_content[:25000]}"
                            )
                    if is_spreadsheet and att.raw_base64:
                        extra_text_parts.append(
                            f"[Spreadsheet file '{label}' is pre-loaded in the Python working directory. "
                            f"To run calculations use the run_python_code tool:\n"
                            f"  import pandas as pd\n"
                            f"  df = pd.read_{'csv' if label.lower().endswith('.csv') else 'excel'}('{label}')\n"
                            f"  print(df.describe())\n"
                            f"The file will be available as '{label}' in the current directory.]"
                        )

            full_text = user_message
            if extra_text_parts:
                full_text = "\n\n".join(extra_text_parts) + "\n\n---\n\n" + user_message

            if content_parts:
                # Multimodal message
                content_parts.insert(0, {"type": "text", "text": full_text})
                messages.append({"role": "user", "content": content_parts})
            else:
                # Text-only (document attachments or non-vision model)
                messages.append({"role": "user", "content": full_text})
        else:
            messages.append({"role": "user", "content": user_message})

        return messages

    # ─────────────────────────────────────────────────────────────────────────
    # Image generation handler
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_image_generation(
        self,
        db,
        conversation: Conversation,
        user: UserInfo,
        request: ChatRequest,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Route to DALL-E for image generation requests."""
        try:
            from app.services.dalle_service import dalle_service, ImageSize, ImageQuality, ImageStyle

            # Let the user know image generation is in progress
            yield StreamChunk(type="content", content="Generating your image, this may take a moment...")

            # Strip common trigger words to extract the clean prompt
            prompt = request.message
            for prefix in [
                "generate an image of ", "generate an image ", "generate image of ", "generate image ",
                "create an image of ", "create an image ", "create image of ", "create image ",
                "make an image of ", "make an image ", "make image of ", "make image ",
                "generate a visual of ", "generate a visual ", "create a visual of ", "create a visual ",
                "generate a design of ", "generate a design ", "create a design of ", "create a design ",
                "generate a picture of ", "generate a picture ", "create a picture of ", "create a picture ",
                "generate a photo of ", "generate a photo ", "create a photo of ", "create a photo ",
                "generate artwork of ", "generate artwork ", "create artwork of ", "create artwork ",
                "generate art of ", "generate art ", "create art of ", "create art ",
                "generate a poster of ", "generate a poster ", "create a poster of ", "create a poster ",
                "generate a logo of ", "generate a logo ", "create a logo of ", "create a logo ",
                "generate an illustration of ", "generate an illustration ",
                "create an illustration of ", "create an illustration ",
                "design an image of ", "design an image ", "design a ",
                "draw ", "illustrate ", "paint ", "sketch ", "visualize ",
                "show me an image of ", "show me a picture of ", "show me a visual of ",
                "imagine ", "dalle ", "dall-e ",
            ]:
                if prompt.lower().startswith(prefix):
                    prompt = prompt[len(prefix):]
                    break

            result = await dalle_service.generate_image(
                prompt=prompt,
                size=ImageSize.SQUARE,
                quality=ImageQuality.STANDARD,
                style=ImageStyle.VIVID,
                response_format="b64_json",
            )

            revised = result.revised_prompt

            # Build the data URI for the image
            if result.b64_json:
                img_url = f"data:image/png;base64,{result.b64_json}"
            else:
                img_url = result.url

            # Replace the "generating" placeholder with the real response
            text_md = "\n\nHere's your generated image!"
            if revised and revised != prompt:
                text_md += f"\n\n*Revised prompt: {revised}*"

            yield StreamChunk(type="content", content=text_md)

            # Send the image as a dedicated chunk — frontend renders it natively
            yield StreamChunk(
                type="image_generated",
                data={
                    "url": img_url,
                    "revised_prompt": revised or prompt,
                    "original_prompt": prompt,
                },
            )

            # Save to conversation (only text, not the huge base64)
            assistant_msg_id = str(uuid.uuid4())
            user_msg_id = str(uuid.uuid4())
            now = datetime.utcnow()

            if self._is_mock_session(db):
                await _ctx_append(
                    conversation.id,
                    {"id": user_msg_id, "role": "user",
                     "content": request.message, "model": request.model, "created_at": now},
                    _fallback=_in_memory_messages,
                )
                await _ctx_append(
                    conversation.id,
                    {"id": assistant_msg_id, "role": "assistant",
                     "content": text_md, "model": "dall-e-3", "created_at": now},
                    _fallback=_in_memory_messages,
                )
                _ctx_len = len(_in_memory_messages.get(conversation.id, []))
                if _ctx_len <= 2:
                    _in_memory_conversations[conversation.id]["title"] = (
                        request.message[:50] + "..." if len(request.message) > 50 else request.message
                    )
                _in_memory_conversations[conversation.id]["updated_at"] = now
            else:
                db.add(Message(
                    id=user_msg_id, conversation_id=conversation.id,
                    role="user", content=request.message, model=request.model,
                ))
                db.add(Message(
                    id=assistant_msg_id, conversation_id=conversation.id,
                    role="assistant", content=text_md, model="dall-e-3",
                ))
                has_prior = await self._has_prior_messages(db, conversation.id)
                if not has_prior:
                    conversation.title = (
                        request.message[:50] + "..." if len(request.message) > 50 else request.message
                    )
                conversation.updated_at = now
                await db.commit()

            yield StreamChunk(
                type="done",
                data={
                    "conversation_id": conversation.id,
                    "message_id": assistant_msg_id,
                    "image_generated": True,
                    "revised_prompt": revised,
                },
            )

        except Exception as e:
            logger.error("Image generation error: %s", e)
            yield StreamChunk(
                type="content",
                content=(
                    "I wasn't able to generate that image right now. "
                    "Please try again or rephrase your request."
                ),
            )

    def _format_code_result(self, result: Dict[str, Any]) -> str:
        """Format code interpreter stdout/stderr as markdown to append to the response.

        File listings are omitted here — each file is streamed as a separate
        'file_generated' SSE chunk before this method is called.
        """
        if not result:
            return ""
        parts: List[str] = []

        stdout = (result.get("stdout") or "").strip()
        stderr = (result.get("stderr") or "").strip()
        success = result.get("success", True)

        if not success and stderr:
            parts.append(f"\n\n**⚠️ Code execution error:**\n```\n{stderr[:2000]}\n```")
        elif stdout:
            parts.append(f"\n\n**Code output:**\n```\n{stdout[:3000]}\n```")

        return "".join(parts)

    @staticmethod
    def _classify_output_type(mime_type: str, filename: str) -> str:
        """Map MIME type / file extension to a short output-type label."""
        mt = (mime_type or "").lower()
        fn = (filename or "").lower()
        if "spreadsheet" in mt or "excel" in mt or fn.endswith((".xlsx", ".xls")):
            return "excel"
        if "pdf" in mt or fn.endswith(".pdf"):
            return "pdf"
        if "wordprocessing" in mt or "word" in mt or fn.endswith((".docx", ".doc")):
            return "word"
        if mt.startswith("image/") or fn.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")):
            return "image"
        if "csv" in mt or fn.endswith(".csv"):
            return "csv"
        if fn.endswith(".zip"):
            return "zip"
        return "other"

    async def _count_messages(self, db: AsyncSession, conversation_id: str) -> int:
        """Count existing messages in a conversation (DB mode)."""
        try:
            result = await db.execute(
                select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
            )
            return int(result.scalar() or 0)
        except Exception as _e:
            logger.debug("_count_messages failed (conv=%s): %s", conversation_id, _e)
            return 0

    async def _has_prior_messages(self, db: AsyncSession, conversation_id: str) -> bool:
        """Check whether a conversation already has messages (DB mode)."""
        try:
            result = await db.execute(
                select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
            )
            count = result.scalar() or 0
            return count > 0
        except Exception as _e:
            logger.debug("_has_prior_messages failed (conv=%s): %s", conversation_id, _e)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Project context helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def _load_project_context(
        self,
        project_id: str,
        current_conv_id: str,
        db: AsyncSession,
    ) -> Optional[Dict[str, Any]]:
        """Build a system message containing project memory + recent cross-chat context."""
        try:
            project = await db.scalar(select(Project).where(Project.id == project_id))
            if not project:
                return None

            memories = list(
                (
                    await db.execute(
                        select(ProjectMemory)
                        .where(ProjectMemory.project_id == project_id)
                        .order_by(ProjectMemory.created_at.desc())
                        .limit(20)
                    )
                ).scalars()
            )

            # Last 3 conversations in this project (excluding current), last 5 messages each
            other_convs = list(
                (
                    await db.execute(
                        select(Conversation)
                        .where(
                            Conversation.project_id == project_id,
                            Conversation.id != current_conv_id,
                            Conversation.is_private == False,  # noqa: E712
                        )
                        .order_by(Conversation.updated_at.desc())
                        .limit(3)
                    )
                ).scalars()
            )

            parts: List[str] = []

            # Project-level custom instructions
            if project.system_prompt:
                parts.append(f"[Project Instructions]\n{project.system_prompt}")

            # Memory block
            if memories:
                mem_lines = "\n".join(f"- {m.fact}" for m in memories)
                parts.append(f"[Project Memory — facts from previous conversations in '{project.name}']\n{mem_lines}")

            # Project files context
            try:
                from app.services.project_service import get_project_file_texts
                file_texts = await get_project_file_texts(project_id, db)
                if file_texts:
                    file_parts = "\n\n".join(
                        f"[Project File: {ft['filename']}]\n{ft['content']}"
                        for ft in file_texts
                    )
                    parts.append(f"[Project Files — documents uploaded to '{project.name}']\n{file_parts}")
            except Exception as ft_err:
                logger.warning(f"Failed to load project file texts: {ft_err}")

            # Recent cross-chat context — single batch query (no N+1 loop)
            if other_convs:
                conv_ids = [c.id for c in other_convs]
                conv_title = {c.id: c.title for c in other_convs}
                # One query for the last 5 messages across all relevant conversations
                from sqlalchemy import and_
                batch_msgs_result = await db.execute(
                    select(Message)
                    .where(
                        and_(
                            Message.conversation_id.in_(conv_ids),
                            Message.role.in_(["user", "assistant"]),
                        )
                    )
                    .order_by(Message.conversation_id, Message.created_at.desc())
                )
                all_msgs = batch_msgs_result.scalars().all()
                # Group by conversation_id, keep last 5 per conversation
                from collections import defaultdict as _dd
                msgs_by_conv: Dict[str, List] = _dd(list)
                for m in all_msgs:
                    if len(msgs_by_conv[m.conversation_id]) < 5:
                        msgs_by_conv[m.conversation_id].append(m)

                ctx_parts: List[str] = []
                for cid in conv_ids:
                    msgs = list(reversed(msgs_by_conv[cid]))
                    if msgs:
                        snippet = "\n".join(
                            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content[:400]}"
                            for m in msgs
                        )
                        ctx_parts.append(f"[{conv_title[cid]}]\n{snippet}")
                if ctx_parts:
                    parts.append(
                        f"[Recent Project Conversations — context from '{project.name}']\n"
                        + "\n\n".join(ctx_parts)
                    )

            if not parts:
                return None

            return {"role": "system", "content": "\n\n".join(parts)}

        except Exception as e:
            logger.warning(f"_load_project_context error: {e}")
            return None

    async def _extract_project_memory(
        self,
        project_id: str,
        conversation_id: str,
        user_message: str,
        assistant_response: str,
    ) -> None:
        """Background task: extract 0–3 memory facts from a conversation turn and store them."""
        try:
            if not openai_service:
                return

            prompt = (
                "You are a memory extraction assistant. "
                "Extract 0 to 3 short, factual bullet points that are worth remembering from the following conversation turn. "
                "Only extract specific, concrete facts (e.g. preferences, decisions, names, key outcomes). "
                "Do NOT extract generic statements or re-state questions. "
                "Return a JSON array of strings (0–3 items). Return [] if nothing is worth remembering.\n\n"
                f"User: {user_message[:1000]}\n\n"
                f"Assistant: {assistant_response[:1500]}"
            )

            extraction_messages = [
                {"role": "system", "content": "You extract memory facts. Return only a JSON array of strings."},
                {"role": "user", "content": prompt},
            ]

            response_text = await openai_service.get_completion(
                messages=extraction_messages,
                model="gpt-4.1",
                max_tokens=256,
                temperature=0.2,
            )

            if not response_text:
                return

            # Parse JSON array
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1] if "```" in cleaned else cleaned
                cleaned = cleaned.lstrip("json").strip()

            facts = json.loads(cleaned)
            if not isinstance(facts, list):
                return

            # Open a fresh DB session for the background task
            async for db_bg in get_db():
                for fact in facts[:3]:
                    if isinstance(fact, str) and fact.strip():
                        try:
                            from app.services.project_service import add_memory
                            await add_memory(project_id, fact.strip(), conversation_id, db_bg)
                        except Exception as mem_err:
                            logger.debug(f"Memory insert skipped: {mem_err}")

        except Exception as e:
            logger.debug(f"_extract_project_memory non-fatal error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Main chat processor
    # ─────────────────────────────────────────────────────────────────────────

    async def process_chat(
        self,
        db: AsyncSession,
        user: UserInfo,
        request: ChatRequest,
        access_token: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Process a chat request with streaming."""
        import uuid as _uuid_mod
        _corr_id = _uuid_mod.uuid4().hex[:12]  # Short correlation ID for log tracing

        # Verify at least one AI provider is available before doing any work
        _any_provider_ok = bool(openai_service)
        if not _any_provider_ok:
            try:
                from app.services.anthropic_service import anthropic_service as _a
                _any_provider_ok = bool(_a)
            except Exception as _e:
                logger.debug("anthropic_service import failed: %s", _e)
        if not _any_provider_ok:
            try:
                from app.services.gemini_service import gemini_service as _g
                _any_provider_ok = bool(_g)
            except Exception as _e:
                logger.debug("gemini_service import failed: %s", _e)
        if not _any_provider_ok:
            yield StreamChunk(
                type="error",
                content="No AI service is configured. Please contact your administrator.",
            )
            return

        # Get or create conversation — bind to profile context
        _ctx = getattr(request, "_profile_context", None)
        _profile_mode = getattr(_ctx, "profile_mode", None) or getattr(request, "context_type", "personal")
        if _profile_mode == "org":
            _profile_mode = "work"
        _tenant_id = getattr(_ctx, "tenant_id", None)

        # ── Budget enforcement (must run before any expensive work) ──────────
        _budget_warning_pct: int = 0
        if not self._is_mock_session(db):
            try:
                from app.services.budget_service import check_budget
                _bgt = await check_budget(db, user.id, tenant_id=_tenant_id)
                if not _bgt.allowed and _bgt.hard_stop:
                    # Hard stop — block the request entirely
                    yield StreamChunk(type="budget_exceeded", data=_bgt.to_dict())
                    yield StreamChunk(
                        type="content",
                        content=(
                            f"**Usage limit reached.** "
                            f"{_bgt.message or 'You have exhausted your allocated budget.'} "
                            "Please contact your administrator to increase your limit."
                        ),
                    )
                    yield StreamChunk(type="done", data={"finish_reason": "budget_exceeded"})
                    return
                elif _bgt.warning:
                    _budget_warning_pct = _bgt.usage_pct
                    # Surface warning to frontend (non-blocking)
                    yield StreamChunk(type="budget_warning", data=_bgt.to_dict())
            except Exception as _bgt_err:
                logger.warning("Budget check failed (non-fatal, continuing): %s", _bgt_err)

        # ── Model access control ──────────────────────────────────────────────
        # 'auto' is a pseudo-model handled by OutcomeOrchestrator — never look it
        # up in ModelRanking, just let it pass through unchanged.
        # For any real model, if the user lacks access silently downgrade rather
        # than blocking the request.
        _requested_model = request.model or "gpt-5.2-chat"
        _is_auto = _requested_model in ("auto", "")
        if not _is_auto and not self._is_mock_session(db):
            try:
                from app.services.model_access_service import get_allowed_models
                _allowed = await get_allowed_models(db, user.id, user.roles or [])
                if _allowed:
                    _allowed_ids = {m.model_id for m in _allowed}
                    if _requested_model not in _allowed_ids:
                        # Pick the best allowed model (lowest rank = highest priority)
                        _best = min(_allowed, key=lambda m: m.rank)
                        logger.info(
                            "Model access control: %s not allowed for user %s"
                            " → downgrading to %s",
                            _requested_model, user.id, _best.model_id,
                        )
                        _requested_model = _best.model_id
            except Exception as _mac_err:
                logger.warning("Model access check failed (non-fatal): %s", _mac_err)
        # Propagate back to request for downstream use
        request = (
            request.model_copy(update={"model": _requested_model})
            if hasattr(request, "model_copy") else request
        )

        # Use 'org' as context_type (legacy frontend alias) for work conversations
        # so the stored value is consistent with the sidebar filter expectations.
        _context_type = "org" if _profile_mode == "work" else _profile_mode
        conversation = await self.get_or_create_conversation(
            db, user, request.conversation_id,
            create_data=ConversationCreate(
                is_private=request.is_private,
                project_id=request.project_id,
                context_type=_context_type,
                tenant_id=_tenant_id,
            ) if not request.conversation_id else None,
        )

        # Image-generation shortcut (only when DALL-E is configured)
        if self._is_image_generation_request(request.message) and settings.ENABLE_IMAGE_GENERATION:
            try:
                from app.services.dalle_service import dalle_service as _dalle
                if _dalle and _dalle.is_configured:
                    async for chunk in self._handle_image_generation(db, conversation, user, request):
                        yield chunk
                    return
                # DALL-E not configured — fall through so the AI can respond
                # (e.g., use run_python_code + matplotlib for chart requests)
                logger.info("DALL-E not configured; routing image request through normal chat")
            except Exception as _e:
                logger.warning("Image-gen shortcut failed, falling back to chat: %s", _e)

        # Conversation history
        history = await self.get_conversation_messages(db, conversation.id)

        # RAG context
        context: Optional[str] = None
        citations: List[Citation] = []

        # Skip RAG for pure action requests — they don't benefit from document context
        # and the unrelated citations they produce are confusing.
        _msg_lower = request.message.lower().strip()
        _skip_rag_actions = (
            "send an email", "send email", "email to ", "send a message",
            "schedule a ", "book a meeting", "create a meeting", "add to calendar",
            "create a task", "add a task", "delete this", "remove this",
        )
        _is_pure_action = any(_msg_lower.startswith(p) or (len(_msg_lower) < 120 and p in _msg_lower[:80])
                              for p in _skip_rag_actions)

        if request.use_rag and settings.ENABLE_RAG and not _is_pure_action:
            # Enterprise search is WORK-ONLY.  Personal mode must never reach
            # SharePoint / Azure AI Search — that would violate the namespace
            # boundary and leak org data into a personal conversation.
            _use_enterprise = (
                _profile_mode == "work"
                and bool(settings.AZURE_SEARCH_ENDPOINT and settings.AZURE_SEARCH_ADMIN_KEY)
            )
            if _use_enterprise:
                try:
                    from app.services.search.query_pipeline import enterprise_query, EnterpriseSearchResult
                    # Use tenant_id as shared org workspace so all users see indexed content.
                    # Falls back to user's azure_id / DB id for personal-only indexes.
                    _ent_workspace = (
                        settings.effective_tenant_id
                        or _tenant_id
                        or getattr(user, "azure_id", None)
                        or str(user.id)
                    )
                    _user_id = str(user.id)
                    _user_groups = getattr(user, "groups", None) or []
                    logger.info(
                        "Enterprise search ACL context: user_id=%s, user_groups=%s, workspace=%s",
                        _user_id, _user_groups, _ent_workspace
                    )
                    ent_results = await enterprise_query.search(
                        query=request.message,
                        workspace_id=_ent_workspace,
                        context_type="org",
                        user_id=_user_id,
                        user_groups=_user_groups,
                        tenant_id=_tenant_id or settings.effective_tenant_id or _ent_workspace,
                        user_role=str(getattr(user, "role", "") or ""),
                        top_k=max(settings.RAG_TOP_K, 8),
                    )

                    # ── OneDrive real-time search (supplements indexed content) ──
                    # If user has a delegated access token, also search their OneDrive
                    # using the Graph Search API for files not yet indexed
                    if access_token and not getattr(request, "_is_dev_token", False):
                        try:
                            from app.services.connectors.graph_client import GraphClient
                            graph_client = GraphClient(delegated_token=access_token)
                            od_hits = await graph_client.search_files(
                                query=request.message,
                                entity_types=["driveItem"],
                                top=5,
                            )
                            for hit in od_hits:
                                name = hit.get("name", "")
                                web_url = hit.get("webUrl", "")
                                # Skip if already in results
                                if any(r.url == web_url for r in ent_results):
                                    continue
                                # Add as search result
                                ent_results.append(EnterpriseSearchResult(
                                    chunk_id=hit.get("id", ""),
                                    document_title=name,
                                    content=hit.get("_summary", f"File: {name}"),
                                    score=0.5,  # Lower score for real-time results
                                    source_type="onedrive",
                                    url=web_url,
                                    citation={
                                        "source": "OneDrive",
                                        "file": name,
                                        "url": web_url,
                                    },
                                ))
                            if od_hits:
                                logger.info("OneDrive real-time search found %d files", len(od_hits))
                        except Exception as od_e:
                            logger.debug("OneDrive real-time search skipped: %s", od_e)

                    if ent_results:
                        context = enterprise_query.build_context_prompt(ent_results)
                        # CR-3 polish: surface injection-scan stats as an SSE
                        # chunk so the frontend can show a small shield icon
                        # and admins can audit dropped/flagged content.
                        _inj_stats = getattr(
                            enterprise_query, "last_context_stats", {}
                        ) or {}
                        _dropped = int(_inj_stats.get("dropped_injection", 0))
                        _flagged = int(_inj_stats.get("flagged_injection", 0))
                        if _dropped or _flagged:
                            yield StreamChunk(
                                type="injection_detected",
                                data={
                                    "dropped": _dropped,
                                    "flagged": _flagged,
                                    "total_considered": int(
                                        _inj_stats.get(
                                            "total_chunks_considered", 0
                                        )
                                    ),
                                },
                            )
                            # Persistent audit trail (admin dashboard).
                            try:
                                from app.core.logging import log_security_event
                                await log_security_event(
                                    db,
                                    user_id=str(user.id),
                                    action="rag_injection_detected",
                                    event_type="security",
                                    resource_type="rag_retrieval",
                                    details={
                                        "dropped": _dropped,
                                        "flagged": _flagged,
                                        "query": (request.message or "")[:200],
                                    },
                                    success=True,
                                )
                            except Exception as _audit_err:
                                logger.debug(
                                    "Audit log injection failed (non-fatal): %s",
                                    _audit_err,
                                )

                        _seen_citation_keys: set = set()
                        for r in ent_results:
                            _key = r.url or r.document_title
                            if _key and _key in _seen_citation_keys:
                                continue
                            _seen_citation_keys.add(_key)
                            citations.append(Citation(
                                document_id=r.chunk_id,
                                document_title=r.document_title,
                                chunk_id=r.chunk_id,
                                content=(
                                    r.content[:200] + "..."
                                    if len(r.content) > 200 else r.content
                                ),
                                relevance_score=r.score,
                                source_url=r.url,
                            ))
                except Exception as _e:
                    logger.warning("Enterprise search failed, continuing without context: %s", _e)
            else:
                try:
                    search_results = await rag_service.search(
                        request.message,
                        top_k=settings.RAG_TOP_K,
                    )
                    if search_results:
                        context = rag_service.build_context_prompt(search_results)
                        citations = [
                            Citation(
                                document_id=r.document_id,
                                document_title=r.document_title,
                                chunk_id=r.chunk_id,
                                content=r.content[:200] + "..." if len(r.content) > 200 else r.content,
                                relevance_score=r.score,
                                source_url=r.source_url,
                            )
                            for r in search_results
                        ]
                except Exception as e:
                    logger.warning(f"RAG search failed, continuing without context: {e}")

        # ── Public web search (user-initiated, per-request) ───────────────────
        # Called when the user toggled "Web Search" on — uses DuckDuckGo (no key needed).
        # Results are appended to the existing context so enterprise docs take priority.
        if getattr(request, "use_web_search", False):
            try:
                from app.services.connectors.public_web import PublicWebConnector
                _web_workspace = (
                    settings.effective_tenant_id
                    or getattr(user, "azure_id", None)
                    or str(user.id)
                )
                pub_web = PublicWebConnector(workspace_id=_web_workspace)
                web_docs = await pub_web.live_search(
                    request.message, top_k=6, bypass_enabled_check=True
                )
                if web_docs:
                    web_snippets = "\n\n".join(
                        f"[Web] {d.title}\nSource: {d.url}\n{d.content}"
                        for d in web_docs
                        if d.content
                    )
                    if context:
                        context = context + "\n\n---\n\n**Live web search results:**\n" + web_snippets
                    else:
                        context = "**Live web search results:**\n" + web_snippets
                    for d in web_docs:
                        citations.append(Citation(
                            document_id=d.id,
                            document_title=d.title,
                            chunk_id=d.id,
                            content=d.content[:300] if d.content else "",
                            relevance_score=0.7,
                            source_url=d.url,
                        ))
                    logger.info("Web search added %d results for query: %.60s", len(web_docs), request.message)
            except Exception as _web_err:
                logger.warning("Public web search failed, skipping: %s", _web_err)

        # Load layered instructions + skills (non-fatal if DB unavailable)
        _instruction_addon = ""
        _skill_model_pref = None
        if not self._is_mock_session(db):
            try:
                from app.services.instruction_service import instruction_service
                from app.services.skill_service import skill_service

                # Seed built-ins on first use (cheap no-op after first call)
                await instruction_service.seed_builtins(db)
                await skill_service.seed_builtins(db)

                # Load instructions for this user
                _instructions = await instruction_service.get_instructions_for_user(
                    db, user.id, getattr(user, "tenant_id", None)
                )

                # Detect skills from the user message
                _skills = await skill_service.match_skills_for_message(
                    db, request.message, user.id,
                    tenant_id=getattr(user, "tenant_id", None),
                )

                # Compose addon block
                _parts = []
                for instr in _instructions:
                    if instr.get("scope") != "global":  # global ones already baked into SYSTEM_PROMPT
                        _parts.append(f"[{instr['name']}]\n{instr['content']}")
                for sk in _skills:
                    _parts.append(f"[Skill: {sk['name']}]\n{sk['instruction_block']}")
                    if sk.get("model_preference") and not _skill_model_pref:
                        _skill_model_pref = sk["model_preference"]

                _instruction_addon = "\n\n".join(_parts) if _parts else ""
            except Exception as _ie:
                logger.warning("Failed to load instructions/skills: %s", _ie)

        # Build OpenAI messages
        messages = self.build_messages(
            user, conversation, history, request.message, context,
            inline_attachments=request.inline_attachments,
            model=request.model or "gpt-5.2-chat",
        )

        # ── Inject org context block (work mode only) ───────────────────────
        # Prepend the org-specific context block so the LLM is grounded in
        # company data before any user instructions or memory.
        if _profile_mode == "work" and messages and messages[0].get("role") == "system":
            try:
                from app.services.org_context_service import org_context_service
                _org_ctx = await org_context_service.get_context(
                    user_id=str(user.id),
                    tenant_id=_tenant_id,
                )
                if _org_ctx:
                    _org_block = org_context_service.build_prompt_block(_org_ctx)
                    if _org_block:
                        messages[0]["content"] = _org_block + "\n\n" + messages[0]["content"]
            except Exception as _org_err:
                logger.warning("Org context injection failed (non-fatal): %s", _org_err)

        # Append instructions/skills to system message
        if _instruction_addon and messages and messages[0].get("role") == "system":
            messages[0]["content"] += "\n\n" + _instruction_addon

        # ── Inject memory context (three-layer memory system) ────────────────
        if not self._is_mock_session(db):
            try:
                from app.services.memory_service import memory_service
                memory_context = await memory_service.build_memory_context(
                    db=db,
                    user_id=str(user.id),
                    conversation_id=conversation.id,
                    profile_mode=_profile_mode,
                    tenant_id=_tenant_id,
                    current_query=request.message,
                )
                if memory_context and messages and messages[0].get("role") == "system":
                    messages[0]["content"] += "\n\n" + memory_context
            except Exception as _mem_err:
                logger.warning("Failed to load memory context: %s", _mem_err)

        # Inject project context (memories + recent cross-chat history)
        effective_project_id = request.project_id or getattr(conversation, "project_id", None)
        if effective_project_id and not self._is_mock_session(db):
            try:
                proj_context_msg = await self._load_project_context(
                    effective_project_id, conversation.id, db
                )
                if proj_context_msg:
                    # Insert right after the system prompt (index 1)
                    messages.insert(1, proj_context_msg)
            except Exception as proj_err:
                logger.warning(f"Failed to load project context: {proj_err}")

        # Tools — suppress search_documents if enterprise context already loaded
        # (the context is already in the system prompt; no need to search twice)
        _user_session = UserSession(
            mode=_profile_mode,
            user_id=str(user.id),
            tenant_id=_tenant_id,
            access_token=access_token,
        )
        tools = await tool_executor.get_available_tools(user, user_session=_user_session)
        if context and tools:
            tools = [t for t in tools if t.get("function", {}).get("name") != "search_documents"]

        # Collect files to pre-load in the code interpreter sandbox.
        # Includes spreadsheets, PDFs, Word docs, and PPTX — anything where the
        # binary is more useful than extracted text for code-based manipulation.
        _sandbox_passthrough_mimes = {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv",
            "application/csv",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        _sandbox_passthrough_exts = {
            ".xlsx", ".xls", ".csv", ".tsv",
            ".pdf", ".docx", ".doc", ".pptx", ".ppt",
        }
        code_input_files: list = []
        if request.inline_attachments:
            for att in request.inline_attachments:
                if att.raw_base64 and (
                    att.content_type in _sandbox_passthrough_mimes
                    or any(
                        att.filename.lower().endswith(ext)
                        for ext in _sandbox_passthrough_exts
                    )
                ):
                    code_input_files.append({"name": att.filename, "base64": att.raw_base64})

        # Save user message
        user_msg_id = str(uuid.uuid4())
        now = datetime.utcnow()

        if self._is_mock_session(db):
            await _ctx_append(
                conversation.id,
                {"id": user_msg_id, "role": "user",
                 "content": request.message, "model": request.model,
                 "created_at": now},
                _fallback=_in_memory_messages,
            )
        else:
            db.add(Message(
                id=user_msg_id,
                conversation_id=conversation.id,
                role="user",
                content=request.message,
                model=request.model,
            ))
            # ── Early commit: persist conversation + user message before LLM ──
            # This releases the SQLite write lock during the LLM streaming phase
            # (which can take 10-60 s), preventing "database is locked" errors
            # when concurrent requests arrive.
            try:
                await db.commit()
            except Exception as _early_err:
                logger.error("Early commit failed [conv=%s]: %s", conversation.id, _early_err)
                await db.rollback()
                yield StreamChunk(
                    type="error",
                    content="Failed to save your message. Please try again.",
                )
                return

        # Stream response
        full_response = ""
        tool_calls: List[Dict] = []
        tool_results_map: Dict[str, Any] = {}  # tool_call_id → result
        _file_log_ids: Dict[str, str] = {}  # filename → file_log_id
        # Safety net: 'auto' should already be resolved by OutcomeOrchestrator,
        # but if process_chat is called directly fall back to a sensible default.
        _safe_model = (
            "gpt-4.1"
            if not request.model or request.model in ("auto", "")
            else request.model
        )
        actual_model = _safe_model
        # Hoist message ID so GeneratedFileLog can reference it before final commit
        assistant_msg_id = str(uuid.uuid4())

        try:
            from app.services.model_router import model_router

            _req_model = _safe_model
            _is_claude  = _req_model.startswith("claude-")
            _is_gemini  = _req_model.startswith("gemini-")
            _resolved_provider = "openai"  # updated from router_resolved chunk
            _claude_tokens_used = 0

            # ── Budget-based model auto-downgrade ─────────────────────────
            if _budget_warning_pct:
                from app.services.model_router import budget_downgrade_model
                _downgraded = budget_downgrade_model(_req_model, _budget_warning_pct)
                if _downgraded != _req_model:
                    logger.info(
                        "Budget auto-downgrade: %s → %s (usage=%d%%)",
                        _req_model, _downgraded, _budget_warning_pct,
                    )
                    _req_model = _downgraded
                    _is_claude = _req_model.startswith("claude-")
                    _is_gemini = _req_model.startswith("gemini-")

            # ── Claude daily-limit check (before routing) ─────────────────
            if _is_claude:
                from app.services.anthropic_service import anthropic_service as _anth_svc
                from app.services.claude_usage_service import claude_usage_service

                if _anth_svc:
                    allowed, usage = await claude_usage_service.check_allowed(db, user.id)
                    if not allowed:
                        limit = usage["limit"]
                        _is_claude = False
                        _req_model = "gpt-4.1"
                        yield StreamChunk(
                            type="claude_limit_reached",
                            data={
                                "question_count": usage["question_count"],
                                "limit": limit,
                                "fallback_model": _req_model,
                            },
                        )
                        yield StreamChunk(
                            type="content",
                            content=(
                                f"> **Claude daily limit reached** "
                                f"({usage['question_count']}/{limit} questions used today). "
                                f"Switching to {_req_model} for this response.\n\n"
                            ),
                        )
                    else:
                        warn_at = settings.CLAUDE_WARN_AT_REMAINING
                        if warn_at > 0 and 0 < usage["remaining"] <= warn_at:
                            yield StreamChunk(type="claude_usage", data=usage)
                else:
                    # Anthropic not configured — model_router will silently fall back
                    _is_claude = False

            # ── Unified streaming via model router (all providers + failover) ─
            async for chunk in model_router.stream(
                messages,
                model=_req_model,
                user_id=user.id,
                tools=tools if settings.ENABLE_AGENTS else None,
            ):
                if chunk.type == "router_resolved":
                    # Internal chunk — update tracking, never forward to client
                    _resolved_provider = chunk.data.get("provider", "openai")
                    actual_model = chunk.data.get("model", _req_model)
                    _is_claude = _resolved_provider == "anthropic"
                    _is_gemini = _resolved_provider == "gemini"
                    continue
                elif chunk.type == "content":
                    full_response += chunk.content
                    yield chunk
                elif chunk.type == "model_switched":
                    actual_model = (
                        chunk.data.get("to_model", actual_model)
                        if chunk.data else actual_model
                    )
                    yield chunk
                elif chunk.type == "tool_call":
                    tool_calls.append(chunk.data)
                    yield chunk
                elif chunk.type in ("claude_usage", "thinking"):
                    yield chunk
                elif chunk.type == "error":
                    # router_resolved should have fired before this; surface only
                    # genuine unrecoverable errors (e.g. content policy violation)
                    yield chunk
                    return
                elif chunk.type == "done":
                    if _is_claude:
                        _claude_tokens_used = (chunk.data or {}).get("total_tokens", 0)
                    break

            # ── Record Claude usage if Anthropic was the winning provider ──
            if _is_claude:
                try:
                    from app.services.anthropic_service import anthropic_service as _anth_svc2
                    from app.services.claude_usage_service import claude_usage_service as _cu_svc
                    if _anth_svc2:
                        updated = await _cu_svc.record_question(
                            db, user.id, tokens_used=_claude_tokens_used
                        )
                        yield StreamChunk(type="claude_usage", data=updated)
                except Exception as _ue:
                    logger.warning("Failed to record Claude usage: %s", _ue)

            # ── Agentic tool loop ─────────────────────────────────────────────
            # Execute tool calls and run follow-up LLM passes until the model
            # produces a final text response or the round limit is reached.
            # Supports multi-step tasks: e.g. get_inbox → send_email → done.
            _MAX_TOOL_ROUNDS = 5
            _tool_round = 0
            # Build a running message history that accumulates tool round turns
            _agentic_messages = list(messages)

            # Allow Claude and Gemini to use tools now that they support it
            while tool_calls and _tool_round < _MAX_TOOL_ROUNDS and not _is_gemini:
                _tool_round += 1
                _round_tool_calls = list(tool_calls)
                tool_calls = []  # reset for next round

                logger.info(
                    "[agentic] round=%d tools=%s model=%s",
                    _tool_round,
                    [tc["name"] for tc in _round_tool_calls],
                    _req_model,
                )

                # Execute each tool in this round
                for tc in _round_tool_calls:
                    # Signal to frontend which tool is running
                    yield StreamChunk(
                        type="tool_executing",
                        data={"name": tc["name"], "round": _tool_round},
                    )
                    try:
                        result = await tool_executor.execute_tool(
                            tc["name"], tc["arguments"], user,
                            access_token=access_token,
                            input_files=code_input_files if tc["name"] == "run_python_code" else None,
                            user_session=_user_session,
                        )
                        tool_results_map[tc["id"]] = result

                        # Phase 3a (CR-3): if the confirmation gate blocked
                        # this dispatch, surface a dedicated chunk to the
                        # frontend so it can render an approval prompt. We
                        # still feed the result back to the LLM as a tool
                        # result so the model naturally asks the user for
                        # approval in its narration.
                        if isinstance(result, dict) and result.get("requires_confirmation"):
                            yield StreamChunk(
                                type="confirmation_required",
                                data={
                                    "tool_call_id": tc["id"],
                                    "tool": result.get("tool", tc["name"]),
                                    "preview": result.get("preview", {}),
                                    "reason": result.get("reason", "user_confirmation_required"),
                                    "message": result.get("message", ""),
                                },
                            )

                        yield StreamChunk(
                            type="tool_result",
                            data={"tool_call_id": tc["id"], "name": tc["name"], "result": result},
                        )

                        # Email draft: emit structured chunk for frontend card
                        if tc["name"] == "create_draft_email" and result.get("draft_id"):
                            yield StreamChunk(
                                type="email_draft",
                                data={
                                    "draft_id": result["draft_id"],
                                    "to": result.get("to", []),
                                    "subject": result.get("subject", ""),
                                    "body_preview": result.get("body_preview", ""),
                                    "status": "saved",
                                },
                            )

                        # Code interpreter: stream each generated file
                        if tc["name"] == "run_python_code":
                            _code_ok = result.get("success", False)
                            for f in result.get("files", []):
                                out_type = self._classify_output_type(
                                    f["mime_type"], f["name"]
                                )
                                file_log_id = str(uuid.uuid4())
                                _file_log_ids[f["name"]] = file_log_id
                                yield StreamChunk(
                                    type="file_generated",
                                    data={
                                        "file_log_id": file_log_id,
                                        "name": f["name"],
                                        "mime_type": f["mime_type"],
                                        "size": f["size"],
                                        "base64": f["base64"],
                                        "output_type": out_type,
                                    },
                                )
                                if not self._is_mock_session(db):
                                    db.add(GeneratedFileLog(
                                        id=file_log_id,
                                        message_id=assistant_msg_id,
                                        conversation_id=conversation.id,
                                        user_id=user.id,
                                        filename=f["name"],
                                        mime_type=f["mime_type"],
                                        file_size=f["size"],
                                        source_inputs=(
                                            [i["name"] for i in code_input_files]
                                            if code_input_files else None
                                        ),
                                        output_type=out_type,
                                        file_data=f["base64"],
                                    ))
                            # Only surface code output on success;
                            # on failure let the follow-up LLM pass respond.
                            if _code_ok:
                                summary = self._format_code_result(result)
                                if summary:
                                    full_response += summary
                                    yield StreamChunk(
                                        type="content", content=summary
                                    )

                    except Exception as te:
                        logger.error(
                            "Tool execution failed [%s]: %s", tc["name"], te
                        )
                        tool_results_map[tc["id"]] = {"error": str(te)}

                # Append this round's assistant + tool messages to history
                _agentic_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": (
                                    json.dumps(tc["arguments"])
                                    if isinstance(tc["arguments"], dict)
                                    else tc["arguments"]
                                ),
                            },
                        }
                        for tc in _round_tool_calls
                    ],
                })
                for tc in _round_tool_calls:
                    _agentic_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(
                            tool_results_map.get(tc["id"], {"error": "No result"}),
                            default=str,
                        ),
                    })

                # Only skip the follow-up LLM pass when code ran successfully
                # AND no further tool calls were queued.  For any multi-step
                # task the model may queue more tool calls in the same round,
                # so we must NOT break early in that case.
                _all_code_ok = all(
                    tool_results_map.get(tc["id"], {}).get("success", True)
                    for tc in _round_tool_calls
                    if tc["name"] == "run_python_code"
                )
                _only_code_tools = all(
                    tc["name"] == "run_python_code" for tc in _round_tool_calls
                )
                if full_response and _all_code_ok and _only_code_tools and not tool_calls:
                    break

                # Run the next LLM pass.
                # Allow tools again unless this is the last round (force final answer).
                _next_tools = (
                    tools if _tool_round < _MAX_TOOL_ROUNDS else None
                )
                try:
                    # Route all follow-up LLM passes through the unified model router
                    # so tool rounds benefit from the same cross-provider failover.
                    async for chunk in model_router.stream(
                        _agentic_messages,
                        model=actual_model,
                        user_id=user.id,
                        tools=_next_tools,
                    ):
                        if chunk.type == "router_resolved":
                            actual_model = chunk.data.get("model", actual_model)
                            _is_claude = chunk.data.get("provider") == "anthropic"
                            continue
                        elif chunk.type == "content":
                            full_response += chunk.content
                            yield chunk
                        elif chunk.type == "tool_call":
                            tool_calls.append(chunk.data)  # queue for next round
                            yield chunk
                        elif chunk.type == "model_switched":
                            actual_model = (
                                chunk.data.get("to_model", actual_model)
                                if chunk.data else actual_model
                            )
                        elif chunk.type == "error":
                            logger.warning(
                                "[agentic] round=%d error: %s", _tool_round, chunk.content
                            )
                            break
                        elif chunk.type == "done":
                            break
                except Exception as _round_err:
                    logger.warning("[agentic] round=%d failed: %s", _tool_round, _round_err)
                    break

                # Continue only if the model queued NEW tool calls this pass.
                # If this pass produced text with no new tool calls, we're done.
                if full_response and not tool_calls:
                    break

            # ── Never return an empty response ────────────────────────────────
            if not full_response:
                logger.warning(
                    "[%s] Empty response after all passes — generating completion fallback",
                    _corr_id,
                )
                try:
                    _fallback_msgs = _agentic_messages if _tool_round > 0 else list(messages)
                    async for chunk in model_router.stream(
                        _fallback_msgs, model="gpt-4.1", user_id=user.id, tools=None
                    ):
                        if chunk.type == "router_resolved":
                            actual_model = chunk.data.get("model", actual_model)
                            continue
                        elif chunk.type == "content":
                            full_response += chunk.content
                            yield chunk
                        elif chunk.type in ("error", "done"):
                            break
                except Exception as _fallback_err:
                    logger.error("Fallback response failed: %s", _fallback_err)
                # Absolute last resort — never leave the user with silence
                if not full_response:
                    full_response = (
                        "I'm working on your request. Please try again in a moment."
                    )
                    yield StreamChunk(type="content", content=full_response)

            # Filter citations — only emit ones the AI actually used in its response.
            # If the response doesn't reference any source content (e.g. it was an
            # action like sending an email, or the retrieved docs were irrelevant),
            # suppress the citations so the user isn't misled.
            if citations and full_response:
                _resp_lower = full_response.lower()
                _used_indicators = [
                    "according to", "based on the", "the document", "from sharepoint",
                    "from the knowledge", "as mentioned in", "per the document",
                    "the file", "retrieved", "in the report", "the source",
                    "in our documents", "from our knowledge", "the policy",
                    "from armely", "from the website", "the presentation",
                ]
                _phrase_hit = any(p in _resp_lower for p in _used_indicators)
                _title_hit = any(
                    c.document_title and c.document_title.lower()[:30] in _resp_lower
                    for c in citations
                )
                # Also keep citations when the LLM explicitly searched (tool_calls)
                _tool_searched = any(tc.get("name") == "search_documents" for tc in tool_calls)
                if not _phrase_hit and not _title_hit and not _tool_searched:
                    citations = []

            for citation in citations:
                yield StreamChunk(type="citation", data=citation.model_dump())

            # ── Strip [MEMORY_UPDATE] blocks before persistence ──────────────
            # The AI may include internal memory directives in its response.
            # These must NEVER be persisted in user-visible message content or
            # shown in the chat history.  We process them (below) then strip.
            _raw_response = full_response
            if full_response and "[MEMORY_UPDATE]" in full_response:
                from app.services.memory_service import memory_service
                full_response = memory_service.strip_memory_blocks(full_response)
                # Tell the frontend to replace the displayed content with the
                # clean version (the streamed chunks included the raw blocks).
                if full_response != _raw_response:
                    yield StreamChunk(
                        type="content_replace",
                        content=full_response,
                    )

            # Save assistant message
            tokens_used = openai_service.count_tokens(full_response)
            # assistant_msg_id was hoisted before the tool loop so GeneratedFileLog
            # records could reference it; reuse it here (do NOT reassign)

            # Collect generated-file metadata for persistence (no base64 in DB)
            _file_meta: List[Dict] = []
            for tc in tool_calls:
                if tc.get("name") == "run_python_code":
                    for f in tool_results_map.get(tc.get("id", ""), {}).get("files", []):
                        _file_meta.append({
                            "name": f["name"],
                            "mime_type": f["mime_type"],
                            "size": f["size"],
                            "output_type": self._classify_output_type(
                                f["mime_type"], f["name"]
                            ),
                            "file_log_id": _file_log_ids.get(f["name"]),
                        })

            if self._is_mock_session(db):
                await _ctx_append(
                    conversation.id,
                    {"id": assistant_msg_id, "role": "assistant",
                     "content": full_response, "model": actual_model,
                     "created_at": datetime.utcnow()},
                    _fallback=_in_memory_messages,
                )
                # Auto-title on first turn
                if len(history) == 0 and full_response:
                    _in_memory_conversations[conversation.id]["title"] = (
                        request.message[:50] + "..." if len(request.message) > 50 else request.message
                    )
                _in_memory_conversations[conversation.id]["updated_at"] = datetime.utcnow()
            else:
                is_first_message = len(history) == 0
                db.add(Message(
                    id=assistant_msg_id,
                    conversation_id=conversation.id,
                    role="assistant",
                    content=full_response,
                    model=actual_model,
                    tokens_used=tokens_used,
                    citations=[c.model_dump() for c in citations] if citations else None,
                    tool_calls=tool_calls if tool_calls else None,
                    # Store file metadata so history can show "re-run to download"
                    tool_results=(
                        {"__generated_file_meta__": _file_meta} if _file_meta else None
                    ),
                ))

                if is_first_message and full_response:
                    conversation.title = (
                        request.message[:50] + "..." if len(request.message) > 50 else request.message
                    )

                conversation.updated_at = datetime.utcnow()

                # Track usage
                prompt_tokens = openai_service.count_tokens(json.dumps(messages, default=str))
                _total_tokens = prompt_tokens + tokens_used
                db.add(ModelUsage(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    model=actual_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=tokens_used,
                    total_tokens=_total_tokens,
                    conversation_id=conversation.id,
                ))

                # Update user's daily token counter. Use a targeted UPDATE so we
                # don't need to load the full User object in this hot path.
                # Also auto-reset the counter if last_token_reset was a previous day.
                try:
                    from app.models.models import User as _UserModel
                    _today = datetime.utcnow().date()
                    # Reset if stale, then increment — done in two cheap UPDATE calls.
                    await db.execute(
                        sa_update(_UserModel)
                        .where(
                            _UserModel.azure_id == user.id,
                            func.date(_UserModel.last_token_reset) < str(_today),
                        )
                        .values(tokens_used_today=0, last_token_reset=datetime.utcnow())
                    )
                    await db.execute(
                        sa_update(_UserModel)
                        .where(_UserModel.azure_id == user.id)
                        .values(tokens_used_today=_UserModel.tokens_used_today + _total_tokens)
                    )
                except Exception as _te:
                    logger.warning("Failed to update tokens_used_today: %s", _te)

                try:
                    await db.commit()
                    # Update budget cache after successful commit.
                    try:
                        from app.core.budget_cache import increment_usage_cache as _incr_cache
                        _cost_hint = float(tokens_used) * 0.000002  # rough estimate; exact cost handled by billing
                        await _incr_cache(user.id, _total_tokens, _cost_hint)
                    except Exception as _be:
                        logger.debug("budget_cache increment skipped: %s", _be)
                except Exception as commit_err:
                    logger.error(
                        "Failed to commit assistant message [corr=%s conv=%s]: %s",
                        _corr_id, conversation.id, commit_err,
                    )
                    try:
                        await db.rollback()
                    except Exception as _rb_err:
                        logger.warning("Rollback after commit failure [corr=%s]: %s", _corr_id, _rb_err)

            # Kick off background project-memory extraction (non-blocking)
            if effective_project_id and not request.is_private and full_response:
                async def _safe_extract():
                    try:
                        await self._extract_project_memory(
                            effective_project_id,
                            conversation.id,
                            request.message,
                            full_response,
                        )
                    except Exception as _mem_err:
                        logger.warning(
                            "Background project-memory extraction failed: %s", _mem_err
                        )
                asyncio.create_task(_safe_extract())

            # ── Process memory updates from AI response ──────────────────────
            # Parse and apply any [MEMORY_UPDATE] blocks the AI included
            if _raw_response and not request.is_private:
                try:
                    from app.services.memory_service import memory_service
                    await memory_service.process_memory_updates(
                        db=db,
                        user_id=str(user.id),
                        assistant_response=_raw_response,
                        source_conversation_id=conversation.id,
                        profile_scope="global" if _profile_mode == "personal" else _profile_mode,
                        tenant_id=_tenant_id,
                    )
                except Exception as _mem_err:
                    logger.warning("Failed to process memory updates: %s", _mem_err)

            # ── Update session memory (Layer 2) ──────────────────────────────
            # Summarise the latest exchange and persist it so the next turn
            # picks up compressed context even when the conversation is long.
            if full_response and not self._is_mock_session(db) and not request.is_private:
                try:
                    from app.services.memory_service import memory_service
                    _all_msgs = await self.get_conversation_messages(db, conversation.id)
                    _msg_count = len(_all_msgs)
                    # Build a lightweight summary from the last exchange
                    _summary_parts = []
                    _key_facts: list[str] = []
                    # Use the last few messages (up to 6) for the summary
                    for _m in _all_msgs[-6:]:
                        _role_label = "User" if _m.role == "user" else "Assistant"
                        _snippet = (_m.content or "")[:300]
                        if _snippet:
                            _summary_parts.append(f"{_role_label}: {_snippet}")
                    _summary = "\n".join(_summary_parts) if _summary_parts else full_response[:500]
                    await memory_service.update_session_memory(
                        db=db,
                        conversation_id=conversation.id,
                        user_id=str(user.id),
                        summary=_summary,
                        key_facts=_key_facts or None,
                        last_message_id=assistant_msg_id,
                        message_count=_msg_count,
                        profile_mode=_profile_mode,
                        tenant_id=_tenant_id,
                    )
                except Exception as _sess_err:
                    logger.warning("Failed to update session memory: %s", _sess_err)

            yield StreamChunk(
                type="done",
                data={
                    "conversation_id": conversation.id,
                    "message_id": assistant_msg_id,
                    "tokens_used": tokens_used,
                },
            )

        except Exception as e:
            logger.error(
                "Chat processing error [corr=%s user=%s]: %s",
                _corr_id, getattr(user, "id", "unknown"), e,
                exc_info=True,
            )
            try:
                if not self._is_mock_session(db):
                    await db.rollback()
            except Exception as _rb_err:
                logger.warning("Rollback after chat error failed [corr=%s]: %s", _corr_id, _rb_err)
            from app.core.error_classifier import classify_chat_error
            _err_code, _err_msg = classify_chat_error(e, _corr_id)
            yield StreamChunk(
                type="error",
                content=_err_msg,
                error_code=_err_code,
                correlation_id=_corr_id,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Conversation CRUD
    # ─────────────────────────────────────────────────────────────────────────

    async def list_conversations(
        self,
        db: AsyncSession,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        archived: bool = False,
        context_type: Optional[str] = None,
        profile_context=None,  # app.core.profile_context.ProfileContext | None
    ) -> List[ConversationResponse]:
        """List user's conversations with message counts.

        Pass a ProfileContext to enforce profile-level namespace isolation.
        Falls back to context_type ('org'|'personal') when profile_context is None.
        """
        # Normalise: 'org' is legacy alias for 'work'
        if context_type == "org":
            context_type = "work"

        # In-memory fallback
        if self._is_mock_session(db):
            convs = [
                v for v in _in_memory_conversations.values()
                if v.get("user_id") == user_id
                and v.get("is_archived", False) == archived
                and not v.get("is_private", False)
            ]
            if profile_context is not None:
                pm = profile_context.profile_mode
                convs = [c for c in convs if c.get("profile_mode", c.get("context_type", "personal")) == pm]
            elif context_type:
                convs = [c for c in convs if c.get("profile_mode", c.get("context_type", "personal")) == context_type]
            convs.sort(key=lambda c: c.get("updated_at", datetime.utcnow()), reverse=True)
            convs = convs[offset: offset + limit]
            return [
                ConversationResponse(
                    id=c["id"],
                    title=c.get("title", "New Conversation"),
                    model=c.get("model", "gpt-5.2-chat"),
                    system_prompt=c.get("system_prompt"),
                    is_archived=c.get("is_archived", False),
                    project_id=c.get("project_id"),
                    message_count=len(_in_memory_messages.get(c["id"], [])),  # fallback count
                    created_at=c.get("created_at", datetime.utcnow()),
                    updated_at=c.get("updated_at", datetime.utcnow()),
                )
                for c in convs
            ]

        # DB: single query — owned conversations + shared conversations (ChatMember)
        from app.models.models import ChatMember
        from sqlalchemy import or_

        msg_count_subq = (
            select(Message.conversation_id, func.count(Message.id).label("msg_count"))
            .group_by(Message.conversation_id)
            .subquery()
        )

        # Conversations owned by user OR where user is a ChatMember
        member_ids_subq = select(ChatMember.conversation_id).where(ChatMember.user_id == user_id)

        where_clauses = [
            or_(Conversation.user_id == user_id, Conversation.id.in_(member_ids_subq)),
            Conversation.is_archived == archived,
            Conversation.is_private == False,  # noqa: E712
        ]

        # GDPR/SOC2 Sprint 2: hide soft-deleted conversations when the flag
        # is on. No-op when ENABLE_SOFT_DELETE=false.
        from app.core.soft_delete import is_soft_delete_enabled
        if is_soft_delete_enabled():
            where_clauses.append(Conversation.deleted_at.is_(None))

        # Apply profile namespace filter — ProfileContext takes precedence over
        # the legacy context_type query param.
        if profile_context is not None:
            where_clauses += profile_context.where_clauses(Conversation)
        elif context_type:
            # Normalise 'org' → 'work' for backward compat
            _pm = "work" if context_type == "org" else context_type
            where_clauses.append(Conversation.profile_mode == _pm)

        result = await db.execute(
            select(Conversation, msg_count_subq.c.msg_count)
            .outerjoin(msg_count_subq, Conversation.id == msg_count_subq.c.conversation_id)
            .where(*where_clauses)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )

        rows = result.all()
        return [
            ConversationResponse(
                id=conv.id,
                title=conv.title,
                model=conv.model,
                system_prompt=conv.system_prompt,
                is_archived=conv.is_archived,
                is_private=getattr(conv, "is_private", False),
                project_id=conv.project_id,
                context_type=getattr(conv, "context_type", "personal"),
                message_count=int(msg_count or 0),
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )
            for conv, msg_count in rows
        ]

    async def get_conversation_detail(
        self,
        db: AsyncSession,
        conversation_id: str,
        user_id: str,
        admin_override: bool = False,
    ) -> Optional[tuple]:
        """Return (Conversation, List[Message]) or None — handles MockSession.
        admin_override=True skips user_id filter (for admin access to private chats)."""
        if self._is_mock_session(db):
            conv_data = _in_memory_conversations.get(conversation_id)
            if not conv_data or (not admin_override and conv_data.get("user_id") != user_id):
                return None
            conv = Conversation(
                id=conv_data["id"],
                user_id=conv_data.get("user_id", user_id),
                title=conv_data.get("title", "New Conversation"),
                model=conv_data.get("model", "gpt-5.2-chat"),
                system_prompt=conv_data.get("system_prompt"),
                is_archived=conv_data.get("is_archived", False),
                is_private=conv_data.get("is_private", False),
                private_expires_at=conv_data.get("private_expires_at"),
                created_at=conv_data.get("created_at", datetime.utcnow()),
                updated_at=conv_data.get("updated_at", datetime.utcnow()),
            )
            messages = await self.get_conversation_messages(db, conversation_id)
            return conv, messages

        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return None

        # Access check: owner, admin override, or a ChatMember record
        if not admin_override and conversation.user_id != user_id:
            from app.models.models import ChatMember
            member = await db.scalar(
                select(ChatMember).where(
                    ChatMember.conversation_id == conversation_id,
                    ChatMember.user_id == user_id,
                )
            )
            if not member:
                return None

        messages = await self.get_conversation_messages(db, conversation_id)
        return conversation, messages

    async def update_conversation_data(
        self,
        db: AsyncSession,
        conversation_id: str,
        user_id: str,
        updates: dict,
    ) -> Optional[Conversation]:
        """Update conversation fields — handles MockSession. Returns updated Conversation or None."""
        if self._is_mock_session(db):
            conv_data = _in_memory_conversations.get(conversation_id)
            if not conv_data or conv_data.get("user_id") != user_id:
                return None
            _in_memory_conversations[conversation_id].update(updates)
            _in_memory_conversations[conversation_id]["updated_at"] = datetime.utcnow()
            d = _in_memory_conversations[conversation_id]
            return Conversation(
                id=d["id"],
                user_id=user_id,
                title=d.get("title", "New Conversation"),
                model=d.get("model", "gpt-5.2-chat"),
                system_prompt=d.get("system_prompt"),
                is_archived=d.get("is_archived", False),
                created_at=d.get("created_at", datetime.utcnow()),
                updated_at=d["updated_at"],
            )

        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return None
        for key, value in updates.items():
            setattr(conversation, key, value)
        conversation.updated_at = datetime.utcnow()
        await db.flush()
        return conversation

    async def delete_conversation(
        self,
        db: AsyncSession,
        conversation_id: str,
        user_id: str,
    ) -> bool:
        """Delete a conversation."""

        if self._is_mock_session(db):
            if conversation_id in _in_memory_conversations:
                if _in_memory_conversations[conversation_id].get("user_id") == user_id:
                    del _in_memory_conversations[conversation_id]
                    await _ctx_invalidate(conversation_id, _fallback=_in_memory_messages)
                    return True
            return False

        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return False

        await db.delete(conversation)
        await db.commit()
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Shared conversations
    # ─────────────────────────────────────────────────────────────────────────

    def _conv_to_response(
        self, conv: "Conversation", msg_count: int = 0
    ) -> "ConversationResponse":
        return ConversationResponse(
            id=conv.id,
            title=conv.title,
            model=conv.model,
            system_prompt=conv.system_prompt,
            is_archived=conv.is_archived,
            is_private=getattr(conv, "is_private", False),
            project_id=conv.project_id,
            context_type=getattr(conv, "context_type", "personal"),
            message_count=msg_count,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )

    async def list_shared_with_me(
        self, db: AsyncSession, user_id: str
    ) -> List[ConversationResponse]:
        """Conversations shared with this user (they are a member, not owner)."""
        if self._is_mock_session(db):
            return []

        from app.models.models import ChatMember

        msg_count_subq = (
            select(Message.conversation_id, func.count(Message.id).label("msg_count"))
            .group_by(Message.conversation_id)
            .subquery()
        )

        from app.core.soft_delete import is_soft_delete_enabled
        _shared_where = [
            ChatMember.user_id == user_id,
            Conversation.user_id != user_id,
            Conversation.is_archived == False,  # noqa: E712
            Conversation.is_private == False,  # noqa: E712
        ]
        if is_soft_delete_enabled():
            _shared_where.append(Conversation.deleted_at.is_(None))

        result = await db.execute(
            select(Conversation, msg_count_subq.c.msg_count)
            .join(ChatMember, ChatMember.conversation_id == Conversation.id)
            .outerjoin(
                msg_count_subq, Conversation.id == msg_count_subq.c.conversation_id
            )
            .where(*_shared_where)
            .order_by(Conversation.updated_at.desc())
        )
        return [
            self._conv_to_response(conv, int(mc or 0))
            for conv, mc in result.all()
        ]

    async def list_shared_by_me(
        self, db: AsyncSession, user_id: str
    ) -> List[ConversationResponse]:
        """Conversations owned by this user that have at least one other member."""
        if self._is_mock_session(db):
            return []

        from app.models.models import ChatMember
        from sqlalchemy import exists

        has_members = (
            select(ChatMember.id)
            .where(
                ChatMember.conversation_id == Conversation.id,
                ChatMember.user_id != user_id,
            )
            .limit(1)
        )

        msg_count_subq = (
            select(Message.conversation_id, func.count(Message.id).label("msg_count"))
            .group_by(Message.conversation_id)
            .subquery()
        )

        result = await db.execute(
            select(Conversation, msg_count_subq.c.msg_count)
            .outerjoin(
                msg_count_subq, Conversation.id == msg_count_subq.c.conversation_id
            )
            .where(
                Conversation.user_id == user_id,
                Conversation.is_archived == False,  # noqa: E712
                Conversation.is_private == False,  # noqa: E712
                exists(has_members),
            )
            .order_by(Conversation.updated_at.desc())
        )
        return [
            self._conv_to_response(conv, int(mc or 0))
            for conv, mc in result.all()
        ]


# Singleton instance
try:
    chat_service = ChatService()
except Exception as e:
    logger.warning(f"Failed to initialize ChatService: {e}")
    chat_service = None
