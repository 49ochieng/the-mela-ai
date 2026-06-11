"""Background scan worker. Run with: python -m app.workers.worker"""
from __future__ import annotations

import asyncio
import logging

from ..database import session_scope
from ..logging_config import setup_logging
from ..services.queue.queue import get_queue
from ..services.tasks.scan_runner import run_scan

logger = logging.getLogger(__name__)


async def _handle(payload: dict) -> None:
    if payload.get("type") != "scan":
        logger.warning("Unknown payload: %s", payload)
        return
    scan_run_id = payload["scan_run_id"]
    logger.info("Starting scan_run %s", scan_run_id)
    try:
        async with session_scope() as session:
            await run_scan(session, scan_run_id)
    except Exception:
        logger.exception("Scan %s crashed", scan_run_id)


async def main() -> None:
    setup_logging()
    queue = get_queue()
    logger.info("Worker started")
    async for payload in queue.consume():
        try:
            await _handle(payload)
        except Exception:
            logger.exception("Worker handler error")


if __name__ == "__main__":
    asyncio.run(main())
