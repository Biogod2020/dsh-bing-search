from __future__ import annotations

import asyncio
import re
import time

from .cache import TTLCache
from .config import settings
from .extract import extract_title, html_to_readable_text
from .http import http_client
from .models import (
    FindMatch,
    FindResponse,
    ImageSearchResponse,
    OpenResponse,
    SearchResponse,
)
from .providers.base import SearchProvider
from .providers.bing import BingHtmlProvider
from .providers.bing_images import BingImagesProvider
from .providers.commons import CommonsProvider
from .providers.ddg import DuckDuckGoHtmlProvider
from .quality import apply_quality
from .ranking import low_score_threshold, rank_candidates
from .url_utils import canonicalize_url, normalize_market, source_id_for

_SEARCH_CACHE: TTLCache[SearchResponse] = TTLCache(
    maxsize=256, ttl_seconds=settings.search_cache_ttl_seconds
)
_OPEN_CACHE: TTLCache[OpenResponse] = TTLCache(
    maxsize=128, ttl_seconds=settings.open_cache_ttl_seconds
)
_BING = BingHtmlProvider()
_DDG = DuckDuckGoHtmlProvider()
_CONCURRENCY = asyncio.Semaphore(settings.concurrency)
_BING_IMAGES = BingImagesProvider()
_COMMONS = CommonsProvider()
_IMAGE_CACHE: TTLCache[ImageSearchResponse] = TTLCache(
    maxsize=128, ttl_seconds=settings.open_cache_ttl_seconds
)
_DDG_PROBE_TTL = 60.0
_ddg_probe_ok: bool | None = None
_ddg_probe_at = 0.0


async def probe_duckduckgo(*, force: bool = False) -> bool:
    """Cache a cheap DDG HTML ping so we do not probe on every search."""
    global _ddg_probe_ok, _ddg_probe_at
    now = asyncio.get_running_loop().time()
    if not force and _ddg_probe_ok is not None and (now - _ddg_probe_at) < _DDG_PROBE_TTL:
        return _ddg_probe_ok
    ok = await _DDG.probe()
    _ddg_probe_ok = ok
    _ddg_probe_at = now
    return ok


def _annotate(response: SearchResponse) -> SearchResponse:
    score, label, extra = apply_quality(response.query, response.results)
    response.quality_score = score
    response.quality_label = label
    if extra:
        response.warnings = list(response.warnings) + extra
    return response


def _usable(response: SearchResponse) -> bool:
    return (
        response.status == "ok"
        and bool(response.results)
        and response.quality_label != "poor"
    )


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
        response = await _search_with_providers(
            query,
            count=count,
            offset=offset,
            market=market,
            safe_search=safe_search,
            ddg=_DDG,
            bing=_BING,
        )
    if response.status == "ok":
        _SEARCH_CACHE.set(key, response.model_copy(deep=True))
    return response


async def _search_with_providers(
    query: str,
    *,
    count: int,
    offset: int,
    market: str,
    safe_search: str,
    ddg: SearchProvider,
    bing: SearchProvider,
) -> SearchResponse:
    global _ddg_probe_ok
    ddg_ok = await probe_duckduckgo()
    ddg_response: SearchResponse | None = None
    if ddg_ok:
        ddg_response = _annotate(
            await ddg.search(
                query,
                count=count,
                offset=offset,
                market=market,
                safe_search=safe_search,
            )
        )
        if _usable(ddg_response):
            return ddg_response
        if ddg_response.status in {"blocked", "error"}:
            _ddg_probe_ok = False

    bing_response = _annotate(
        await bing.search(
            query,
            count=count,
            offset=offset,
            market=market,
            safe_search=safe_search,
        )
    )
    if ddg_response is None:
        bing_response.warnings = [
            "duckduckgo_unreachable: used Bing",
            *bing_response.warnings,
        ]
        return bing_response

    reason = ddg_response.error or ",".join(ddg_response.warnings) or ddg_response.quality_label
    bing_response.warnings = [
        f"fell_back_from_duckduckgo: {reason}",
        *bing_response.warnings,
    ]
    if _usable(bing_response) or not _usable(ddg_response):
        if ddg_response.quality_score > bing_response.quality_score and ddg_response.results:
            ddg_response.warnings = [
                *ddg_response.warnings,
                "bing_fallback_was_worse; returning DuckDuckGo results",
            ]
            return ddg_response
        return bing_response
    return ddg_response


