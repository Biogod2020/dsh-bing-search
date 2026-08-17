from __future__ import annotations

import warnings
from typing import Literal

# fastmcp's server model carries a `lifespan` field with an unresolved forward
# reference; pydantic_settings warns about it on every startup
# (IncompleteFieldDefinitionWarning) even though it resolves fine at runtime.
# Silence it so the stdio server starts with a clean stderr — anything written
# to stdout would corrupt the MCP stdio protocol.
warnings.filterwarnings(
    "ignore",
    message=r"Field 'lifespan' has an incomplete definition",
    category=UserWarning,
)

from mcp.server.fastmcp import FastMCP

from .models import FindResponse, ImageSearchResponse, OpenResponse, SearchResponse
from .service import find_in_webpage, open_web, search_images_web, search_web

mcp = FastMCP(
    name="dsh-bing-search",
    instructions=(
        "Use search to discover sources, open to read a selected source, and find to locate a literal phrase "
        "inside a long source. Prefer multiple independent sources for factual claims. "
        "If quality_label is poor, do not cite the titles; refine the query or use a specialized corpus. "
        "search_images returns image URLs ranked 0-100 by pure text (query overlap + source-domain reputation) "
        "with explainable signals; prefer the highest score and treat anything below ~40 as unverified. "
        "provider=auto falls back to Wikimedia Commons when Bing Images is text-weak."
    ),
    json_response=True,
)


@mcp.tool(name="search", title="Search the Web", structured_output=True)
async def search_tool(
    query: str,
    count: int = 8,
    offset: int = 0,
    market: str = "en-US",
    safe_search: Literal["Strict", "Moderate", "Off"] = "Moderate",
) -> SearchResponse:
    """Search the public web. DuckDuckGo is tried first when reachable; Bing is the fallback.

    Chinese queries / zh-* markets use cn.bing.com. Read quality_label: poor means the
    titles are unrelated or first-token junk — do not treat them as answers.

    Args:
        query: Search query. Keep it short. For a person plus a paper, search the author name first.
        count: Number of organic results to return, from 1 to 20.
        offset: Result offset for pagination, from 0 to 100.
        market: Locale such as en-US or zh-CN. Chinese text should use zh-CN.
        safe_search: SafeSearch level (used when Bing is the engine).
    """
    return await search_web(
        query,
        count=count,
        offset=offset,
        market=market,
        safe_search=safe_search,
    )


@mcp.tool(name="search_images", title="Search Images", structured_output=True)
async def search_images_tool(
    query: str,
    count: int = 10,
    market: str = "en-US",
    provider: Literal["auto", "bing_images", "commons"] = "auto",
) -> ImageSearchResponse:
    """Search image indexes and rank results with pure text so vision is not required.

    auto (default) tries Bing Images first and falls back to Wikimedia Commons
    when the top text score is below ~40, so one call yields a ranked set.
    bing_images parses Bing Images metadata (original URL / thumbnail / source
    page / title). commons queries Wikimedia Commons, a curated and
    licence-clear platform. Every result carries a 0-100 text score, a domain
    hint and explainable signals; pick the highest score, treat scores below
    ~40 as unverified, and optionally verify with `find`/`open` on the source
    page before downloading.

    Args:
        query: What the image should depict. Keep it specific.
        count: Number of ranked image results to return, from 1 to 20.
        market: Locale such as en-US or zh-CN (Bing Images; Commons is language-neutral).
        provider: auto (default), bing_images, or commons.
    """
    return await search_images_web(
        query,
        count=count,
        market=market,
        provider=provider,
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
