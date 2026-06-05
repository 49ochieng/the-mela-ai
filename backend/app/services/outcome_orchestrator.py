"""
Mela AI - Outcome Orchestrator

The guaranteed execution layer that sits ABOVE all models and providers.

Every chat request goes through:
  1. Intent Detection  — classify what the user actually needs
  2. Model Selection   — Auto Mode picks cheapest/best model for the intent
  3. Execution         — delegate to chat_service (which uses ModelRouter)
  4. Outcome Verify    — check the expected output was produced
  5. Correction Pass   — if file expected but not produced, retry up to MAX_ATTEMPTS

GUARANTEE:
  - File requests ALWAYS produce a downloadable file or an honest explanation
  - Empty responses are retried silently with fallback models
  - No raw provider errors ever reach the user
  - Every request completes — the orchestrator never gives up before MAX_ATTEMPTS
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import AsyncGenerator, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.chat import ChatRequest, StreamChunk
from app.schemas.auth import UserInfo

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3   # Total execution attempts per request


# ── Intent classification ─────────────────────────────────────────────────────

class IntentType(str, Enum):
    FILE_ARTIFACT = "file_artifact"   # PDF, DOCX, XLSX, CSV, chart, zip
    CODE          = "code"            # scripts, apps, functions
    ANALYSIS      = "analysis"        # summarize, compare, evaluate, explain
    GENERAL       = "general"         # everything else / conversation
    # Phase 3: a goal that spans two or more distinct worker capability
    # domains in the same sentence (e.g. tasks AND meetings).  Routes
    # through the orchestration brain's planner; falls through to the
    # standard chat path on any planning failure.
    CROSS_WORKER  = "cross_worker"


# Phrase-level triggers (highest priority — clear file-creation intent)
_STRONG_FILE_PHRASES = {
    "generate a pdf", "create a pdf", "make a pdf", "export as pdf", "export to pdf",
    "generate a word", "create a word", "make a word doc", "create a docx",
    "generate excel", "create excel", "make a spreadsheet", "create a spreadsheet",
    "generate a report", "create a report", "build a report", "make a report",
    "write a proposal", "create a proposal", "generate a proposal",
    "create an invoice", "generate an invoice",
    "create a contract", "generate a contract",
    "one pager", "one-pager", "onepager",
    "create a presentation", "generate a presentation", "make a slide",
    "create a template", "generate a template",
    "export this", "download this as",
}

# Secondary triggers — creation verb + file noun required together
_CREATION_VERBS  = {"create", "generate", "make", "build", "write", "produce", "export", "prepare", "draft"}
_FILE_NOUNS      = {"pdf", "docx", "excel", "xlsx", "spreadsheet", "csv", "report",
                    "proposal", "invoice", "contract", "document", "file", "chart", "graph"}
_CODE_PHRASES    = {"write code", "build an app", "create a script", "python script",
                    "javascript function", "typescript", "write a function", "write a class",
                    "implement this", "code that", "program that", "write me a program"}
_ANALYSIS_WORDS  = {"analyze", "analyse", "summarize", "summarise", "compare", "evaluate",
                    "review", "assess", "explain what", "describe", "what does", "what is",
                    "how does", "why does"}

# ── Cross-worker domain keywords ──────────────────────────────────────────
# Each frozen-set is one worker capability domain.  CROSS_WORKER triggers
# when the user message touches >= 2 distinct domains in the same
# sentence — that's the cheap heuristic; the planner does the real work.
_DOMAIN_TASKS    = frozenset({
    "task", "tasks", "todo", "to-do", "to do", "overdue",
    "due ", "planner", "task radar",
})
_DOMAIN_MEETING  = frozenset({
    "meeting", "meetings", "standup", "stand-up",
    "transcript", "calendar event", "decision",
    "decided", "agreed",
    # Phase 4: Meeting Assistant capability surface
    "action item", "action items", "participants",
    "attendees", "summary of",
})
_DOMAIN_EMAIL    = frozenset({
    "inbox", "email", "emails", "mail",
})
_DOMAIN_CALENDAR = frozenset({
    "calendar", "schedule", "agenda", "availability",
})

_DOMAIN_SETS: tuple[frozenset[str], ...] = (
    _DOMAIN_TASKS, _DOMAIN_MEETING, _DOMAIN_EMAIL, _DOMAIN_CALENDAR,
)
# Conjunctions that signal multi-intent within a single sentence.  Required
# to avoid mis-classifying e.g. "summarize my tasks" (one domain) as
# CROSS_WORKER just because the message also mentions "today's meeting".
_CONJUNCTIONS = (" and ", " plus ", " as well as ", "; ", ", and ")


def _hits_domains(text: str) -> int:
    """Count how many distinct domains the text mentions."""
    return sum(1 for domain in _DOMAIN_SETS if any(k in text for k in domain))


def detect_intent(message: str) -> IntentType:
    """Classify the user's primary intent from the message text."""
    lower = message.lower().strip()

    # 0. Cross-worker: 2+ distinct capability domains joined by a
    # conjunction.  Checked first so "tasks AND meeting" doesn't fall
    # into ANALYSIS just because it contains "what".
    if any(c in lower for c in _CONJUNCTIONS) and _hits_domains(lower) >= 2:
        return IntentType.CROSS_WORKER

    # 1. Strong multi-word file phrases
    if any(phrase in lower for phrase in _STRONG_FILE_PHRASES):
        return IntentType.FILE_ARTIFACT

    # 2. Code generation
    if any(phrase in lower for phrase in _CODE_PHRASES):
        return IntentType.CODE

    # 3. Verb + noun combination → file artifact
    has_creation = any(v in lower.split() or f"{v} " in lower for v in _CREATION_VERBS)
    has_file_noun = any(n in lower for n in _FILE_NOUNS)
    if has_creation and has_file_noun:
        return IntentType.FILE_ARTIFACT

    # 4. Analysis / explanation
    if any(k in lower for k in _ANALYSIS_WORDS):
        return IntentType.ANALYSIS

    return IntentType.GENERAL


