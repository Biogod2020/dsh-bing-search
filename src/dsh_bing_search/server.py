from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from .models import FindResponse, OpenResponse, SearchResponse
from .service import find_in_webpage, open_web, search_web

mcp = FastMCP(
    name="dsh-bing-search",
    instructions=(
        "Use search to discover sources, open to read a selected source, and find to locate a literal phrase "
        "inside a long source. Prefer multiple independent sources for factual claims."
    ),
    json_response=True,
)


@mcp.tool(name="search", title="Search Bing", structured_output=True)
async def search_tool(
    query: str,
    count: int = 8,
    offset: int = 0,
    market: str = "en-US",
    safe_search: Literal["Strict", "Moderate", "Off"] = "Moderate",
) -> SearchResponse:
    """Search Bing's HTML results with curl_cffi and return normalized organic sources.

    Args:
        query: Search query. Refine it and call again when the first result set is insufficient.
        count: Number of organic results to return, from 1 to 20.
        offset: Result offset for pagination, from 0 to 100.
        market: Bing market such as en-US or zh-CN.
        safe_search: Bing SafeSearch level.
    """
    return await search_web(
        query,
        count=count,
        offset=offset,
        market=market,
        safe_search=safe_search,
    )


@mcp.tool(name="open", title="Open Web Page", structured_output=True)
async def open_tool(url: str, max_chars: int = 24000) -> OpenResponse:
    """Fetch a public HTTP(S) page with curl_cffi and return cleaned readable text.

    Use after search when result snippets are insufficient. Private/local addresses are rejected,
    redirect targets use curl_cffi safe-follow mode, and response bytes are capped.
    """
    return await open_web(url, max_chars=max_chars)


@mcp.tool(name="find", title="Find in Web Page", structured_output=True)
async def find_tool(
    url: str,
    pattern: str,
    max_matches: int = 5,
    context_chars: int = 700,
) -> FindResponse:
    """Find a literal phrase in a page and return compact context windows around matches."""
    return await find_in_webpage(
        url,
        pattern,
        max_matches=max_matches,
        context_chars=context_chars,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
