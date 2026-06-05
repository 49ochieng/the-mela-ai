"""
Mela AI - Canonical orchestration types.

These are the ONLY types that cross the adapter boundary. Worker-specific
shapes (MCP tool args, REST payloads, gRPC messages) must be translated
into / out of these types inside an adapter and never leak into
orchestration logic, the planner, the router, or the executor.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ─────────────────────────────────────────────────────────────────


class Protocol(str, Enum):
    """Wire protocol an adapter speaks to its worker."""

    MCP = "mcp"
    REST = "rest"
    WEBHOOK = "webhook"
    GRPC = "grpc"


class WorkerStatus(str, Enum):
    """Liveness state of a registered worker, set by health polling."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"
    # Phase 4: registered manifest exists but its base URL / credentials
    # were left blank at seed time.  ``unreachable`` would be misleading
    # because no probe was ever attempted.  This is a UX signal to admins.
    UNCONFIGURED = "unconfigured"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class AuthScheme(str, Enum):
    BEARER = "bearer"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    NONE = "none"


# ── Capability + Manifest ────────────────────────────────────────────────


class Capability(BaseModel):
    """A single thing a worker can do.

    The ``name`` is what planners and ``MelaTask.capability`` reference.
    ``inputParams`` / ``outputShape`` are JSON Schema fragments — kept as
    raw dicts so we don't lock the registry into a specific Pydantic version.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1)
    input_params: dict[str, Any] = Field(default_factory=dict)
    output_shape: dict[str, Any] = Field(default_factory=dict)
    is_async: bool = False
    estimated_ms: int = Field(default=1000, ge=0)


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_ms: int = Field(default=500, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)


class WorkerManifest(BaseModel):
    """A registered worker — source of truth for what Mela can orchestrate."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64, description="unique slug")
    display_name: str
    version: str = Field(..., description="semver of the worker's API contract")
    capabilities: list[Capability]
    protocol: Protocol
    base_url: str
    health_check_url: str
    auth_scheme: AuthScheme = AuthScheme.NONE
    auth_config: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=30_000, ge=100)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    report_back_url: Optional[str] = None
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_health_check: Optional[datetime] = None
    status: WorkerStatus = WorkerStatus.UNKNOWN

    def capability(self, name: str) -> Optional[Capability]:
        return next((c for c in self.capabilities if c.name == name), None)

    def has_capability(self, name: str) -> bool:
        return self.capability(name) is not None


# ── Task + Result (the lingua franca) ────────────────────────────────────


class MelaContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    user_id: str
    project_id: Optional[str] = None
    goal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    priority: Priority = Priority.NORMAL


class MelaTask(BaseModel):
    """Outbound instruction from Mela to a worker (via an adapter)."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    capability: str
    worker_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    context: MelaContext
    execution_mode: Literal["sync", "async"] = "sync"
    callback_url: Optional[str] = None
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    timeout_ms: int = Field(default=30_000, ge=100)


class MelaError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool = False


class MelaResultMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: int = 0
    source: str = ""
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MelaResult(BaseModel):
    """Inbound result from a worker, normalized for the planner / synthesizer.

    Adapters MUST always return one of these — never raise. The planner only
    consumes ``summary``; full ``data`` is stored in the KB and pulled by
    pointer so the LLM context window stays lean.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    trace_id: str
    worker_id: str
    capability: str
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    metadata: MelaResultMetadata = Field(default_factory=MelaResultMetadata)
    error: Optional[MelaError] = None

    @classmethod
    def failure(
        cls,
        *,
        task: MelaTask,
        code: str,
        message: str,
        retryable: bool = False,
        latency_ms: int = 0,
        source: str = "",
    ) -> "MelaResult":
        """Helper for the common adapter failure path."""
        return cls(
            task_id=task.task_id,
            trace_id=task.trace_id,
            worker_id=task.worker_id,
            capability=task.capability,
            success=False,
            data={},
            summary=f"{task.capability} failed: {message}",
            metadata=MelaResultMetadata(latency_ms=latency_ms, source=source),
            error=MelaError(code=code, message=message, retryable=retryable),
        )
