from __future__ import annotations

import html as _html
import json
import re
from typing import Any

from ..config import Settings, settings
from ..http import CurlHttpClient, http_client
from ..models import ImageCandidate
from .bing_locale import bing_images_url
from .bing_parser import detect_bing_block

_M_BLOB_RE = re.compile(r'\bm="([^"]{40,})"')


def build_headers(market: str) -> dict[str, str]:
    language = market.split("-", 1)[0]
    return {
        "Accept-Language": f"{market},{language};q=0.9,en;q=0.7",
    }


def build_params(query: str, *, market: str, cc_override: str) -> dict[str, str | int]:
    """Bing Images request params; country code comes from the override, else the
    market region (en-US -> cc=US), and is omitted when the market has no region."""
    language = market.split("-", 1)[0]
    params: dict[str, str | int] = {
        "q": query,
        "mkt": market,
        "setlang": language,
        "form": "HDRSC2",
    }
    region = market.split("-", 1)[1] if "-" in market else ""
    cc = (cc_override or region).strip()
    if cc:
        params["cc"] = cc
    return params


class BingImagesProvider:
    """Unofficial Bing Images provider.

    Parses the ``<a class="iusc" m="{...}">`` metadata blobs: each carries the
    original image URL (murl), Bing thumbnail (turl), source page (purl), md5
    and a text title (``t``) that the pure-text ranker consumes.
    """

    def __init__(
        self,
        client: CurlHttpClient = http_client,
        config: Settings = settings,
    ) -> None:
        self.client = client
        self.config = config

    async def search(
        self, query: str, *, count: int, market: str
    ) -> list[ImageCandidate]:
        params = build_params(query, market=market, cc_override=self.config.bing_images_cc)
        headers = build_headers(market)
        document = await self.client.fetch(
            bing_images_url(market, query, self.config.bing_images_url),
            params=params,
            headers=headers,
            max_bytes=self.config.max_search_bytes,
            validate_url=False,
            referer="https://www.bing.com/",
        )
        if document.status_code >= 400:
            raise RuntimeError(f"bing_http_{document.status_code}")
        page = document.text()
        if detect_bing_block(page):
            raise RuntimeError(
                "bing_challenge: Bing returned a challenge page; no bypass was attempted"
            )

        return self._parse_page(page, count=count)

    def _parse_page(self, page: str, *, count: int) -> list[ImageCandidate]:
        """Extract image candidates from a Bing Images result page (offline-testable)."""
        candidates: list[ImageCandidate] = []
        seen: set[str] = set()
        for blob in _M_BLOB_RE.findall(page):
            try:
                data = json.loads(_html.unescape(blob))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            murl = data.get("murl")
            if not isinstance(murl, str) or not murl.startswith(("http://", "https://")):
                continue
            if murl in seen:
                continue
            seen.add(murl)
            title = data.get("t")
            candidates.append(
                ImageCandidate(
                    title=str(title).strip() if isinstance(title, str) and title.strip() else None,
                    murl=murl,
                    turl=_as_str(data.get("turl")),
                    purl=_as_str(data.get("purl")),
                    md5=_as_str(data.get("md5")),
                )
            )
            if len(candidates) >= count:
                break
        return candidates


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
