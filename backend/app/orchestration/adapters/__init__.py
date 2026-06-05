"""Worker adapters — one per protocol; one factory dispatches by manifest."""

from app.orchestration.adapters.base import (
    AdapterHealth,
    WorkerAdapter,
)
from app.orchestration.adapters.factory import AdapterFactory, adapter_factory
from app.orchestration.adapters.task_radar import MCPAdapter, TaskRadarAdapter

__all__ = [
    "AdapterFactory",
    "AdapterHealth",
    "MCPAdapter",
    "TaskRadarAdapter",
    "WorkerAdapter",
    "adapter_factory",
]