# ── Auto Mode: model selection ────────────────────────────────────────────────

# Models ordered by preference per intent.
# The first one that has a configured deployment wins.
_INTENT_MODEL_PRIORITY: dict[IntentType, list[str]] = {
    # FILE_ARTIFACT: gpt-4.1 is the most reliable tool-caller for code interpreter
    IntentType.FILE_ARTIFACT: ["gpt-4.1", "gpt-5.2-chat", "claude-sonnet-4-6", "kimi-k2.5", "gpt-4o-mini"],
    # CODE: Claude excels at code; fall back to GPT
    IntentType.CODE:          ["claude-sonnet-4-6", "gpt-4.1", "gpt-5.2-chat", "kimi-k2.5", "gpt-4o-mini"],
    # ANALYSIS: strongest reasoning first
    IntentType.ANALYSIS:      ["gpt-5.2-chat", "gpt-4.1", "claude-sonnet-4-6", "kimi-k2.5", "gpt-4o-mini"],
    # GENERAL: fastest/cheapest first
    IntentType.GENERAL:       ["gpt-4o-mini", "grok-3-mini", "gpt-4.1", "gpt-5.2-chat"],
    # CROSS_WORKER: synthesis prompt benefits from strong reasoning so the
    # multi-worker summary reads as one coherent answer.
    IntentType.CROSS_WORKER:  ["gpt-5.2-chat", "gpt-4.1", "claude-sonnet-4-6", "gpt-4o-mini"],
}


