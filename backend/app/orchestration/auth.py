"""
Mela AI - Worker callback authentication.

Workers POST to ``/api/v1/ingest/result`` and ``/api/v1/ingest/event``
when they have something to report.  These endpoints are NOT for
human users — ``get_current_user`` would be wrong.  Instead we
authenticate workers by:

  1. Reading ``X-Worker-Id`` and ``X-Worker-Api-Key`` headers
  2. Looking up the worker manifest in the registry
  3. Constant-time comparing the supplied key against
     ``manifest.auth_config["inbound_api_key"]``

For Phase 2 the inbound key is stored directly on the manifest
(``WorkerManifest.auth_config["inbound_api_key"]``); the .env samples
document a Key-Vault-reference path so production can rotate via
``@Microsoft.KeyVault(...)`` without code changes.
"""

from __future__ import annotations

import hmac
import logging
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.orchestration.registry import worker_registry
from app.orchestration.types import WorkerManifest

logger = logging.getLogger(__name__)


# Header names — kept in one place so the .env samples and any worker SDKs
# we ship later reference the same constants.
WORKER_ID_HEADER = "X-Worker-Id"
WORKER_API_KEY_HEADER = "X-Worker-Api-Key"


async def require_worker_api_key(
    x_worker_id: str = Header(..., alias=WORKER_ID_HEADER),
    x_worker_api_key: str = Header(..., alias=WORKER_API_KEY_HEADER),
    db: AsyncSession = Depends(get_db),
) -> WorkerManifest:
    """FastAPI dependency: validate the inbound worker callback.

    Returns the matched :class:`WorkerManifest` so handlers can read
    ``manifest.id`` for trust decisions without a second lookup.

    Raises:
        HTTP 401 if the worker isn't registered, the manifest declares
        no inbound key, or the supplied key doesn't match.
    """
    if not x_worker_id or not x_worker_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Worker authentication headers required",
        )

    manifest = await worker_registry.get(db, x_worker_id)
    if manifest is None:
        # Don't leak whether the worker_id exists vs the key was wrong —
        # both look like 401 to a caller.
        logger.warning(
            "worker auth: unknown worker_id=%r (callback rejected)", x_worker_id
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker credentials",
        )

    expected = (manifest.auth_config or {}).get("inbound_api_key")
    if not expected:
        logger.warning(
            "worker auth: no inbound_api_key configured for worker=%s "
            "(callback rejected — set manifest.auth_config['inbound_api_key'])",
            x_worker_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker credentials",
        )

    # Constant-time compare to dodge timing oracles.
    if not hmac.compare_digest(str(expected), str(x_worker_api_key)):
        logger.warning(
            "worker auth: bad inbound_api_key for worker=%s", x_worker_id
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker credentials",
        )

    return manifest
