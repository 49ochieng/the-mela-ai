"""
Mela AI - Organisation Website Connector
Crawls admin-approved domains via sitemap or link-following.
Respects robots.txt. Never crawls unapproved domains.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional, Set
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx

from app.core.config import settings
from app.services.connectors.base import ConnectorBase, ConnectorDocument, SOURCE_TYPE_ORG_WEBSITE

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "MelaAI-KnowledgeBot/1.0 (internal-enterprise-use)"}


def _doc_id(url: str) -> str:
    return hashlib.sha256(f"orgweb:{url}".encode()).hexdigest()[:40]


def _extract_html(html: str) -> tuple[str, str]:
    """Return (title, body_text) from raw HTML."""
    import html as html_lib
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = html_lib.unescape(title_m.group(1).strip()) if title_m else ""
    clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", clean)
    text = html_lib.unescape(re.sub(r"\s+", " ", text).strip())
    return title, text[:25000]


class OrgWebsiteConnector(ConnectorBase):
    source_type = SOURCE_TYPE_ORG_WEBSITE

    def __init__(
        self,
        workspace_id: str,
        context_type: str = "org",
        allowed_domains: Optional[List[str]] = None,
        crawl_depth: int = 0,
    ) -> None:
        super().__init__(workspace_id, context_type)
        self.allowed_domains = allowed_domains or settings.org_website_domains
        self.crawl_depth = crawl_depth or settings.ORG_WEBSITE_CRAWL_DEPTH

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_allowed(self, url: str) -> bool:
        host = urlparse(url).netloc.lower().lstrip("www.")
        return any(
            host == d.lower().lstrip("www.") or host.endswith("." + d.lower().lstrip("www."))
            for d in self.allowed_domains
        )

    async def _robots_disallowed(self, base_url: str) -> Set[str]:
        paths: Set[str] = set()
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=10, follow_redirects=True) as c:
                r = await c.get(f"{base_url.rstrip('/')}/robots.txt")
                if r.status_code == 200:
                    for line in r.text.splitlines():
                        if line.lower().startswith("disallow:"):
                            p = line.split(":", 1)[1].strip()
                            if p:
                                paths.add(p)
        except Exception:
            pass
        return paths

    async def _sitemap_urls(self, base_url: str) -> List[str]:
        urls: List[str] = []
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=15, follow_redirects=True) as c:
                r = await c.get(f"{base_url.rstrip('/')}/sitemap.xml")
            if r.status_code != 200:
                return []
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            root = ElementTree.fromstring(r.text)
            for loc in root.findall(".//sm:loc", ns):
                u = (loc.text or "").strip()
                if u and self._is_allowed(u):
                    urls.append(u)
        except Exception:
            pass
        return urls

    # ── ConnectorBase interface ───────────────────────────────────────────────

    async def sync(self, full: bool = False) -> AsyncIterator[ConnectorDocument]:
        for domain in self.allowed_domains:
            base = f"https://{domain}" if not domain.startswith("http") else domain
            disallowed = await self._robots_disallowed(base)
            seed_urls = await self._sitemap_urls(base) or [base]

            visited: Set[str] = set()
            queue: List[tuple[str, int]] = [(u, 0) for u in seed_urls]

            while queue:
                url, depth = queue.pop(0)
                if url in visited:
                    continue
                parsed_path = urlparse(url).path
                if any(parsed_path.startswith(d) for d in disallowed):
                    continue
                visited.add(url)

                try:
                    async with httpx.AsyncClient(
                        headers=_HEADERS, timeout=20, follow_redirects=True
                    ) as c:
                        r = await c.get(url)
                    if "text/html" not in r.headers.get("content-type", ""):
                        continue
                    title, text = _extract_html(r.text)
                    if len(text) < 80:
                        continue

                    yield ConnectorDocument(
                        id=_doc_id(url),
                        source_type=self.source_type,
                        source_id=domain,
                        workspace_id=self.workspace_id,
                        context_type=self.context_type,
                        title=title or url,
                        content=text,
                        url=url,
                        file_type="html",
                        path=urlparse(url).path,
                        last_modified=datetime.now(timezone.utc),
                        citation={
                            "source": "Organisation Website",
                            "domain": domain,
                            "url": url,
                            "title": title,
                        },
                    )

                    if depth < self.crawl_depth:
                        # Match ALL hrefs (absolute and relative) then resolve
                        # against the current page URL so /about, ../team, etc.
                        # are all discovered correctly.
                        for href_raw in re.findall(
                            r'href=["\']([^"\'#\s]+)["\']', r.text
                        ):
                            if href_raw.lower().startswith(
                                ("mailto:", "tel:", "javascript:", "data:")
                            ):
                                continue
                            href = urljoin(url, href_raw)
                            if self._is_allowed(href) and href not in visited:
                                queue.append((href, depth + 1))

                except Exception as e:
                    logger.debug("Crawl error %s: %s", url, str(e))

    async def health_check(self) -> bool:
        if not self.allowed_domains:
            return False
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=10, follow_redirects=True) as c:
                r = await c.get(f"https://{self.allowed_domains[0]}")
            return r.status_code < 500
        except Exception:
            return False