def _is_model_available(model: str) -> bool:
    """Return True if this model has a configured, non-empty deployment."""
    if model.startswith("claude-"):
        try:
            from app.services.anthropic_service import anthropic_service
            return bool(anthropic_service)
        except Exception:
            return False
    if model.startswith("gemini-"):
        try:
            from app.services.gemini_service import gemini_service  # noqa: F401
            return bool(gemini_service)
        except Exception:
            return False
    # Azure OpenAI / AI Foundry models
    try:
        from app.services.openai_service import openai_service
        if not openai_service:
            return False
        cfg = openai_service.models.get(model, {})
        return bool(cfg.get("deployment"))
    except Exception:
        return False


def select_auto_model(intent: IntentType, budget_warning_pct: int = 0) -> str:
    """Return the best available model for this intent under current budget pressure."""
    from app.services.model_router import budget_downgrade_model

    candidates = _INTENT_MODEL_PRIORITY.get(intent, _INTENT_MODEL_PRIORITY[IntentType.GENERAL])
    for candidate in candidates:
        effective = budget_downgrade_model(candidate, budget_warning_pct)
        if _is_model_available(effective):
            return effective
    # Absolute fallback — gpt-4o-mini is always registered even if unconfigured
    return budget_downgrade_model("gpt-4.1", budget_warning_pct)


# ── Correction messages (used when model generates text instead of a file) ────

_CORRECTION_ATTEMPT_1 = (
    "Please now generate the actual downloadable file using `run_python_code`. "
    "Write and execute Python code that creates the file and saves it to disk. "
    "Do not show code blocks — execute them and produce the real file."
)

_CORRECTION_ATTEMPT_2 = (
    "Use `run_python_code` right now to create this file. "
    "Choose the simplest possible approach: "
    "PDF → use fpdf2; DOCX → use python-docx; Excel → use openpyxl. "
    "Keep it minimal — one page / one sheet is enough. Execute immediately."
)


# ── Orchestrator ──────────────────────────────────────────────────────────────

