"""Pluggable queue. In-memory for dev, Service Bus for prod."""
from __future__ import annotations

import abc
import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

from ...config import get_settings

logger = logging.getLogger(__name__)


class Queue(abc.ABC):
    @abc.abstractmethod
    async def enqueue(self, payload: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    async def consume(self) -> AsyncIterator[dict[str, Any]]: ...


class InMemoryQueue(Queue):
    _instance: Optional["InMemoryQueue"] = None

    def __init__(self) -> None:
        self._q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    @classmethod
    def instance(cls) -> "InMemoryQueue":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def enqueue(self, payload: dict[str, Any]) -> None:
        await self._q.put(payload)

    async def consume(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._q.get()
            yield item


class ServiceBusQueue(Queue):
    def __init__(self, conn: str, queue_name: str) -> None:
        self.conn = conn
        self.queue_name = queue_name

    async def enqueue(self, payload: dict[str, Any]) -> None:
        from azure.servicebus.aio import ServiceBusClient
        from azure.servicebus import ServiceBusMessage

        async with ServiceBusClient.from_connection_string(self.conn) as client:
            sender = client.get_queue_sender(self.queue_name)
            async with sender:
                await sender.send_messages(ServiceBusMessage(json.dumps(payload)))

    async def consume(self) -> AsyncIterator[dict[str, Any]]:
        from azure.servicebus.aio import ServiceBusClient

        async with ServiceBusClient.from_connection_string(self.conn) as client:
            receiver = client.get_queue_receiver(self.queue_name, max_wait_time=5)
            async with receiver:
                async for msg in receiver:
                    try:
                        payload = json.loads(str(msg))
                        yield payload
                        await receiver.complete_message(msg)
                    except Exception as e:
                        logger.exception("queue consume error: %s", e)
                        await receiver.dead_letter_message(msg, reason=str(e)[:200])


_queue: Optional[Queue] = None


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        s = get_settings()
        if s.queue_provider == "servicebus" and s.azure_service_bus_connection_string:
            _queue = ServiceBusQueue(s.azure_service_bus_connection_string, s.azure_service_bus_queue)
        else:
            _queue = InMemoryQueue.instance()
    return _queue
