"""Tests for the public web SSRF guard in the agent-memory user web connector."""

from unittest.mock import patch

import pytest

from app.services.connectors.user_web_connector import is_safe_public_url


# ── URL syntax ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "ftp://example.com/x",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "gopher://example.com",
    "",
    "not-a-url",
])
def test_rejects_non_http_schemes(url):
    ok, reason = is_safe_public_url(url)
    assert ok is False, f"expected reject for {url}: {reason}"


def test_rejects_url_without_host():
    ok, _ = is_safe_public_url("http:///nohost")
    assert ok is False


# ── DNS-resolved address checks ──────────────────────────────────────────────


def _patch_addrinfo(addresses):
    """Patch socket.getaddrinfo to return fake A/AAAA records."""
    def fake(host, port, *args, **kwargs):
        # Each entry: (family, type, proto, canonname, sockaddr)
        out = []
        for fam, addr in addresses:
            sockaddr = (addr, port) if fam == 2 else (addr, port, 0, 0)
            out.append((fam, 1, 6, "", sockaddr))
        return out
    return patch("socket.getaddrinfo", side_effect=fake)


@pytest.mark.parametrize("addr", [
    "127.0.0.1",       # loopback
    "10.0.0.5",        # private
    "192.168.1.1",     # private
    "172.16.0.1",      # private
    "169.254.169.254", # link-local — Azure/AWS metadata
    "0.0.0.0",         # unspecified
    "224.0.0.1",       # multicast
    "240.0.0.1",       # reserved
])
def test_blocks_private_ipv4_addresses(addr):
    with _patch_addrinfo([(2, addr)]):
        ok, reason = is_safe_public_url("https://attacker.example.com/")
    assert ok is False, f"should block {addr}: got {reason}"


@pytest.mark.parametrize("addr", [
    "::1",                           # IPv6 loopback
    "fc00::1",                       # IPv6 unique local
    "fe80::1",                       # IPv6 link-local
    "::ffff:127.0.0.1",              # IPv4-mapped IPv6 → loopback
    "::ffff:169.254.169.254",        # IPv4-mapped → metadata
])
def test_blocks_private_ipv6_addresses(addr):
    with _patch_addrinfo([(10, addr)]):
        ok, _ = is_safe_public_url("https://attacker.example.com/")
    assert ok is False


def test_blocks_when_any_resolved_address_is_private():
    # Multi-A trick: one public, one private. Must reject (fail-closed).
    with _patch_addrinfo([(2, "8.8.8.8"), (2, "10.0.0.1")]):
        ok, _ = is_safe_public_url("https://multi.example.com/")
    assert ok is False


def test_allows_clearly_public_address():
    with _patch_addrinfo([(2, "8.8.8.8")]):
        ok, _ = is_safe_public_url("https://example.com/page")
    assert ok is True


def test_blocks_when_dns_fails():
    import socket
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("nxdomain")):
        ok, _ = is_safe_public_url("https://example.invalid/")
    assert ok is False


# ── Quota counter ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_per_user_quota_is_enforced(monkeypatch):
    from app.services.connectors import user_web_connector as uwc
    monkeypatch.setattr(uwc, "_PER_USER_DAILY_PAGE_QUOTA", 5)
    # Reset the in-process counter so other tests don't bleed in
    uwc._quota_used.clear()
    from app.services.connectors.user_web_connector import check_and_consume_quota
    ok, remaining = check_and_consume_quota("user-x", 3)
    assert ok is True and remaining == 2
    ok, remaining = check_and_consume_quota("user-x", 2)
    assert ok is True and remaining == 0
    ok, remaining = check_and_consume_quota("user-x", 1)
    assert ok is False and remaining == 0
    # different user has their own bucket
    ok, _ = check_and_consume_quota("user-y", 5)
    assert ok is True
