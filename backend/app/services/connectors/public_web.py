"""
Mela AI - Public Web Search Connector
Query-time live search using DuckDuckGo (free, no key) with Bing as optional fallback.
Respects an optional domain allowlist. No persistent index — results are fetched at
request time and injected as context into the LLM.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, List
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.connectors.base import ConnectorBase, ConnectorDocument, SOURCE_TYPE_PUBLIC_WEB

logger = logging.getLogger(__name__)


def _doc_id(url: str) -> str:
    return hashlib.sha256(f"web:{url}".encode()).hexdigest()[:40]


def _make_doc(url: str, title: str, snippet: str, workspace_id: str, context_type: str) -> ConnectorDocument:
    return ConnectorDocument(
        id=_doc_id(url),
        source_type=SOURCE_TYPE_PUBLIC_WEB,
        source_id="public_web",
        workspace_id=workspace_id,
        context_type=context_type,
        title=title,
        content=snippet,
        url=url,
        file_type="html",
        last_modified=datetime.now(timezone.utc),
        citation={
            "source": "Web",
            "url": url,
            "title": title,
        },
    )


class PublicWebConnector(ConnectorBase):
    source_type = SOURCE_TYPE_PUBLIC_WEB

    def __init__(self, workspace_id: str, context_type: str = "org") -> None:
        super().__init__(workspace_id, context_type)

    def _is_enabled(self) -> bool:
        return settings.WEB_SEARCH_ENABLED and settings.CONNECTOR_PUBLIC_WEB_ENABLED

    def _is_domain_allowed(self, url: str) -> bool:
        if not settings.WEB_SEARCH_ALLOWLIST:
            return True
        host = urlparse(url).netloc.lower()
        return any(
            host == d.strip() or host.endswith("." + d.strip())
            for d in settings.WEB_SEARCH_ALLOWLIST.split(",")
            if d.strip()
        )

    # Query-time only: sync() always yields nothing.
    async def sync(self, full: bool = False) -> AsyncIterator[ConnectorDocument]:
        if False:
            yield

    # ── DuckDuckGo (primary, free, no API key needed) ────────────────────────

    async def _search_ddg(self, query: str, top_k: int) -> List[ConnectorDocument]:
        """Search using DuckDuckGo — no API key required.

        Supports both `ddgs` (v1+, current name) and `duckduckgo_search` (legacy).
        The sync DDGS.text() call runs in a thread executor to avoid blocking the
        asyncio event loop.
        """
        DDGS_cls = None
        try:
            from ddgs import DDGS as DDGS_cls  # type: ignore  # new package name (v1+)
        except ImportError:
            try:
                from duckduckgo_search import DDGS as DDGS_cls  # type: ignore  # legacy name
            except ImportError:
                logger.warning("Web search not available — install with: pip install ddgs")
                return []

        results: List[ConnectorDocument] = []
        try:
            _cls = DDGS_cls  # capture for lambda

            def _sync_search() -> list:
                return list(_cls().text(query, max_results=top_k))

            raw = await asyncio.get_event_loop().run_in_executor(None, _sync_search)

            for item in raw or []:
                url = item.get("href", "") or item.get("link", "")
                if not url or not self._is_domain_allowed(url):
                    continue
                results.append(_make_doc(
                    url=url,
                    title=item.get("title", url),
                    snippet=item.get("body", ""),
                    workspace_id=self.workspace_id,
                    context_type=self.context_type,
                ))
        except Exception as exc:
            logger.warning("DuckDuckGo search error: %s", exc)
        return results

    # ── Bing (optional fallback if BING_SEARCH_KEY configured) ──────────────

    async def _search_bing(self, query: str, top_k: int) -> List[ConnectorDocument]:
        """Search using Bing — requires BING_SEARCH_KEY env var."""
        bing_key = getattr(settings, "BING_SEARCH_KEY", "")
        if not bing_key:
            return []

        results: List[ConnectorDocument] = []
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://api.bing.microsoft.com/v7.0/search",
                    headers={"Ocp-Apim-Subscription-Key": bing_key},
                    params={"q": query, "count": top_k, "mkt": "en-US"},
                )
                r.raise_for_status()
                data = r.json()
            for item in data.get("webPages", {}).get("value", []):
                url = item.get("url", "")
                if not self._is_domain_allowed(url):
                    continue
                results.append(_make_doc(
                    url=url,
                    title=item.get("name", url),
                    snippet=item.get("snippet", ""),
                    workspace_id=self.workspace_id,
                    context_type=self.context_type,
                ))
        except Exception as exc:
            logger.warning("Bing search error: %s", exc)
        return results

    # ── Public API ────────────────────────────────────────────────────────────

    async def live_search(
        self,
        query: str,
        top_k: int = 6,
        bypass_enabled_check: bool = False,
    ) -> List[ConnectorDocument]:
        """
        Perform a live web search.
        Uses DuckDuckGo by default (free, no key). Falls back to Bing if
        BING_SEARCH_KEY is configured.

        Set bypass_enabled_check=True to allow search even if admin flags
        (WEB_SEARCH_ENABLED / CONNECTOR_PUBLIC_WEB_ENABLED) are off — used
        when the user explicitly enables web search per-request.
        """
        if not bypass_enabled_check and not self._is_enabled():
            logger.debug("Public web search disabled by admin policy")
            return []

        # Try DuckDuckGo first
        results = await self._search_ddg(query, top_k)

        # Fallback to Bing if DDG returned nothing and key is set
        if not results:
            results = await self._search_bing(query, top_k)

        return results

    async def health_check(self) -> bool:
        """Verify DuckDuckGo is reachable."""
        try:
            results = await self._search_ddg("test", 1)
            return True  # even empty list means no error
        except Exception:
            return False
