"""
Mela AI — Antivirus scan service (Phase 6 / M-5).

Pluggable AV scanner that runs *after* :func:`file_security.scan_file` and
*before* the bytes are persisted to durable storage or forwarded to the LLM.

Backends
--------
- ``disabled`` (default): returns ``unknown`` so callers can fail-open in
  dev and fail-closed in prod via :pyattr:`Settings.AV_SCAN_FAIL_CLOSED`.
- ``clamav``: streams the bytes to a ClamAV ``clamd`` daemon over TCP
  (``INSTREAM``). Used by docker-compose and on-prem deployments.
- ``defender``: trusts the Microsoft Defender for Storage blob-index tag
  (``Malware Scanning scan result``). The blob path must already exist and
  ``scan_blob_tag`` is the entry point.

The service is *fail-loud*: any unexpected error from a configured backend
returns verdict ``error`` (treated like ``unknown`` by callers, but logged
at ``ERROR`` level). The synchronous compute (clamd ``INSTREAM``) is
off-loaded with :func:`asyncio.to_thread` so it never blocks the loop.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

Verdict = Literal["clean", "malicious", "unknown", "error"]


@dataclass(frozen=True)
class ScanResult:
    verdict: Verdict
    engine: str
    signature: Optional[str] = None
    message: Optional[str] = None
    scanned_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    @property
    def is_malicious(self) -> bool:
        return self.verdict == "malicious"

    @property
    def is_clean(self) -> bool:
        return self.verdict == "clean"


# ── Public API ───────────────────────────────────────────────────────────


async def scan_bytes(data: bytes, filename: str = "") -> ScanResult:
    """Scan raw bytes; returns a :class:`ScanResult` (never raises)."""
    if not getattr(settings, "AV_SCAN_ENABLED", False):
        return ScanResult(verdict="unknown", engine="disabled")

    backend = (getattr(settings, "AV_SCAN_BACKEND", "disabled") or "").lower()

    max_bytes = int(getattr(settings, "AV_SCAN_MAX_BYTES", 25 * 1024 * 1024))
    if len(data) > max_bytes:
        logger.warning(
            "[av] file %r exceeds AV_SCAN_MAX_BYTES (%d > %d) — skipped",
            filename, len(data), max_bytes,
        )
        return ScanResult(
            verdict="unknown",
            engine=backend or "disabled",
            message="file_too_large_for_scan",
        )

    try:
        if backend == "clamav":
            return await _scan_with_clamav(data, filename)
        if backend == "defender":
            # No blob path supplied — caller should use scan_blob_tag instead.
            return ScanResult(
                verdict="unknown",
                engine="defender",
                message="defender_requires_blob_tag",
            )
        return ScanResult(verdict="unknown", engine="disabled")
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("[av] backend %s crashed scanning %r", backend, filename)
        return ScanResult(
            verdict="error",
            engine=backend or "unknown",
            message=str(exc)[:200],
        )


async def scan_blob_tag(scan_result_tag: Optional[str]) -> ScanResult:
    """Map a Microsoft Defender for Storage blob-index tag to a ScanResult.

    Defender sets the tag ``Malware Scanning scan result`` to one of
    ``"No threats found"``, ``"Malicious"``, or absent (still scanning).
    """
    if not scan_result_tag:
        return ScanResult(
            verdict="unknown",
            engine="defender",
            message="scan_pending",
        )
    val = scan_result_tag.strip().lower()
    if val == "no threats found":
        return ScanResult(verdict="clean", engine="defender")
    if val.startswith("malicious"):
        return ScanResult(
            verdict="malicious",
            engine="defender",
            signature=scan_result_tag,
        )
    return ScanResult(
        verdict="unknown", engine="defender", message=scan_result_tag,
    )


def should_fail_closed_on_unknown() -> bool:
    """Whether ``unknown`` verdicts should reject the upload."""
    return bool(getattr(settings, "AV_SCAN_FAIL_CLOSED", False))


# ── ClamAV (clamd INSTREAM) ──────────────────────────────────────────────


def _clamd_instream(host: str, port: int, data: bytes, timeout: float) -> str:
    """Blocking clamd INSTREAM protocol; returns the raw response line.

    The INSTREAM command streams the file in chunks prefixed by a 4-byte
    big-endian length; a zero-length chunk terminates the stream. The
    daemon replies with ``stream: OK`` (clean), ``stream: <SIG> FOUND``
    (malicious), or ``... ERROR``.
    """
    chunk_size = 64 * 1024
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(b"zINSTREAM\0")
        view = memoryview(data)
        for i in range(0, len(view), chunk_size):
            chunk = view[i : i + chunk_size]
            sock.sendall(struct.pack("!L", len(chunk)) + bytes(chunk))
        sock.sendall(struct.pack("!L", 0))

        # Read response until NUL terminator.
        buf = bytearray()
        sock.settimeout(timeout)
        while True:
            piece = sock.recv(4096)
            if not piece:
                break
            buf.extend(piece)
            if b"\0" in piece:
                break
        return buf.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()


async def _scan_with_clamav(data: bytes, filename: str) -> ScanResult:
    host = (getattr(settings, "CLAMAV_HOST", "") or "").strip()
    port = int(getattr(settings, "CLAMAV_PORT", 3310) or 3310)
    timeout = float(getattr(settings, "AV_SCAN_TIMEOUT_S", 30) or 30)

    if not host:
        return ScanResult(
            verdict="unknown",
            engine="clamav",
            message="CLAMAV_HOST not configured",
        )

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_clamd_instream, host, port, data, timeout),
            timeout=timeout + 5,
        )
    except (asyncio.TimeoutError, socket.timeout, OSError) as exc:
        logger.warning("[av] clamav unreachable (%s:%d) for %r: %s",
                       host, port, filename, exc)
        return ScanResult(
            verdict="error",
            engine="clamav",
            message=f"clamav_unreachable: {exc!s}"[:200],
        )

    # Typical responses:
    #   "stream: OK"
    #   "stream: Eicar-Test-Signature FOUND"
    #   "stream: <something> ERROR"
    lowered = response.lower()
    if lowered.endswith(" ok") or lowered == "stream: ok":
        return ScanResult(verdict="clean", engine="clamav")
    if " found" in lowered:
        # signature is the token between ":" and "FOUND"
        sig = response.split(":", 1)[-1].rsplit(" ", 1)[0].strip()
        logger.warning("[av] clamav FOUND signature=%r file=%r", sig, filename)
        return ScanResult(
            verdict="malicious", engine="clamav", signature=sig,
        )
    logger.warning("[av] clamav unexpected response %r for %r", response, filename)
    return ScanResult(
        verdict="error", engine="clamav", message=response[:200],
    )
