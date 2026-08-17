from __future__ import annotations

from ..config import Settings, settings
from ..http import CurlHttpClient, http_client
from ..models import SearchResponse
from .bing_locale import bing_search_url, bing_setlang
from .bing_parser import detect_bing_block, detect_no_results, parse_bing_results


class BingHtmlProvider:
    """Unofficial Bing HTML provider backed exclusively by curl_cffi."""

    def __init__(
        self,
        client: CurlHttpClient = http_client,
        config: Settings = settings,
    ) -> None:
        self.client = client
        self.config = config

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
        setlang = bing_setlang(market)
        search_url = bing_search_url(market, query, self.config.bing_search_url)
        params: dict[str, str | int] = {
            "q": query,
            "first": offset + 1,
            "mkt": market,
            "setlang": setlang,
        }
        headers = {
            "Accept-Language": f"{market},{language};q=0.9,en;q=0.7",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
        referer = "https://cn.bing.com/" if "cn.bing.com" in search_url else "https://www.bing.com/"

        try:
            document = await self.client.fetch(
                search_url,
                params=params,
                headers=headers,
                max_bytes=self.config.max_search_bytes,
                validate_url=False,
                referer=referer,
            )
        except Exception as exc:
            return SearchResponse(
                status="error",
                query=query,
                requested_count=count,
                offset=offset,
                market=market,
                safe_search=safe_search,  # type: ignore[arg-type]
                error=f"network_error: {type(exc).__name__}: {exc}",
            )

        html = document.text()
        common = dict(
            query=query,
            requested_count=count,
            offset=offset,
            market=market,
            safe_search=safe_search,
            elapsed_ms=document.elapsed_ms,
        )

        if detect_bing_block(html):
            return SearchResponse(
                status="blocked",
                **common,
                error="bing_challenge: Bing returned a challenge page; no bypass was attempted",
            )
        if document.status_code >= 400:
            return SearchResponse(
                status="error",
                **common,
                error=f"bing_http_{document.status_code}",
            )

        results = parse_bing_results(html, count=count)
        warnings: list[str] = []
        if document.truncated:
            warnings.append("Bing result HTML reached the configured byte limit.")
        if not results and not detect_no_results(html):
            warnings.append(
                "No organic results were parsed; Bing markup may have changed or the response may be incomplete."
            )

        return SearchResponse(
            status="ok",
            **common,
            returned_count=len(results),
            results=results,
            warnings=warnings,
        )
