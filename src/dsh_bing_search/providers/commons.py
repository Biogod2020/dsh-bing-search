from __future__ import annotations

import json
from typing import Any

from ..config import Settings, settings
from ..http import CurlHttpClient, http_client
from ..models import ImageCandidate

_API_URL = "https://commons.wikimedia.org/w/api.php"


def parse_commons_pages(payload: dict[str, Any], *, count: int) -> list[ImageCandidate]:
    """Map a Commons API ``action=query&generator=search`` payload to candidates."""
    pages = (payload.get("query") or {}).get("pages") or {}
    candidates: list[ImageCandidate] = []
    for page_id in sorted(pages, key=lambda key: int(key) if str(key).lstrip("-").isdigit() else 0):
        page = pages[page_id]
        if not isinstance(page, dict):
            continue
        infos = page.get("imageinfo") or []
        info = infos[0] if infos else None
        if not isinstance(info, dict):
            continue
        mime = info.get("mime") or ""
        if not mime.startswith("image/"):
            continue
        page_title = page.get("title") or ""
        candidates.append(
            ImageCandidate(
                title=page_title,
                murl=info.get("url") or "",
                turl=info.get("thumburl"),
                purl="https://commons.wikimedia.org/wiki/" + page_title.replace(" ", "_"),
                md5=None,
                width=info.get("width"),
                height=info.get("height"),
            )
        )
        if len(candidates) >= count:
            break
    return candidates


class CommonsProvider:
    """Wikimedia Commons search via the public API (curated, licence-clear media)."""

    def __init__(
        self,
        client: CurlHttpClient = http_client,
        config: Settings = settings,
    ) -> None:
        self.client = client
        self.config = config

    async def search(self, query: str, *, count: int) -> list[ImageCandidate]:
        params: dict[str, str | int] = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "6",
            "gsrlimit": min(count, 50),
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
            "iiurlwidth": "640",
            "format": "json",
        }
        headers = {
            "User-Agent": "dsh-bing-search/0.1 (MCP plugin; contact: repository owner)",
        }
        document = await self.client.fetch(
            _API_URL,
            params=params,
            headers=headers,
            max_bytes=self.config.max_search_bytes,
        )
        if document.status_code >= 400:
            raise RuntimeError(f"commons_http_{document.status_code}")
        try:
            payload = json.loads(document.text())
        except Exception as exc:
            raise RuntimeError(f"commons_parse_error: {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("commons_parse_error: unexpected payload shape")
        return parse_commons_pages(payload, count=count)
