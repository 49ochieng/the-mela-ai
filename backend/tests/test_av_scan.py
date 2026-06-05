"""
Phase 6 / M-5 — Antivirus scan service tests.

Covers the ``av_scan_service`` module in isolation:

  * Disabled backend (default) → verdict ``unknown``.
  * ClamAV INSTREAM happy path — uses a tiny in-process socket server
    that mimics the clamd wire protocol and replies with either
    ``stream: OK`` or ``stream: Eicar-Test-Signature FOUND``.
  * ClamAV unreachable → verdict ``error``.
  * ``scan_blob_tag`` mapping for Microsoft Defender for Storage.
  * Oversize file → verdict ``unknown`` (skipped, never streamed).
  * ``should_fail_closed_on_unknown`` honours the settings flag.
"""

from __future__ import annotations

import socket
import struct
import threading
from typing import Optional

import pytest

from app.core.config import settings
from app.services import av_scan_service as avs


# ── Fake clamd ────────────────────────────────────────────────────────────


class FakeClamd:
    """In-process clamd that replies based on the streamed payload."""

    def __init__(self, response: bytes = b"stream: OK\0"):
        self.response = response
        self.received: bytearray = bytearray()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            # consume command line up to NUL
            cmd = bytearray()
            while True:
                b = conn.recv(1)
                if not b or b == b"\0":
                    break
                cmd.extend(b)
            # read chunks: 4-byte BE length + payload, terminated by zero-length
            while True:
                hdr = self._recv_exact(conn, 4)
                if not hdr:
                    break
                (n,) = struct.unpack("!L", hdr)
                if n == 0:
                    break
                chunk = self._recv_exact(conn, n)
                self.received.extend(chunk)
            conn.sendall(self.response)

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            piece = conn.recv(n - len(buf))
            if not piece:
                return bytes(buf)
            buf.extend(piece)
        return bytes(buf)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def fake_clamd_clean():
    srv = FakeClamd(response=b"stream: OK\0")
    yield srv
    srv.close()


@pytest.fixture
def fake_clamd_eicar():
    srv = FakeClamd(response=b"stream: Eicar-Test-Signature FOUND\0")
    yield srv
    srv.close()


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    monkeypatch.setattr(settings, "AV_SCAN_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_BACKEND", "disabled", raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_FAIL_CLOSED", False, raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_TIMEOUT_S", 5, raising=False)
    monkeypatch.setattr(
        settings, "AV_SCAN_MAX_BYTES", 25 * 1024 * 1024, raising=False,
    )
    monkeypatch.setattr(settings, "CLAMAV_HOST", "", raising=False)
    monkeypatch.setattr(settings, "CLAMAV_PORT", 3310, raising=False)


# ── Disabled backend ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_returns_unknown():
    r = await avs.scan_bytes(b"hello", "x.txt")
    assert r.verdict == "unknown"
    assert r.engine == "disabled"
    assert not r.is_malicious and not r.is_clean


@pytest.mark.asyncio
async def test_enabled_unknown_backend_returns_unknown(monkeypatch):
    monkeypatch.setattr(settings, "AV_SCAN_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_BACKEND", "disabled", raising=False)
    r = await avs.scan_bytes(b"hello")
    assert r.verdict == "unknown"


# ── ClamAV ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clamav_clean(monkeypatch, fake_clamd_clean):
    monkeypatch.setattr(settings, "AV_SCAN_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_BACKEND", "clamav", raising=False)
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1", raising=False)
    monkeypatch.setattr(
        settings, "CLAMAV_PORT", fake_clamd_clean.port, raising=False,
    )

    payload = b"harmless file body"
    r = await avs.scan_bytes(payload, "ok.txt")
    assert r.verdict == "clean", r
    assert r.engine == "clamav"
    # The fake daemon should have received exactly the payload.
    assert bytes(fake_clamd_clean.received) == payload


@pytest.mark.asyncio
async def test_clamav_malicious_eicar(monkeypatch, fake_clamd_eicar):
    monkeypatch.setattr(settings, "AV_SCAN_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_BACKEND", "clamav", raising=False)
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1", raising=False)
    monkeypatch.setattr(
        settings, "CLAMAV_PORT", fake_clamd_eicar.port, raising=False,
    )

    # The well-known EICAR test signature payload (safe — recognised by
    # every AV vendor as the standard "fake virus" file).
    eicar = (
        rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-"
        rb"STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    )
    r = await avs.scan_bytes(eicar, "eicar.com")
    assert r.verdict == "malicious"
    assert r.engine == "clamav"
    assert r.signature and "eicar" in r.signature.lower()
    assert r.is_malicious


@pytest.mark.asyncio
async def test_clamav_unreachable_returns_error(monkeypatch):
    monkeypatch.setattr(settings, "AV_SCAN_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_BACKEND", "clamav", raising=False)
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1", raising=False)
    # Pick a port nothing is listening on.
    monkeypatch.setattr(settings, "CLAMAV_PORT", 1, raising=False)

    r = await avs.scan_bytes(b"x")
    assert r.verdict == "error"
    assert r.engine == "clamav"


@pytest.mark.asyncio
async def test_clamav_missing_host_returns_unknown(monkeypatch):
    monkeypatch.setattr(settings, "AV_SCAN_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_BACKEND", "clamav", raising=False)
    monkeypatch.setattr(settings, "CLAMAV_HOST", "", raising=False)
    r = await avs.scan_bytes(b"x")
    assert r.verdict == "unknown"
    assert r.engine == "clamav"


# ── Defender blob-tag mapping ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tag,expected",
    [
        ("No threats found", "clean"),
        ("no threats found", "clean"),
        ("Malicious", "malicious"),
        ("Malicious - Eicar-Test", "malicious"),
        (None, "unknown"),
        ("", "unknown"),
        ("Pending", "unknown"),
    ],
)
async def test_defender_tag_mapping(tag: Optional[str], expected: str):
    r = await avs.scan_blob_tag(tag)
    assert r.verdict == expected
    assert r.engine == "defender"


# ── Oversize / config helpers ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_oversize_file_skipped(monkeypatch):
    monkeypatch.setattr(settings, "AV_SCAN_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_BACKEND", "clamav", raising=False)
    monkeypatch.setattr(settings, "AV_SCAN_MAX_BYTES", 100, raising=False)
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1", raising=False)

    r = await avs.scan_bytes(b"a" * 200, "big.bin")
    assert r.verdict == "unknown"
    assert r.message == "file_too_large_for_scan"


def test_should_fail_closed_on_unknown(monkeypatch):
    monkeypatch.setattr(settings, "AV_SCAN_FAIL_CLOSED", False, raising=False)
    assert avs.should_fail_closed_on_unknown() is False
    monkeypatch.setattr(settings, "AV_SCAN_FAIL_CLOSED", True, raising=False)
    assert avs.should_fail_closed_on_unknown() is True
