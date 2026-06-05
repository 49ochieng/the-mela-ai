"""
ai_triage.py
LLM-based incident triage using Azure OpenAI.
Returns AiTriage dataclass with confidence score.
Output is ALWAYS labeled "human validation required".
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.alert_service import AlertIncident, AiTriage

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an SRE assistant for the Mela AI platform.

You receive a single incident JSON object and return ONE JSON object describing
the most likely cause and the smallest safe mitigation a human can validate.

You MUST return strict JSON with these keys ONLY:
{
  "probable_cause": "<one short sentence>",
  "confidence": <float 0.0-1.0>,
  "immediate_mitigation": "<one concrete actionable step>",
  "likely_owner": "<backend|platform|data|frontend|infra>",
  "first_validation_step": "<one observable command or check>"
}

Be conservative. If unsure, use confidence below 0.5.
Never invent system names. Never recommend destructive actions.
"""


async def generate_triage(incident) -> Optional["AiTriage"]:
    """Generate AI triage. Returns None on failure (caller treats as no triage)."""
    from app.services.alert_service import AiTriage

    endpoint = getattr(settings, "effective_openai_endpoint", None) or getattr(settings, "AZURE_OPENAI_ENDPOINT", None)
    api_key = getattr(settings, "effective_openai_api_key", None) or getattr(settings, "AZURE_OPENAI_API_KEY", None)
    deployment = getattr(settings, "AZURE_OPENAI_CHAT_DEPLOYMENT", None)
    api_version = getattr(settings, "AZURE_OPENAI_API_VERSION", "2024-05-01-preview")

    if not (endpoint and api_key and deployment):
        logger.info("AI triage skipped — Azure OpenAI not configured")
        return None

    try:
        from openai import AsyncAzureOpenAI

        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )

        payload = {
            "code": incident.code,
            "severity": incident.severity,
            "title": incident.title,
            "route": incident.route,
            "tenant_id": incident.tenant_id,
            "worker": incident.worker,
            "error_message": incident.error_message,
            "stack_tail": (incident.stack_trace or "").splitlines()[-10:],
        }

        response = await client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=400,
            timeout=20,
        )

        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)

        confidence = float(data.get("confidence", 0.0))
        if confidence < settings.ALERT_CONFIDENCE_THRESHOLD:
            logger.info("AI triage confidence %.2f below threshold — discarding", confidence)
            return None

        return AiTriage(
            probable_cause=str(data.get("probable_cause", ""))[:500],
            confidence=confidence,
            immediate_mitigation=str(data.get("immediate_mitigation", ""))[:500],
            likely_owner=str(data.get("likely_owner", "backend"))[:50],
            first_validation_step=str(data.get("first_validation_step", ""))[:500],
            generated_at=datetime.now(timezone.utc),
            model_used=deployment,
            human_validation_required=True,
        )
    except Exception as exc:
        logger.warning("AI triage call failed: %s", exc)
        return None
