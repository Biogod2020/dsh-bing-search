from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from ..models import SearchResult
from ..url_utils import canonicalize_url, source_id_for, unwrap_bing_url

_WHITESPACE_RE = re.compile(r"\s+")
_BLOCK_MARKERS = (
    "verify you are human",
    "unusual traffic",
    "captcha",
    "our services aren't available right now",
    "sorry, but we can't serve your request",
    "one last step",
)
_NO_RESULTS_MARKERS = (
    "there are no results for",
    "no results found for",
    "没有与此相关的结果",
    "未找到相关结果",
)


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value).strip()
    return cleaned or None


def detect_bing_block(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _BLOCK_MARKERS)


def detect_no_results(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _NO_RESULTS_MARKERS)


def _first(node: Tag, selectors: tuple[str, ...]) -> Tag | None:
    for selector in selectors:
        found = node.select_one(selector)
        if isinstance(found, Tag):
            return found
    return None


def _is_unresolved_bing_tracking_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    host = (parts.hostname or "").lower().rstrip(".")
    is_bing = host == "bing.com" or host.endswith(".bing.com")
    return is_bing and parts.path.rstrip("/") in {"/ck/a", "/aclick"}


def parse_bing_results(html: str, *, count: int) -> list[SearchResult]:
    """Parse organic Bing result cards while keeping DOM assumptions isolated."""
    soup = BeautifulSoup(html, "lxml")
    nodes = soup.select("#b_results > li.b_algo")
    if not nodes:
        nodes = soup.select("li.b_algo, div.b_algo")

    results: list[SearchResult] = []
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, Tag):
            continue
        anchor = _first(node, ("h2 a[href]", ".b_title a[href]", "a[href]"))
        if anchor is None:
            continue

        direct_url = anchor.get("data-url")
        href = direct_url if isinstance(direct_url, str) and direct_url else anchor.get("href")
        if not isinstance(href, str):
            continue

        url = canonicalize_url(unwrap_bing_url(href))
        if not url.startswith(("https://", "http://")):
            continue
        # Never expose an unresolved Bing tracking/advertising endpoint as a source.
        if _is_unresolved_bing_tracking_url(url):
            continue
        if url in seen:
            continue

        title = clean_text(anchor.get_text(" ", strip=True))
        if not title:
            continue

        snippet_node = _first(
            node,
            (
                ".b_caption p",
                "p.b_lineclamp2",
                ".b_snippet",
                ".b_caption",
                "p",
            ),
        )
        cite_node = _first(node, ("cite", ".b_attribution cite", ".b_attribution"))
        snippet = clean_text(snippet_node.get_text(" ", strip=True) if snippet_node else None)
        display_url = clean_text(cite_node.get_text(" ", strip=True) if cite_node else None)

        seen.add(url)
        results.append(
            SearchResult(
                source_id=source_id_for(url),
                rank=len(results) + 1,
                title=title,
                url=url,
                display_url=display_url,
                snippet=snippet,
            )
        )
        if len(results) >= count:
            break
    return results
