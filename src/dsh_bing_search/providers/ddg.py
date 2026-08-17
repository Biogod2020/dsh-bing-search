from __future__ import annotations

from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from ..config import Settings, settings
from ..http import CurlHttpClient, http_client
from ..models import SearchResponse, SearchResult
from ..url_utils import canonicalize_url, source_id_for, unwrap_ddg_url


def _result_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for prefix in ("m.", "www.", "wap."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    return f"{host}{parts.path.rstrip('/')}"


def _display_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or url

DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
_DDG_BLOCK_MARKERS = (
    "anomaly-modal",
    " unfortunately, bots use duckduckgo too",
    "please complete the following challenge",
)


def parse_ddg_results(html: str, *, count: int) -> list[SearchResult]:
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.select("a.result__a")
    if not anchors:
        anchors = soup.select("a.result-link")

    results: list[SearchResult] = []
    seen: set[str] = set()
    for anchor in anchors:
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        url = canonicalize_url(unwrap_ddg_url(href))
        if not url.startswith(("https://", "http://")):
            continue
        key = _result_key(url)
        if key in seen:
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            continue
        snippet = None
        parent = anchor.find_parent(class_="result")
        if isinstance(parent, Tag):
            snippet_node = parent.select_one(".result__snippet, .result-snippet")
            if snippet_node:
                snippet = " ".join(snippet_node.get_text(" ", strip=True).split()) or None
        seen.add(key)
        results.append(
            SearchResult(
                source_id=source_id_for(url),
                rank=len(results) + 1,
                title=title,
                url=url,
                display_url=_display_url(url),
                snippet=snippet,
            )
        )
        if len(results) >= count:
            break
    return results


def detect_ddg_block(html: str, status_code: int) -> bool:
    if status_code == 202:
        return True
    lowered = html.lower()
    return any(marker in lowered for marker in _DDG_BLOCK_MARKERS)


class DuckDuckGoHtmlProvider:
    def __init__(
        self,
        client: CurlHttpClient = http_client,
        config: Settings = settings,
    ) -> None:
        self.client = client
        self.config = config

    async def probe(self) -> bool:
        try:
            document = await self.client.fetch(
                DDG_SEARCH_URL,
                params={"q": "ping"},
                headers={"Accept-Language": "en-US,en;q=0.9"},
                max_bytes=min(self.config.max_search_bytes, 256 * 1024),
                validate_url=False,
                referer="https://html.duckduckgo.com/",
            )
        except Exception:
            return False
        html = document.text()
        if detect_ddg_block(html, document.status_code):
            return False
        if document.status_code != 200:
            return False
        return bool(parse_ddg_results(html, count=1) or "result" in html.lower())

    async def search(
        self,
        query: str,
        *,
        count: int = 8,
        offset: int = 0,
        market: str = "en-US",
        safe_search: str = "Moderate",
    ) -> SearchResponse:
        language = market.split("-", 1)[0]
        try:
            document = await self.client.fetch(
                DDG_SEARCH_URL,
                params={"q": query, "s": max(offset, 0)},
                headers={"Accept-Language": f"{market},{language};q=0.9,en;q=0.6"},
                max_bytes=self.config.max_search_bytes,
                validate_url=False,
                referer="https://html.duckduckgo.com/",
            )
        except Exception as exc:
            return SearchResponse(
                status="error",
                provider="duckduckgo",
                query=query,
                requested_count=count,
                offset=offset,
                market=market,
                safe_search=safe_search,  # type: ignore[arg-type]
                error=f"network_error: {type(exc).__name__}: {exc}",
            )

        html = document.text()
        common = dict(
            provider="duckduckgo",
            query=query,
            requested_count=count,
            offset=offset,
            market=market,
            safe_search=safe_search,
            elapsed_ms=document.elapsed_ms,
        )
        if detect_ddg_block(html, document.status_code):
            return SearchResponse(
                status="blocked",
                **common,
                error="ddg_challenge: DuckDuckGo returned a challenge or empty bot page",
            )
        if document.status_code >= 400:
            return SearchResponse(
                status="error",
                **common,
                error=f"ddg_http_{document.status_code}",
            )

        results = parse_ddg_results(html, count=count)
        warnings: list[str] = []
        if document.truncated:
            warnings.append("DuckDuckGo result HTML reached the configured byte limit.")
        if not results:
            warnings.append("No organic DuckDuckGo results were parsed.")
        return SearchResponse(
            status="ok",
            **common,
            returned_count=len(results),
            results=results,
            warnings=warnings,
        )
