"""
Mela AI - Orchestration event bus.

In-process pub/sub channel that delivers ``WorkerEventChunk`` payloads
to every open SSE connection a user has against
``/api/v1/orchestration/events/stream``.  Replaces the polling /
notification-only path with a real-time push.

Design constraints
------------------

* **Multi-subscriber per user.**  ``subscribe(user_id)`` returns a fresh
  ``asyncio.Queue`` every call so multiple browser tabs / devices can
  listen concurrently.  ``unsubscribe`` removes only the queue passed
  in — never tears down a user's other subscriptions.
* **Bounded queues.**  Each subscriber queue caps at ``MAX_QUEUE_DEPTH``
  events.  When full, ``publish`` drops the OLDEST event (not the
  newest) so the freshest signal always reaches the listener.
* **No persistence.**  Events delivered while no one is subscribed are
  silently dropped — the KB + AuditLog already record everything for
  history; the bus is a live signal only.
* **In-process.**  Single Mela instance only.  When we move to
  multi-instance, swap this implementation for a Redis Pub/Sub
  variant: ``subscribe`` becomes ``redis.pubsub().subscribe(f"u:{id}")``
  and ``publish`` becomes ``redis.publish(f"u:{id}", json)``.  The
  module-level singleton + method signatures stay identical.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Optional

from app.schemas.chat import WorkerEventChunk

logger = logging.getLogger(__name__)


# Per-subscriber queue depth.  Picked so that a brief frontend stall
# can't lose meaningful state, but a runaway publisher can't burn
# unbounded RAM either.
MAX_QUEUE_DEPTH = 50


class OrchestrationEventBus:
    """Per-user fan-out for ``WorkerEventChunk`` payloads."""

    def __init__(self) -> None:
        # user_id → list of queues (one per open SSE connection).
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()

    # ── Subscription lifecycle ───────────────────────────────────────────

    async def subscribe(self, user_id: str) -> asyncio.Queue:
        """Register a new subscriber and return its queue.

        Always creates a fresh queue — multiple tabs from the same user
        each get their own.  Caller MUST call ``unsubscribe`` on stream
        close (finally block) to avoid leaking queues.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_DEPTH)
        async with self._lock:
            self._subscribers[user_id].append(q)
        logger.debug(
            "event_bus: subscribed user=%s (active=%d)",
            user_id, len(self._subscribers[user_id]),
        )
        return q

    async def unsubscribe(
        self, user_id: str, queue: asyncio.Queue
    ) -> None:
        """Remove the specific queue from this user's subscriber list."""
        async with self._lock:
            queues = self._subscribers.get(user_id)
            if not queues:
                return
            try:
                queues.remove(queue)
            except ValueError:
                # Already removed (double-unsubscribe) — fine.
                return
            if not queues:
                # Last subscriber for this user → free the dict slot.
                self._subscribers.pop(user_id, None)
        logger.debug("event_bus: unsubscribed user=%s", user_id)

    # ── Publish ──────────────────────────────────────────────────────────

    async def publish(
        self, user_id: str, event: WorkerEventChunk
    ) -> int:
        """Push *event* to every subscriber queue for *user_id*.

        Returns the number of subscriber queues the event landed in.
        On a full queue, drops the oldest item to make room — the
        freshest event always wins.  No subscribers → returns 0
        without raising.
        """
        delivered = 0
        async with self._lock:
            queues = list(self._subscribers.get(user_id, ()))
        for q in queues:
            self._enqueue_evicting_oldest(q, event)
            delivered += 1
        return delivered

    async def publish_to_tenant(
        self,
        tenant_id: str,
        event: WorkerEventChunk,
        *,
        user_index: Optional["TenantUserIndex"] = None,
    ) -> int:
        """Fan out *event* to every user under *tenant_id*.

        Phase 5A ships the API surface; the user-index resolver is a
        tiny abstraction so a future tenant-scope event (e.g.
        ``worker_available``) can call this without rewiring.  When
        called without a *user_index*, the bus walks its own
        ``_subscribers`` keys — fine when those keys *are* user_ids
        (the current scheme) and you accept that it only fans out to
        currently-connected users.
        """
        delivered = 0
        async with self._lock:
            user_ids = (
                user_index.resolve(tenant_id)
                if user_index is not None
                else list(self._subscribers.keys())
            )
        for uid in user_ids:
            delivered += await self.publish(uid, event)
        return delivered

    # ── Internals ────────────────────────────────────────────────────────

    @staticmethod
    def _enqueue_evicting_oldest(
        q: asyncio.Queue, event: WorkerEventChunk
    ) -> None:
        """put_nowait, evicting the oldest entry on overflow.

        We deliberately drop the oldest (not the newest) so a slow
        consumer always sees the freshest worker state.  All write
        ops are non-blocking; the event bus must never wait on a
        subscriber's reader.
        """
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                _ = q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Truly impossible in practice (we just freed a slot)
                # but never raise from publish — the bus is best-effort.
                logger.warning(
                    "event_bus: unable to enqueue worker event "
                    "after eviction (dropping)"
                )


class TenantUserIndex:
    """Trivial protocol for resolving tenant_id → list[user_id]."""

    def resolve(self, tenant_id: str) -> list[str]:  # pragma: no cover
        return []


# Module-level singleton.  One bus per process.
event_bus = OrchestrationEventBus()
