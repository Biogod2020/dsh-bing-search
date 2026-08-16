from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return min(max(value, minimum), maximum)


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    return min(max(value, minimum), maximum)


@dataclass(frozen=True, slots=True)
class Settings:
    bing_search_url: str
    impersonate: str
    proxy: str | None
    timeout_seconds: float
    connect_timeout_seconds: float
    max_body_bytes: int
    max_search_bytes: int
    max_redirects: int
    concurrency: int
    search_cache_ttl_seconds: float
    open_cache_ttl_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bing_search_url=os.getenv("DSH_BING_SEARCH_URL", "https://www.bing.com/search"),
            impersonate=os.getenv("DSH_WEB_IMPERSONATE", "chrome"),
            proxy=os.getenv("DSH_WEB_PROXY") or None,
            timeout_seconds=_env_float(
                "DSH_WEB_TIMEOUT_SECONDS", 20.0, minimum=1.0, maximum=120.0
            ),
            connect_timeout_seconds=_env_float(
                "DSH_WEB_CONNECT_TIMEOUT_SECONDS", 8.0, minimum=1.0, maximum=60.0
            ),
            max_body_bytes=_env_int(
                "DSH_WEB_MAX_BODY_BYTES",
                5 * 1024 * 1024,
                minimum=64 * 1024,
                maximum=25 * 1024 * 1024,
            ),
            max_search_bytes=_env_int(
                "DSH_BING_MAX_BODY_BYTES",
                2 * 1024 * 1024,
                minimum=64 * 1024,
                maximum=10 * 1024 * 1024,
            ),
            max_redirects=_env_int(
                "DSH_WEB_MAX_REDIRECTS", 8, minimum=0, maximum=20
            ),
            concurrency=_env_int(
                "DSH_WEB_CONCURRENCY", 8, minimum=1, maximum=64
            ),
            search_cache_ttl_seconds=_env_float(
                "DSH_BING_CACHE_TTL_SECONDS", 90.0, minimum=0.0, maximum=3600.0
            ),
            open_cache_ttl_seconds=_env_float(
                "DSH_WEB_CACHE_TTL_SECONDS", 600.0, minimum=0.0, maximum=86400.0
            ),
        )


settings = Settings.from_env()