def _image_cache_key(query: str, count: int, provider: str, market: str) -> str:
    return "\x1f".join((query, str(count), provider, market))


def _pick_auto(
    bing: ImageSearchResponse,
    commons: ImageSearchResponse,
    *,
    threshold: int,
) -> ImageSearchResponse:
    """Choose the better of the two provider responses (pure score comparison)."""
    bing_top = bing.results[0].score if bing.returned_count else None
    commons_top = commons.results[0].score if commons.returned_count else None
    if bing_top is not None and bing_top >= threshold:
        return bing
    if commons_top is not None and (bing_top is None or commons_top > bing_top):
        reason = (
            f"bing_images top score {bing_top}/100 below {threshold}"
            if bing_top is not None
            else "bing_images returned no usable results"
        )
        commons.warnings = [
            f"auto_fallback: {reason}; used commons",
            *commons.warnings,
        ]
        return commons
    return bing


async def _search_images_one(
    provider: str,
    query: str,
    *,
    count: int,
    market: str,
) -> ImageSearchResponse:
    """Single-provider image search with its own cache entry."""
    key = _image_cache_key(query, count, provider, market)
    cached = _IMAGE_CACHE.get(key)
    if cached is not None:
        return cached.model_copy(deep=True)

    started = time.perf_counter()
    try:
        async with _CONCURRENCY:
            if provider == "bing_images":
                candidates = await _BING_IMAGES.search(query, count=count, market=market)
            else:
                candidates = await _COMMONS.search(query, count=count)
    except Exception as exc:
        status = "blocked" if str(exc).startswith("bing_challenge") else "error"
        return ImageSearchResponse(
            status=status, provider=provider, query=query,
            requested_count=count, market=market,
            error=f"provider_error: {type(exc).__name__}: {exc}",
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    results = rank_candidates(query, candidates)
    if not results:
        response = ImageSearchResponse(
            status="ok", provider=provider, query=query,
            requested_count=count, returned_count=0, market=market, results=[],
            warnings=["no_image_results: the provider returned no parseable image entries"],
            elapsed_ms=elapsed_ms,
        )
        _IMAGE_CACHE.set(key, response.model_copy(deep=True))
        return response
    warnings: list[str] = []
    if results[0].score < low_score_threshold():
        warnings.append(
            f"top_score_low ({results[0].score}/100): the best match is text-weak; "
            "refine the query, switch provider, or verify the image before use"
        )
    response = ImageSearchResponse(
        status="ok", provider=provider, query=query,
        requested_count=count, returned_count=len(results),
        market=market, results=results, warnings=warnings,
        elapsed_ms=elapsed_ms,
    )
    _IMAGE_CACHE.set(key, response.model_copy(deep=True))
    return response


async def search_images_web(
    query: str,
    *,
    count: int = 8,
    market: str = "en-US",
    provider: str = "auto",
) -> ImageSearchResponse:
    """Search images and rank them with pure text for non-vision models.

    provider="auto" tries Bing Images first and transparently falls back to
    Wikimedia Commons when the top text score is below the threshold, so one
    call yields a usable, ranked set even when Bing is text-weak.
    """
    query = _collapse_spaces(query)
    if not query:
        return ImageSearchResponse(status="error", provider="auto", query="", error="empty_query")
    if len(query) > 512:
        return ImageSearchResponse(
            status="error", provider="auto", query=query, error="query_too_long"
        )
    count = min(max(int(count), 1), 20)
    if provider not in {"auto", "bing_images", "commons"}:
        return ImageSearchResponse(
            status="error", provider="auto", query=query,
            requested_count=count, market=market,
            error="provider must be auto, bing_images, or commons",
        )
    try:
        market = normalize_market(market)
    except ValueError as exc:
        return ImageSearchResponse(
            status="error", provider="auto", query=query,
            requested_count=count, market=market, error=str(exc),
        )

    key = _image_cache_key(query, count, provider, market)
    cached = _IMAGE_CACHE.get(key)
    if cached is not None:
        return cached.model_copy(deep=True)

    if provider == "auto":
        bing = await _search_images_one("bing_images", query, count=count, market=market)
        if bing.returned_count and bing.results[0].score >= low_score_threshold():
            response = bing
        else:
            commons = await _search_images_one("commons", query, count=count, market=market)
            response = _pick_auto(bing, commons, threshold=low_score_threshold())
    else:
        response = await _search_images_one(provider, query, count=count, market=market)

    _IMAGE_CACHE.set(key, response.model_copy(deep=True))
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
