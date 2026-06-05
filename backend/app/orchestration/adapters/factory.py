"""
Mela AI - Adapter factory.

Given a ``WorkerManifest``, returns the correct ``WorkerAdapter``
instance.  The dispatch key is ``manifest.protocol`` — any worker
sharing a protocol shares an adapter implementation.  Adding a new
worker that speaks an existing protocol costs zero new code; adding a
new protocol means writing one adapter and registering it in
``_PROTOCOL_TO_ADAPTER``.

Adapters are cached per ``manifest.id`` so we don't recreate them on
every router lookup.  Cache is invalidated when a manifest's
``updated_at`` changes — caller passes a freshly-loaded manifest, the
factory checks identity by manifest version + auth_config snapshot.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.orchestration.adapters.base import WorkerAdapter
from app.orchestration.adapters.task_radar import MCPAdapter
from app.orchestration.types import Protocol, WorkerManifest

logger = logging.getLogger(__name__)


# Protocol → adapter class mapping.  One entry per supported protocol.
# Phase 2 ships MCP only; REST / webhook / gRPC adapters land later as
# new entries here without touching anything else.
_PROTOCOL_TO_ADAPTER: dict[Protocol, type[WorkerAdapter]] = {
    Protocol.MCP: MCPAdapter,
}


class AdapterFactory:
    """Stateful factory + per-process cache of adapter instances."""

    def __init__(self) -> None:
        # worker_id → (manifest_signature, adapter)
        self._cache: dict[str, tuple[str, WorkerAdapter]] = {}

    @staticmethod
    def _signature(manifest: WorkerManifest) -> str:
        """Cheap fingerprint that changes when an adapter must be rebuilt."""
        # Include auth_config (api_key swap should rebuild) and base_url
        # (worker moved → new client).  Version covers everything else.
        ac = manifest.auth_config or {}
        return (
            f"{manifest.version}|{manifest.base_url}|"
            f"{manifest.protocol.value}|{sorted(ac.items())}"
        )

    def get(self, manifest: WorkerManifest) -> Optional[WorkerAdapter]:
        """Return the adapter for *manifest*, or None if no adapter handles its protocol."""
        adapter_cls = _PROTOCOL_TO_ADAPTER.get(manifest.protocol)
        if adapter_cls is None:
            logger.warning(
                "AdapterFactory: no adapter registered for protocol=%s (worker=%s)",
                manifest.protocol.value,
                manifest.id,
            )
            return None

        sig = self._signature(manifest)
        cached = self._cache.get(manifest.id)
        if cached is not None:
            cached_sig, adapter = cached
            if cached_sig == sig and isinstance(adapter, adapter_cls):
                return adapter

        adapter = adapter_cls(manifest)
        self._cache[manifest.id] = (sig, adapter)
        return adapter

    def invalidate(self, worker_id: str) -> None:
        self._cache.pop(worker_id, None)

    def clear(self) -> None:
        self._cache.clear()


# Module-level singleton — one factory per process.
adapter_factory = AdapterFactory()
