from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_DEFAULT_HOSTS = {
    "https://www.bing.com/search",
    "https://cn.bing.com/search",
}


def prefers_chinese(market: str, query: str) -> bool:
    return market.lower().startswith("zh") or bool(_CJK_RE.search(query))


def bing_search_url(market: str, query: str, configured_url: str) -> str:
    """Pick cn.bing.com for Chinese, www.bing.com otherwise.

    An explicit non-default DSH_BING_SEARCH_URL still wins (tests / debugging).
    """
    if configured_url.rstrip("/") not in _DEFAULT_HOSTS:
        return configured_url
    if prefers_chinese(market, query):
        return "https://cn.bing.com/search"
    return "https://www.bing.com/search"


def bing_setlang(market: str) -> str:
    lowered = market.lower()
    if lowered in {"zh-tw", "zh-hk", "zh-mo"}:
        return "zh-Hant"
    if lowered.startswith("zh"):
        return "zh-Hans"
    return market.split("-", 1)[0]
