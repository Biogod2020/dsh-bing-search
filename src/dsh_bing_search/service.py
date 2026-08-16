from __future__ import annotations

import asyncio
import re

from .cache import TTLCache
from .config import settings
from .extract import extract_title, html_to_readable_text
from .http import http_client
from .models import FindMatch, FindResponse, OpenResponse, SearchResponse
from .providers.bing import BingHtmlProvider
from .url_utils import canonicalize_url, normalize_market, source_id_for

_SEARCH_CACHE: TTLCache[SearchResponse] = TTLCache(
    maxsize=256, ttl_seconds=settings.search_cache_ttl_seconds
)
_OPEN_CACHE: TTLCache[OpenResponse] = TTLCache(
    maxsize=128, ttl_seconds=settings.open_cache_ttl_seconds
)
_PROVIDER = BingHtmlProvider()
_CONCURRENCY = asyncio.Semaphore(settings.concurrency)


def _collapse_spaces(value: str) -> str:
    return " ".join(value.split())


def _search_cache_key(query: str, count: int, offset: int, market: str, safe_search: str) -> str:
    return "\x1f".join((query, str(count), str(offset), market, safe_search))


async def search_web(
    query: str,
    *,
    count: int = 8,
    offset: int = 0,
    market: str = "en-US",
    safe_search: str = "Moderate",
) -> SearchResponse:
    query = _collapse_spaces(query)
    if not query:
        return SearchResponse(status="error", query="", error="empty_query")
    if len(query) > 512:
        return SearchResponse(status="error", query=query, error="query_too_long")

    count = min(max(int(count), 1), 20)
    offset = min(max(int(offset), 0), 100)
    safe_search = safe_search.title()
    if safe_search not in {"Strict", "Moderate", "Off"}:
        return SearchResponse(
            status="error",
            query=query,
            requested_count=count,
            offset=offset,
            error="safe_search must be Strict, Moderate, or Off",
        )
    try:
        market = normalize_market(market)
    except ValueError as exc:
        return SearchResponse(
            status="error",
            query=query,
            requested_count=count,
            offset=offset,
            error=str(exc),
        )

    key = _search_cache_key(query, count, offset, market, safe_search)
    cached = _SEARCH_CACHE.get(key)
    if cached is not None:
        return cached.model_copy(deep=True)

    async with _CONCURRENCY:
        response = await _PROVIDER.search(
            query,
            count=count,
            offset=offset,
            market=market,
            safe_search=safe_search,
        )
    if response.status == "ok":
        _SEARCH_CACHE.set(key, response.model_copy(deep=True))
    return response


async def open_web(url: str, *, max_chars: int = 24000) -> OpenResponse:
    requested = url.strip()
    if not requested:
        return OpenResponse(status="error", requested_url=requested, error="empty_url")
    if len(requested) > 4096:
        return OpenResponse(status="error", requested_url=requested, error="url_too_long")

    canonical = canonicalize_url(requested)
    if not canonical.startswith(("http://", "https://")):
        return OpenResponse(
            status="error", requested_url=requested, error="only http:// and https:// URLs are allowed"
        )

    max_chars = min(max(int(max_chars), 1000), 100000)
    cache_key = f"{canonical}\x1f{max_chars}"
    cached = _OPEN_CACHE.get(cache_key)
    if cached is not None:
        return cached.model_copy(deep=True)

    try:
        async with _CONCURRENCY:
            document = await http_client.fetch(canonical)
    except Exception as exc:
        return OpenResponse(
            status="error",
            requested_url=requested,
            error=f"fetch_error: {type(exc).__name__}: {exc}",
        )

    if document.status_code >= 400:
        return OpenResponse(
            status="error",
            requested_url=requested,
            final_url=document.final_url,
            content_type=document.content_type,
            fetched_bytes=len(document.body),
            elapsed_ms=document.elapsed_ms,
            error=f"http_{document.status_code}",
        )

    content_type = (document.content_type or "").lower()
    if content_type and not any(
        kind in content_type for kind in ("text/", "html", "xml", "json", "javascript")
    ):
        return OpenResponse(
            status="error",
            requested_url=requested,
            final_url=document.final_url,
            content_type=document.content_type,
            fetched_bytes=len(document.body),
            elapsed_ms=document.elapsed_ms,
            error="unsupported_content_type",
        )

    raw_text = document.text()
    is_html = "html" in content_type or "<html" in raw_text[:2000].lower()
    title = extract_title(raw_text) if is_html else None
    text = html_to_readable_text(raw_text) if is_html else raw_text.replace("\x00", "").strip()
    truncated = document.truncated or len(text) > max_chars
    text = text[:max_chars]
    final_url = canonicalize_url(document.final_url)

    response = OpenResponse(
        status="ok",
        source_id=source_id_for(final_url),
        requested_url=requested,
        final_url=final_url,
        title=title,
        content_type=document.content_type,
        text=text,
        truncated=truncated,
        fetched_bytes=len(document.body),
        elapsed_ms=document.elapsed_ms,
    )
    _OPEN_CACHE.set(cache_key, response.model_copy(deep=True))
    return response


async def find_in_webpage(
    url: str,
    pattern: str,
    *,
    max_matches: int = 5,
    context_chars: int = 700,
) -> FindResponse:
    pattern = pattern.strip()
    if not pattern:
        return FindResponse(status="error", url=url, pattern="", error="empty_pattern")
    if len(pattern) > 256:
        return FindResponse(status="error", url=url, pattern=pattern, error="pattern_too_long")

    opened = await open_web(url, max_chars=100000)
    if opened.status != "ok" or not opened.text:
        return FindResponse(
            status=opened.status,
            source_id=opened.source_id,
            url=opened.final_url or url,
            pattern=pattern,
            error=opened.error,
        )

    max_matches = min(max(int(max_matches), 1), 20)
    context_chars = min(max(int(context_chars), 200), 3000)
    matcher = re.compile(re.escape(pattern), re.IGNORECASE)
    all_matches = list(matcher.finditer(opened.text))
    matches: list[FindMatch] = []
    for index, match in enumerate(all_matches[:max_matches], start=1):
        left = max(0, match.start() - context_chars // 2)
        right = min(len(opened.text), match.end() + context_chars // 2)
        matches.append(
            FindMatch(
                index=index,
                start=match.start(),
                end=match.end(),
                text=opened.text[left:right].strip(),
            )
        )

    return FindResponse(
        status="ok",
        source_id=opened.source_id,
        url=opened.final_url or url,
        pattern=pattern,
        total_matches=len(all_matches),
        matches=matches,
    )