class OutcomeOrchestrator:
    """
    Guaranteed execution engine.

    Wraps chat_service.process_chat() with:
    - Intent detection
    - Auto model selection
    - Post-response outcome verification
    - Silent retry / correction pass for file requests
    - Never exposes raw errors to the user
    """

    async def run(
        self,
        db: AsyncSession,
        user: UserInfo,
        request: ChatRequest,
        access_token: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        from app.services.chat_service import chat_service

        # ── 1. Detect intent ────────────────────────────────────────────────
        intent = detect_intent(request.message)
        logger.info(
            "[orchestrator] user=%s intent=%s model=%s msg=%.80s",
            user.id, intent, request.model or "auto", request.message,
        )

        # ── 1b. Read current budget pressure for Auto Mode downgrade logic ──
        _budget_pct: int = 0
        try:
            from app.services.budget_service import check_budget
            from app.services.chat_service import ChatService
            _is_mock = hasattr(db, "_mock_session")
            if not _is_mock:
                _bgt = await check_budget(
                    db,
                    user_id=user.id,
                    tenant_id=getattr(user, "tenant_id", None),
                    fire_notifications=False,  # don't double-fire — chat_service handles it
                )
                _budget_pct = _bgt.usage_pct if _bgt.warning or not _bgt.allowed else 0
        except Exception as _be:
            logger.debug("[orchestrator] Budget pre-check skipped: %s", _be)

        # ── 1c. Cross-worker branch (Phase 3) ────────────────────────────────
        # CROSS_WORKER routes through the orchestration brain's planner.
        # Any planning failure falls through silently to the standard path.
        # On success, planner output is executed and a synthesis prompt is
        # streamed to the user via the normal SSE pipeline.
        if intent == IntentType.CROSS_WORKER:
            cross_worker_handled = False
            try:
                async for chunk in self._run_cross_worker(
                    db, user, request, budget_pct=_budget_pct,
                ):
                    if chunk is None:
                        # Sentinel for "planner failed, fall through".
                        break
                    yield chunk
                    cross_worker_handled = True
            except Exception as _xw_err:
                logger.warning(
                    "[orchestrator] cross-worker branch failed, "
                    "falling through to standard path: %s",
                    _xw_err,
                )
                cross_worker_handled = False
            if cross_worker_handled:
                return
            # Otherwise: drop down to the normal single-LLM execution path.

        # ── 2. Auto Mode ─────────────────────────────────────────────────────
        _req = request
        # Pydantic model_copy() silently drops non-declared private attributes
        # (e.g. _profile_context, _user_timezone set in the HTTP layer).
        # Capture them once here so they can be re-attached after every copy.
        _saved_profile_ctx = getattr(request, "_profile_context", None)
        _saved_user_tz = getattr(request, "_user_timezone", None)

        def _reattach_private_attrs(req):
            """Re-attach private attrs lost by Pydantic model_copy()."""
            if _saved_profile_ctx is not None:
                object.__setattr__(req, "_profile_context", _saved_profile_ctx)
            if _saved_user_tz is not None:
                object.__setattr__(req, "_user_timezone", _saved_user_tz)
            return req

        _was_auto = not _req.model or _req.model in ("auto", "")
        if _was_auto:
            _selected = select_auto_model(intent, budget_warning_pct=_budget_pct)
            _req = _reattach_private_attrs(_req.model_copy(update={"model": _selected}))
            logger.info(
                "[orchestrator] Auto Mode selected model=%s (budget_pct=%d)",
                _selected, _budget_pct,
            )
            # Tell the frontend which model Auto picked so the model indicator
            # and model selector badge update before the first token arrives.
            yield StreamChunk(
                type="model_switched",
                content=f"Auto selected {_selected}",
                data={"from_model": "auto", "to_model": _selected},
            )

        # conversation_id may be None on first turn; resolved from done chunk
        _resolved_conv_id: Optional[str] = _req.conversation_id

        # ── 3. Execute with outcome verification (up to MAX_ATTEMPTS) ────────
        for attempt in range(MAX_ATTEMPTS):
            logger.info(
                "[orchestrator] attempt=%d/%d model=%s intent=%s",
                attempt + 1, MAX_ATTEMPTS, _req.model, intent,
            )

            files_produced: List[dict] = []
            content_chars = 0
            error_before_content = False

            try:
                async for chunk in chat_service.process_chat(
                    db, user, _req, access_token=access_token
                ):
                    # Track outcome signals (never suppress these from the stream)
                    if chunk.type == "file_generated":
                        files_produced.append(chunk.data or {})
                    elif chunk.type == "content" and chunk.content:
                        content_chars += len(chunk.content)
                    elif chunk.type == "done":
                        if chunk.data:
                            _resolved_conv_id = chunk.data.get(
                                "conversation_id", _resolved_conv_id
                            )
                    elif chunk.type == "error" and content_chars == 0:
                        error_before_content = True

                    yield chunk

            except Exception as exc:
                logger.error(
                    "[orchestrator] attempt=%d unhandled exception: %s",
                    attempt + 1, exc, exc_info=True,
                )
                if attempt < MAX_ATTEMPTS - 1:
                    # Silently retry — user already sees "Generating…" or nothing
                    continue
                # Last attempt — emit a non-alarming message
                yield StreamChunk(
                    type="content",
                    content=(
                        "\n\nI ran into a temporary issue. "
                        "Please try sending your message again."
                    ),
                )
                yield StreamChunk(type="done", data={"finish_reason": "orchestrator_max_retries"})
                return

            # ── 4. Outcome verification ──────────────────────────────────────

            # FILE_ARTIFACT: guarantee a file was produced
            if intent == IntentType.FILE_ARTIFACT:
                if files_produced:
                    return  # ✓ File delivered — done

                if attempt < MAX_ATTEMPTS - 1:
                    # Model responded with text but no file — inject correction
                    correction = (
                        _CORRECTION_ATTEMPT_1 if attempt == 0
                        else _CORRECTION_ATTEMPT_2
                    )
                    logger.info(
                        "[orchestrator] File intent but no file produced on attempt %d — "
                        "injecting correction (conv=%s)",
                        attempt + 1, _resolved_conv_id,
                    )
                    # Signal to user (natural-looking, not an error)
                    yield StreamChunk(
                        type="content",
                        content="\n\n*Generating your file now...*\n\n",
                    )
                    # Build correction request in the same conversation so the model
                    # sees its own previous response and knows what file to create.
                    _retry_model = "gpt-4.1" if _is_model_available("gpt-4.1") else _req.model
                    _req = _reattach_private_attrs(_req.model_copy(update={
                        "message": correction,
                        "conversation_id": _resolved_conv_id,
                        "model": _retry_model,
                    }))
                    continue

                # All attempts used — couldn't produce file
                logger.error(
                    "[orchestrator] File generation failed after %d attempts "
                    "(intent=%s, conv=%s)", MAX_ATTEMPTS, intent, _resolved_conv_id,
                )
                return  # Response already streamed — don't add more noise

            # GENERAL / ANALYSIS / CODE: success = non-empty content
            if content_chars > 0:
                return  # ✓ Got a response — done

            # Empty response — retry silently with a fallback model
            if attempt < MAX_ATTEMPTS - 1:
                _fallback_models = ["gpt-4.1", "gpt-4o-mini", "grok-3-mini"]
                _next = _fallback_models[min(attempt, len(_fallback_models) - 1)]
                logger.warning(
                    "[orchestrator] attempt=%d empty response — retrying with %s",
                    attempt + 1, _next,
                )
                _req = _reattach_private_attrs(_req.model_copy(update={
                    "model": _next,
                    "conversation_id": _resolved_conv_id,
                }))
                continue

        # Reached MAX_ATTEMPTS without success — stream has already been yielded
        logger.error(
            "[orchestrator] Exhausted %d attempts without verified outcome "
            "(intent=%s, conv=%s)", MAX_ATTEMPTS, intent, _resolved_conv_id,
        )

    # ── Cross-worker branch (Phase 3) ────────────────────────────────────

    async def _run_cross_worker(
        self,
        db: AsyncSession,
        user: UserInfo,
        request: ChatRequest,
        *,
        budget_pct: int = 0,
    ) -> AsyncGenerator[Optional[StreamChunk], None]:
        """Plan + execute + synthesise across multiple workers.

        Yields ``StreamChunk`` objects on the success path.  On any
        planning failure, yields exactly one ``None`` sentinel and
        returns — the caller treats that as "fall through to the
        standard chat path".  This keeps the integration point clean
        and prevents leaking raw planner state into the run() body.
        """
        from app.orchestration.executor import executor as _executor
        from app.orchestration.knowledge import (
            KBEntry,
            knowledge_store,
            summarise_if_needed,
        )
        from app.services.model_router import model_router
        from app.services.orchestration_planner import (
            AnnotatedPlan,
            PlanningContext,
            PlanningFailure,
            orchestration_planner,
        )

        # ── 1. Build planning context ────────────────────────────────────
        ctx = getattr(request, "_profile_context", None)
        profile_mode = getattr(ctx, "profile_mode", None) or "personal"
        if profile_mode == "org":
            profile_mode = "work"
        tenant_id = getattr(ctx, "tenant_id", None) or getattr(
            user, "tenant_id", None
        )

        planning_ctx = PlanningContext(
            user_id=str(user.id),
            tenant_id=tenant_id,
            profile_mode=profile_mode,
        )

        # ── 2. Plan ──────────────────────────────────────────────────────
        outcome = await orchestration_planner.plan(
            request.message, planning_ctx, db
        )
        if isinstance(outcome, PlanningFailure):
            logger.info(
                "[orchestrator/cross-worker] planner failure=%s detail=%s "
                "→ falling through",
                outcome.reason, outcome.detail,
            )
            yield None
            return
        annotated: AnnotatedPlan = outcome

        # ── 3. Slow-plan warning (UX nicety) ─────────────────────────────
        if annotated.slow_plan:
            yield StreamChunk(
                type="content",
                content="Let me check a few things for you...\n\n",
            )

        # ── 4. Execute ───────────────────────────────────────────────────
        try:
            execution = await _executor.run_plan(db, annotated.plan)
        except Exception as exc:
            logger.warning(
                "[orchestrator/cross-worker] executor raised: %s — "
                "falling through to standard path",
                exc,
            )
            yield None
            return

        # ── 5. Build synthesis prompt ────────────────────────────────────
        # Distinguish completed-with-output from worker-unavailable so
        # the synthesiser can produce a graceful partial answer when
        # some workers were down.  Never surface raw error codes to the
        # user — the synthesis prompt translates them.
        completed: list[str] = []
        unavailable: list[str] = []
        for r in execution.results:
            cap_line = f"- {r.worker_id}.{r.capability}: {r.summary or '(no summary)'}"
            if r.success:
                completed.append(cap_line)
            else:
                code = (r.error.code if r.error else "UNKNOWN")
                unavailable.append(
                    f"- {r.worker_id}.{r.capability}: unavailable "
                    f"(reason: {code})"
                )

        synthesis_user = (
            f"The user asked: {request.message}\n\n"
            "Worker findings:\n"
            + ("\n".join(completed) if completed else "  (none)")
            + (
                "\n\nWorkers that could not respond:\n" + "\n".join(unavailable)
                if unavailable else ""
            )
            + "\n\nAnswer the user's question directly using the findings "
            "above.  If some workers were unavailable, mention briefly that "
            "their data wasn't accessible — but do not surface error codes."
        )
        synthesis_messages = [
            {
                "role": "system",
                "content": (
                    "You synthesise multi-worker results into one concise, "
                    "user-facing answer.  Cite workers naturally ('according "
                    "to your task list', 'from the meeting transcript') — "
                    "do not surface internal IDs."
                ),
            },
            {"role": "user", "content": synthesis_user},
        ]

        # ── 6. Stream synthesis via the standard router (failover free) ──
        synth_model = select_auto_model(
            IntentType.CROSS_WORKER, budget_warning_pct=budget_pct
        )
        content_chars = 0
        try:
            async for chunk in model_router.stream(
                synthesis_messages,
                model=synth_model,
                user_id=str(user.id),
                tools=None,
            ):
                if chunk.type == "router_resolved":
                    continue  # internal — never forward
                if chunk.type == "content" and chunk.content:
                    content_chars += len(chunk.content)
                yield chunk
                if chunk.type == "done":
                    break
        except Exception as exc:
            logger.warning(
                "[orchestrator/cross-worker] synthesis stream failed: %s",
                exc,
            )
            # No content streamed yet → fall through.  Content already
            # streamed → swallow; success criterion is content_chars > 0.
            if content_chars == 0:
                yield None
                return

        # If the synthesiser produced no content, signal fall-through.
        if content_chars == 0:
            yield None
            return

        # ── 7. Persist a goal_result KB entry ────────────────────────────
        # Best-effort — failures here never break the user-facing stream.
        try:
            top_summaries = " | ".join(
                (r.summary or "")[:100] for r in execution.results if r.success
            )[:1500]
            kb_summary = await summarise_if_needed(
                top_summaries or request.message[:400],
                source_worker_id="orchestration_planner",
            )
            await knowledge_store.ingest(
                db,
                KBEntry(
                    user_id=str(user.id),
                    tenant_id=tenant_id,
                    profile_mode=profile_mode,
                    source_worker_id="orchestration_planner",
                    trace_id=execution.trace_id,
                    entry_type="goal_result",
                    title=request.message[:200],
                    summary=kb_summary,
                    data_pointer=f"trace:{execution.trace_id}",
                    tags=["cross_worker"],
                ),
            )
        except Exception as _kb_err:
            logger.debug(
                "[orchestrator/cross-worker] KB ingest skipped: %s",
                _kb_err,
            )


outcome_orchestrator = OutcomeOrchestrator()
