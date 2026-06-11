"""GPT-5.2 task extraction.

The prompt + JSON schema enforce structured output. We validate with Pydantic,
retry once with a repair prompt on malformed JSON, and surface clean
ExtractionResult objects to the worker — together with diagnostics so the scan
runner can record per-message stage outcomes (success / no-task / failed).

Key correctness notes:
- Azure OpenAI's `gpt-5.2-chat` deployment rejects `max_tokens` and requires
  `max_completion_tokens` instead. Using `max_tokens` produces a 400
  `unsupported_parameter` error on every call — this is precisely what caused
  the previous "103 scanned / 0 tasks / 54 errors" scan run.
- We classify failures into safe categories (`auth`, `deployment_not_found`,
  `rate_limit`, `bad_request`, `validation`, `transport`, `unknown`) so the
  scan runner can surface what actually went wrong without leaking PII or
  full prompts.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncAzureOpenAI,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
)
from pydantic import ValidationError

from ...config import get_settings
from ...schemas import ExtractionResult

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are Mela Task Radar's task extraction engine.
You receive ONE Microsoft 365 message (email or Teams) and decide whether it
contains actionable tasks the user must perform.

A TASK is any direct ask, request to review/respond/approve/create/update,
scheduling request, forwarding request, @mention with implied work, deadline
directed at the user, or multiple tasks combined in one message.

For TEAMS messages specifically: a broadcast posted to a channel that implies
work for the recipient is STILL a task even when the user is not explicitly
@mentioned. Examples to extract: "Can someone review this PR by EOD",
"We need volunteers to draft the design doc", "Please update your status
in the tracker by Friday", "Owners: ship by Tuesday", code-review requests,
on-call/rotation asks, deliverable lists, deadlines stated for the team.
Use the provided context (channel name, thread context, who-is-mentioned,
recipient role) to judge whether the user is a plausible owner. When in
doubt and the message contains an action verb plus a deadline or owner
slot, prefer extracting the task with confidence 0.5-0.7 and
priority_reasoning explaining the inference.

NOT a task: FYI, newsletters, automated notifications, thank-you messages,
calendar invites, messages where someone else is clearly assigned, vague
"let me know if you have questions", "looping you in" with no action,
bot/system messages unless explicitly assigned, social chatter
("good morning", emoji reactions, GIFs, congrats).

You MUST output ONLY valid JSON matching this exact schema:
{
  "has_task": boolean,
  "tasks": [
    {
      "title": string,
      "description": string,
      "task_type": "review"|"respond"|"create"|"approve"|"schedule"|"forward"|"follow_up"|"other",
      "assigned_to": string | null,
      "due_date": "YYYY-MM-DD" | null,
      "due_date_raw": string | null,
      "priority": "high"|"medium"|"low",
      "priority_reasoning": string,
      "confidence": number between 0 and 1,
      "evidence": "<short quote from message>"
    }
  ]
}
If no task: {"has_task": false, "tasks": []}.
Never include any text outside the JSON."""


REPAIR_PROMPT = (
    "The previous response was not valid JSON or violated the schema. "
    "Return ONLY valid JSON matching the original schema. No commentary."
)

PROMPT_VERSION = "v3"


class ExtractorConfigError(RuntimeError):
    """Raised when AOAI deployment / endpoint is misconfigured.
    Surfaces to the scan runner so the UI shows a clear, scan-wide error
    instead of N identical per-message failures."""


@dataclass
class ExtractionDiagnostics:
    has_task: bool = False
    task_count: int = 0
    input_chars: int = 0
    output_chars: int = 0
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    prompt_version: str = PROMPT_VERSION
    model_deployment: str = ""
    validation_error: Optional[str] = None
    error_category: Optional[str] = None  # ai_failed sub-category
    error_message: Optional[str] = None
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _build_user_prompt(msg: dict[str, Any]) -> str:
    return json.dumps(
        {
            "source": msg.get("source"),
            "from_name": msg.get("from_name"),
            "from_email": msg.get("from_email"),
            "to": msg.get("to") or [],
            "cc": msg.get("cc") or [],
            "subject_or_channel": msg.get("subject_or_channel"),
            "received_at": msg.get("received_at"),
            "body_text": msg.get("body_text") or "",
            "mentions": msg.get("mentions") or [],
            "thread_context": msg.get("thread_context") or "",
            "attachments": msg.get("attachments") or [],
        },
        ensure_ascii=False,
    )


_client: AsyncAzureOpenAI | None = None


def _reset_client_for_tests() -> None:
    global _client
    _client = None


def _get_client() -> AsyncAzureOpenAI:
    global _client
    if _client is None:
        s = get_settings()
        if not s.azure_openai_endpoint or not s.azure_openai_api_key:
            raise ExtractorConfigError(
                "Azure OpenAI is not configured. Set AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_API_KEY."
            )
        _client = AsyncAzureOpenAI(
            api_key=s.azure_openai_api_key,
            api_version=s.azure_openai_api_version,
            azure_endpoint=s.azure_openai_endpoint,
        )
    return _client


