from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Mapping

from curl_cffi import AsyncSession, CurlFollow

from .config import Settings, settings
from .url_utils import validate_public_http_url

_CHARSET_RE = re.compile(r"charset\s*=\s*['\"]?([^;\s'\"]+)", re.IGNORECASE)
_META_CHARSET_RE = re.compile(
    rb"<meta[^>]+charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)", re.IGNORECASE
)


@dataclass(slots=True)
class HttpDocument:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    truncated: bool
    elapsed_ms: int

    def text(self) -> str:
        content_type_match = _CHARSET_RE.search(self.content_type or "")
        if content_type_match:
            candidates = [content_type_match.group(1)]
        else:
            meta_match = _META_CHARSET_RE.search(self.body[:8192])
            candidates = [meta_match.group(1).decode("ascii")] if meta_match else []
        candidates.extend(["utf-8", "gb18030", "windows-1252"])

        for encoding in candidates:
            try:
                return self.body.decode(encoding, errors="strict")
            except (LookupError, UnicodeDecodeError):
                continue
        return self.body.decode("utf-8", errors="replace")


class CurlHttpClient:
    """Bounded curl_cffi fetcher used for both Bing and ordinary web pages."""

    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    async def fetch(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
        validate_url: bool = True,
        referer: str | None = None,
    ) -> HttpDocument:
        if validate_url:
            await validate_public_http_url(url)

        byte_limit = max_bytes or self.config.max_body_bytes
        request_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,application/json;q=0.7,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if headers:
            request_headers.update(headers)

        started = time.perf_counter()
        async with AsyncSession(
            impersonate=self.config.impersonate,
            timeout=(self.config.connect_timeout_seconds, self.config.timeout_seconds),
            proxy=self.config.proxy,
            trust_env=False,
            allow_redirects=CurlFollow.SAFE,
            max_redirects=self.config.max_redirects,
            max_clients=self.config.concurrency,
        ) as session:
            async with session.stream(
                "GET",
                url,
                params=dict(params) if params else None,
                headers=request_headers,
                referer=referer,
            ) as response:
                body = bytearray()
                truncated = False
                async for chunk in response.aiter_content():
                    if not chunk:
                        continue
                    remaining = byte_limit - len(body)
                    if remaining <= 0:
                        truncated = True
                        break
                    if len(chunk) > remaining:
                        body.extend(chunk[:remaining])
                        truncated = True
                        break
                    body.extend(chunk)

                final_url = str(response.url)
                status_code = int(response.status_code)
                content_type = response.headers.get("content-type", "")

        return HttpDocument(
            requested_url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            body=bytes(body),
            truncated=truncated,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )


http_client = CurlHttpClient()
