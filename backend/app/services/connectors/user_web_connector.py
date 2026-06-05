"""
Mela AI - User Web Connector

Crawls a single seed URL provided by an end-user (Agent Memory "Add website").
Unlike OrgWebsiteConnector (which trusts a curated allow-list), this connector
must defend against:

  • SSRF — the user could supply an internal IP, a metadata endpoint, or a
    DNS name that resolves into RFC1918 / link-local / loopback ranges.
  • Runaway crawls — the user might point us at a site with millions of pages.
  • Per-user abuse — one user crawling thousands of sites a day.

Hardening:
  - Block schemes other than http(s).
  - Resolve the hostname *and* every redirect target, then refuse if the
    resolved IP is private / loopback / link-local / multicast / reserved.
  - Honour robots.txt for the seed origin.
  - Cap pages per crawl (settings.AGENT_MEMORY_MAX_PAGES_PER_SITE, default 50)
    and bytes per page (settings.AGENT_MEMORY_MAX_BYTES_PER_PAGE, default 2 MiB).
  - Per-user-per-day quota enforced by check_and_consume_quota().
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
from collections import defaultdict, deque
from datetime import datetime, date, timedelta, timezone
from typing import AsyncIterator, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx

from app.core.config import settings
from app.services.connectors.base import (
    ConnectorBase,
    ConnectorDocument,
    SOURCE_TYPE_PUBLIC_WEB,
)

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "MelaAI-AgentMemoryBot/1.0 (+user-curated)"}

_MAX_PAGES = int(getattr(settings, "AGENT_MEMORY_MAX_PAGES_PER_SITE", 50))
_MAX_BYTES = int(getattr(settings, "AGENT_MEMORY_MAX_BYTES_PER_PAGE", 2 * 1024 * 1024))
_MAX_DEPTH = int(getattr(settings, "AGENT_MEMORY_MAX_CRAWL_DEPTH", 2))
_PER_USER_DAILY_PAGE_QUOTA = int(
    getattr(settings, "AGENT_MEMORY_PER_USER_DAILY_PAGES", 1000)
)
_REQUEST_TIMEOUT_S = 20


# ── SSRF guard ───────────────────────────────────────────────────────────────


def _is_blocked_ip(ip_str: str) -> Tuple[bool, str]:
    """Return (blocked, reason) for an IPv4/IPv6 string."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, f"invalid ip address: {ip_str}"
    if ip.is_loopback:
        return True, "loopback address"
    if ip.is_private:
        return True, "private (RFC1918) address"
    if ip.is_link_local:
        return True, "link-local address"
    if ip.is_multicast:
        return True, "multicast address"
    if ip.is_reserved:
        return True, "reserved address"
    if ip.is_unspecified:
        return True, "unspecified address"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(str(ip.ipv4_mapped))
    return False, ""


def is_safe_public_url(url: str) -> Tuple[bool, str]:
    """Validate a user-supplied URL before we make any outbound request.

    Returns (ok, reason). ok=True means the URL passed all SSRF checks.
    """
    if not url or not isinstance(url, str):
        return False, "empty url"
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False, f"unsupported scheme: {parsed.scheme!r}"
    host = parsed.hostname
    if not host:
        return False, "missing hostname"
    # Block raw IPs in the URL too (after also resolving DNS below).
    try:
        ip_in_host = ipaddress.ip_address(host)
    except ValueError:
        ip_in_host = None
    if ip_in_host is not None:
        blocked, reason = _is_blocked_ip(str(ip_in_host))
        if blocked:
            return False, reason
    # Resolve DNS — refuse if any A/AAAA record resolves to a blocked range.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"dns resolution failed: {exc}"
    for info in infos:
        ip_str = info[4][0]
        blocked, reason = _is_blocked_ip(ip_str)
        if blocked:
            return False, f"{host} resolved to blocked range ({reason})"
    return True, ""


# ── Per-user quota ───────────────────────────────────────────────────────────
# Fast path: Redis INCR with automatic midnight-UTC TTL.
# Fallback: in-process counter (original behaviour; single-replica only).

_quota_used: Dict[str, Dict[date, int]] = defaultdict(lambda: defaultdict(int))


def _seconds_until_midnight_utc() -> int:
    """Seconds remaining until the next UTC midnight (minimum 1)."""
    from datetime import timezone as _tz
    now = datetime.now(_tz.utc)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, int((midnight - now).total_seconds()))