def _classify_error(exc: BaseException) -> tuple[str, bool]:
    """Return (category, retryable)."""
    if isinstance(exc, AuthenticationError):
        return "auth", False
    if isinstance(exc, RateLimitError):
        return "rate_limit", True
    if isinstance(exc, APIConnectionError):
        return "transport", True
    if isinstance(exc, NotFoundError):
        return "deployment_not_found", False
    if isinstance(exc, BadRequestError):
        msg = str(exc).lower()
        if "max_tokens" in msg or "max_completion_tokens" in msg:
            return "model_param_unsupported", False
        if "deployment" in msg:
            return "deployment_not_found", False
        return "bad_request", False
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None) or 0
        if status in (401, 403):
            return "auth", False
        if status == 404:
            return "deployment_not_found", False
        if status >= 500:
            return "transport", True
        return "bad_request", False
    if isinstance(exc, (json.JSONDecodeError, ValidationError)):
        return "validation", False
    return "unknown", False


async def extract_with_diagnostics(
    message: dict[str, Any],
) -> tuple[ExtractionResult, ExtractionDiagnostics]:
    """Run extraction. Always returns (result, diagnostics).

    Raises ExtractorConfigError only for hard misconfiguration that affects
    the entire scan (so the scan runner can short-circuit instead of producing
    one identical failure per message).
    """
    s = get_settings()
    client = _get_client()

    user_payload = _build_user_prompt(message)
    diag = ExtractionDiagnostics(
        input_chars=len(user_payload),
        model_deployment=s.azure_openai_deployment_gpt52,
    )

    async def _call(extra_user: Optional[str] = None) -> Any:
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ]
        if extra_user:
            msgs.append({"role": "user", "content": extra_user})
        # IMPORTANT: gpt-5.2 requires `max_completion_tokens` — `max_tokens` is
        # rejected with a 400 unsupported_parameter error.
        return await client.chat.completions.create(
            model=s.azure_openai_deployment_gpt52,
            messages=msgs,
            response_format={"type": "json_object"},
            max_completion_tokens=1500,
        )

    try:
        resp = await _call()
    except (AuthenticationError, NotFoundError) as exc:
        category, _ = _classify_error(exc)
        raise ExtractorConfigError(
            f"Azure OpenAI {category}: {str(exc)[:200]}"
        ) from exc
    except BadRequestError as exc:
        category, retryable = _classify_error(exc)
        if category in ("model_param_unsupported", "deployment_not_found"):
            raise ExtractorConfigError(
                f"Azure OpenAI rejected request ({category}): {str(exc)[:200]}"
            ) from exc
        diag.error_category = category
        diag.error_message = str(exc)[:300]
        diag.retryable = retryable
        return ExtractionResult(has_task=False, tasks=[]), diag
    except Exception as exc:  # noqa: BLE001
        category, retryable = _classify_error(exc)
        diag.error_category = category
        diag.error_message = str(exc)[:300]
        diag.retryable = retryable
        return ExtractionResult(has_task=False, tasks=[]), diag

    raw, finish = _extract_text(resp)
    diag.output_chars = len(raw)
    diag.finish_reason = finish
    usage = getattr(resp, "usage", None)
    if usage is not None:
        diag.prompt_tokens = getattr(usage, "prompt_tokens", None)
        diag.completion_tokens = getattr(usage, "completion_tokens", None)
        diag.total_tokens = getattr(usage, "total_tokens", None)

    try:
        result = _parse(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("AI output invalid (%s) — retrying with repair prompt", exc)
        try:
            resp2 = await _call(REPAIR_PROMPT)
        except Exception as exc2:  # noqa: BLE001
            cat, retryable = _classify_error(exc2)
            diag.error_category = cat
            diag.error_message = str(exc2)[:300]
            diag.retryable = retryable
            return ExtractionResult(has_task=False, tasks=[]), diag
        raw2, finish2 = _extract_text(resp2)
        diag.output_chars = len(raw2)
        diag.finish_reason = finish2
        try:
            result = _parse(raw2)
        except (json.JSONDecodeError, ValidationError) as exc2:
            diag.error_category = "validation"
            diag.validation_error = str(exc2)[:300]
            return ExtractionResult(has_task=False, tasks=[]), diag

    diag.has_task = bool(result.has_task)
    diag.task_count = len(result.tasks)
    return result, diag


async def extract_tasks(message: dict[str, Any]) -> ExtractionResult:
    """Backwards-compatible thin wrapper (kept for tests + simple callers)."""
    result, _ = await extract_with_diagnostics(message)
    return result


def _extract_text(resp: Any) -> tuple[str, Optional[str]]:
    try:
        choice = resp.choices[0]
        content = choice.message.content or "{}"
        finish = getattr(choice, "finish_reason", None)
        return content, finish
    except Exception:  # pragma: no cover — defensive
        return "{}", None


def _parse(raw: str) -> ExtractionResult:
    data = json.loads(raw)
    return ExtractionResult.model_validate(data)
