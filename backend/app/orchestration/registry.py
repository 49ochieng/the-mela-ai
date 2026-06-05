"""
Mela AI - Worker Registry.

Source of truth for the workers Mela can orchestrate.  Persists
``WorkerManifest`` rows in the ``worker_registry`` table and serves a
short-TTL in-process read cache so the planner / router can do
capability lookups on the hot path without hammering the DB.

Spec calls for a 60-second Redis-backed cache.  We don't have Redis
today, so the cache lives in-process.  The interface is identical to
what a Redis-backed implementation would expose, which keeps the
swap straightforward when we move to multi-instance.

Concurrency: a single ``asyncio.Lock`` guards refreshes so multiple
concurrent reads on cache miss issue exactly one DB query.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import WorkerRegistryEntry
from app.orchestration.types import WorkerManifest, WorkerStatus

logger = logging.getLogger(__name__)


CACHE_TTL_SECONDS = 60


class WorkerRegistry:
    def __init__(self, *, cache_ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self._cache: dict[str, WorkerManifest] = {}
        self._cache_loaded_at: float = 0.0
        self._cache_ttl = cache_ttl_seconds
        self._lock = asyncio.Lock()

    # ── Reads ─────────────────────────────────────────────────────────────

    async def get(self, db: AsyncSession, worker_id: str) -> Optional[WorkerManifest]:
        await self._ensure_fresh(db)
        return self._cache.get(worker_id)

    async def list(self, db: AsyncSession) -> list[WorkerManifest]:
        await self._ensure_fresh(db)
        return list(self._cache.values())

    async def find_by_capability(
        self, db: AsyncSession, capability: str
    ) -> list[WorkerManifest]:
        await self._ensure_fresh(db)
        return [m for m in self._cache.values() if m.has_capability(capability)]

    # ── Writes ────────────────────────────────────────────────────────────

    async def upsert(
        self, db: AsyncSession, manifest: WorkerManifest
    ) -> WorkerManifest:
        existing = await db.get(WorkerRegistryEntry, manifest.id)
        manifest_dict = manifest.model_dump(mode="json")
        if existing is None:
            row = WorkerRegistryEntry(
                id=manifest.id,
                display_name=manifest.display_name,
                version=manifest.version,
                protocol=manifest.protocol.value,
                base_url=manifest.base_url,
                health_check_url=manifest.health_check_url,
                status=manifest.status.value,
                manifest=manifest_dict,
                last_health_check=manifest.last_health_check,
                registered_at=manifest.registered_at,
            )
            db.add(row)
        else:
            existing.display_name = manifest.display_name
            existing.version = manifest.version
            existing.protocol = manifest.protocol.value
            existing.base_url = manifest.base_url
            existing.health_check_url = manifest.health_check_url
            existing.status = manifest.status.value
            existing.manifest = manifest_dict
            existing.last_health_check = manifest.last_health_check
        await db.commit()
        self._invalidate()
        return manifest

    async def update_status(
        self,
        db: AsyncSession,
        worker_id: str,
        status: WorkerStatus,
        last_health_check: Optional[float] = None,
    ) -> None:
        from datetime import datetime, timezone

        row = await db.get(WorkerRegistryEntry, worker_id)
        if row is None:
            return
        row.status = status.value
        if last_health_check is not None:
            row.last_health_check = datetime.fromtimestamp(
                last_health_check, tz=timezone.utc
            )
        # Keep the JSON manifest's status in sync so a fresh load reflects it.
        manifest_dict = dict(row.manifest or {})
        manifest_dict["status"] = status.value
        if row.last_health_check is not None:
            manifest_dict["last_health_check"] = row.last_health_check.isoformat()
        row.manifest = manifest_dict
        await db.commit()
        self._invalidate()

    async def remove(self, db: AsyncSession, worker_id: str) -> bool:
        result = await db.execute(
            delete(WorkerRegistryEntry).where(WorkerRegistryEntry.id == worker_id)
        )
        await db.commit()
        self._invalidate()
        return (result.rowcount or 0) > 0

    # ── Cache mechanics ───────────────────────────────────────────────────

    def _invalidate(self) -> None:
        self._cache_loaded_at = 0.0

    async def _ensure_fresh(self, db: AsyncSession) -> None:
        now = time.monotonic()
        if self._cache and (now - self._cache_loaded_at) < self._cache_ttl:
            return
        async with self._lock:
            # Re-check after acquiring the lock — another waiter may have refreshed.
            now = time.monotonic()
            if self._cache and (now - self._cache_loaded_at) < self._cache_ttl:
                return
            await self._reload(db)

    async def _reload(self, db: AsyncSession) -> None:
        try:
            rows = (
                await db.execute(select(WorkerRegistryEntry))
            ).scalars().all()
        except Exception as exc:
            # Don't crash the orchestration layer on a transient DB blip — keep
            # serving the previous cache (or empty) and log.
            logger.warning("WorkerRegistry reload failed: %s", exc)
            return
        new_cache: dict[str, WorkerManifest] = {}
        for row in rows:
            try:
                new_cache[row.id] = WorkerManifest.model_validate(row.manifest)
            except Exception as exc:
                logger.warning(
                    "WorkerRegistry: skipping malformed manifest for %s: %s",
                    row.id,
                    exc,
                )
        self._cache = new_cache
        self._cache_loaded_at = time.monotonic()


# Module-level singleton — one cache per process.
worker_registry = WorkerRegistry()