async def check_and_consume_quota_async(user_id: str, pages: int) -> Tuple[bool, int]:
    """Async version with Redis fast path.

    Returns (allowed, remaining_after).  Falls back to the in-process dict
    when Redis is unavailable so callers are always unblocked.
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        from app.core.redis_client import get_redis, key as rkey

        r = await get_redis()
        if r is not None:
            rk = rkey("quota", "crawl", user_id, today_str)
            async with r.pipeline(transaction=True) as pipe:
                pipe.incrby(rk, pages)
                pipe.ttl(rk)
                results = await pipe.execute()
            new_total, existing_ttl = results
            if existing_ttl < 0:
                # Key has no TTL yet — set it to expire at UTC midnight.
                await r.expire(rk, _seconds_until_midnight_utc())
            if new_total > _PER_USER_DAILY_PAGE_QUOTA:
                # Undo the over-consume (DECRBY).
                await r.decrby(rk, pages)
                remaining = max(0, _PER_USER_DAILY_PAGE_QUOTA - (new_total - pages))
                return False, remaining
            return True, max(0, _PER_USER_DAILY_PAGE_QUOTA - new_total)
    except Exception as exc:
        logger.debug("crawl quota Redis error (%s); falling back to in-process", exc)

    # ── In-process fallback ───────────────────────────────────────────────────
    today = datetime.now(timezone.utc).date()
    used = _quota_used[user_id][today]
    if used + pages > _PER_USER_DAILY_PAGE_QUOTA:
        return False, max(0, _PER_USER_DAILY_PAGE_QUOTA - used)
    _quota_used[user_id][today] = used + pages
    return True, _PER_USER_DAILY_PAGE_QUOTA - (used + pages)


def check_and_consume_quota(user_id: str, pages: int) -> Tuple[bool, int]:
    """Synchronous wrapper kept for backward compatibility.

    New async callers should use ``check_and_consume_quota_async`` directly.
    For multi-instance deployments the async version uses Redis; this sync
    version always uses the in-process fallback.
    """
    today = datetime.now(timezone.utc).date()
    used = _quota_used[user_id][today]
    if used + pages > _PER_USER_DAILY_PAGE_QUOTA:
        remaining = max(0, _PER_USER_DAILY_PAGE_QUOTA - used)
        return False, remaining
    _quota_used[user_id][today] = used + pages
    return True, _PER_USER_DAILY_PAGE_QUOTA - (used + pages)


# ── HTML utilities ───────────────────────────────────────────────────────────


def _doc_id(url: str) -> str:
    return hashlib.sha256(f"userweb:{url}".encode()).hexdigest()[:40]


def _extract_html(html: str) -> Tuple[str, str]:
    import html as html_lib

    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = html_lib.unescape(title_m.group(1).strip()) if title_m else ""
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = html_lib.unescape(re.sub(r"\s+", " ", text).strip())
    return title, text[:25000]


# ── Connector ────────────────────────────────────────────────────────────────


class UserWebConnector(ConnectorBase):
    """Crawl a user-supplied seed URL with strict SSRF and quota enforcement."""

    source_type = SOURCE_TYPE_PUBLIC_WEB

    def __init__(
        self,
        workspace_id: str,
        context_type: str,
        seed_url: str,
        *,
        user_id: Optional[str] = None,
        max_pages: int = _MAX_PAGES,
        max_depth: int = _MAX_DEPTH,
    ) -> None:
        super().__init__(workspace_id, context_type)
        self.seed_url = seed_url
        self.user_id = user_id
        self.max_pages = max(1, min(max_pages, 500))
        self.max_depth = max(0, min(max_depth, 5))

    # ── Same-origin gate ─────────────────────────────────────────────────────

    @staticmethod
    def _origin(url: str) -> Tuple[str, str]:
        p = urlparse(url)
        return p.scheme, (p.netloc or "").lower()

    def _is_same_origin(self, candidate: str) -> bool:
        cs, cn = self._origin(candidate)
        ss, sn = self._origin(self.seed_url)
        if cs not in ("http", "https") or ss not in ("http", "https"):
            return False
        # Allow exact host match OR same registered second-level domain.
        if cn == sn:
            return True
        seed_host = sn.lstrip("www.")
        cand_host = cn.lstrip("www.")
        return cand_host.endswith("." + seed_host) or seed_host.endswith("." + cand_host)

    # ── robots.txt ───────────────────────────────────────────────────────────

    async def _robots_disallowed(self, base: str) -> Set[str]:
        paths: Set[str] = set()
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, timeout=10, follow_redirects=False
            ) as c:
                r = await c.get(f"{base.rstrip('/')}/robots.txt")
            if r.status_code == 200:
                for line in r.text.splitlines():
                    if line.lower().startswith("disallow:"):
                        p = line.split(":", 1)[1].strip()
                        if p:
                            paths.add(p)
        except Exception:
            pass
        return paths

    async def _sitemap_urls(self, base: str) -> List[str]:
        urls: List[str] = []
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, timeout=15, follow_redirects=False
            ) as c:
                r = await c.get(f"{base.rstrip('/')}/sitemap.xml")
            if r.status_code != 200:
                return []
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            root = ElementTree.fromstring(r.text)
            for loc in root.findall(".//sm:loc", ns):
                u = (loc.text or "").strip()
                if u and self._is_same_origin(u):
                    urls.append(u)
        except Exception:
            pass
        return urls

    # ── Single-page fetch with SSRF re-check on each redirect target ─────────

    async def _safe_get(self, client: httpx.AsyncClient, url: str) -> Optional[httpx.Response]:
        ok, reason = is_safe_public_url(url)
        if not ok:
            logger.warning("Refusing fetch of unsafe URL %s: %s", url, reason)
            return None
        # Disable auto-redirect so we can SSRF-check each Location header.
        try:
            r = await client.get(url, follow_redirects=False)
        except Exception as exc:
            logger.debug("Fetch error %s: %s", url, exc)
            return None
        hops = 0
        while r.status_code in (301, 302, 303, 307, 308) and hops < 5:
            loc = r.headers.get("location")
            if not loc:
                return r
            next_url = urljoin(url, loc)
            ok, reason = is_safe_public_url(next_url)
            if not ok:
                logger.warning("Blocked redirect from %s to %s: %s", url, next_url, reason)
                return None
            try:
                r = await client.get(next_url, follow_redirects=False)
            except Exception as exc:
                logger.debug("Redirect-fetch error %s: %s", next_url, exc)
                return None
            url = next_url
            hops += 1
        return r

    # ── Main crawl loop ──────────────────────────────────────────────────────

    async def sync(self, full: bool = False) -> AsyncIterator[ConnectorDocument]:
        ok, reason = is_safe_public_url(self.seed_url)
        if not ok:
            logger.warning("Refusing user web crawl: %s", reason)
            return

        # Per-user daily quota (Redis fast path → in-process fallback)
        if self.user_id:
            allowed, remaining = await check_and_consume_quota_async(self.user_id, 1)
            if not allowed:
                logger.warning(
                    "User %s hit daily crawl quota; refusing %s",
                    self.user_id, self.seed_url,
                )
                return

        parsed = urlparse(self.seed_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        disallowed = await self._robots_disallowed(base)
        sitemap = await self._sitemap_urls(base)
        seed_queue: deque[Tuple[str, int]] = deque(
            [(u, 0) for u in (sitemap or [self.seed_url])]
        )

        visited: Set[str] = set()
        pages_emitted = 0

        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=False,
        ) as client:
            while seed_queue and pages_emitted < self.max_pages:
                url, depth = seed_queue.popleft()
                if url in visited:
                    continue
                visited.add(url)
                if any(urlparse(url).path.startswith(d) for d in disallowed):
                    continue

                resp = await self._safe_get(client, url)
                if resp is None or resp.status_code >= 400:
                    continue
                ctype = resp.headers.get("content-type", "")
                if "text/html" not in ctype:
                    continue
                if len(resp.content) > _MAX_BYTES:
                    logger.info("Skipping oversize page (%d bytes): %s",
                                len(resp.content), url)
                    continue
                title, text = _extract_html(resp.text)
                if len(text) < 80:
                    continue

                yield ConnectorDocument(
                    id=_doc_id(url),
                    source_type=self.source_type,
                    source_id=base,
                    workspace_id=self.workspace_id,
                    context_type=self.context_type,
                    title=title or url,
                    content=text,
                    url=url,
                    file_type="html",
                    path=urlparse(url).path,
                    last_modified=datetime.now(timezone.utc),
                    citation={
                        "source": "User-added website",
                        "domain": parsed.netloc,
                        "url": url,
                        "title": title,
                    },
                )
                pages_emitted += 1

                # Consume one more quota unit for each additional page.
                if self.user_id and pages_emitted > 1:
                    allowed, _ = await check_and_consume_quota_async(self.user_id, 1)
                    if not allowed:
                        logger.info(
                            "User %s exhausted quota mid-crawl after %d pages",
                            self.user_id, pages_emitted,
                        )
                        return

                if depth < self.max_depth:
                    for href_raw in re.findall(
                        r'href=["\']([^"\'#\s]+)["\']', resp.text
                    ):
                        if href_raw.lower().startswith(
                            ("mailto:", "tel:", "javascript:", "data:", "ftp:")
                        ):
                            continue
                        nxt = urljoin(url, href_raw)
                        if self._is_same_origin(nxt) and nxt not in visited:
                            seed_queue.append((nxt, depth + 1))

    async def health_check(self) -> bool:
        ok, _ = is_safe_public_url(self.seed_url)
        return ok
