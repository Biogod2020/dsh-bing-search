from __future__ import annotations

from typing import Protocol

from ..models import SearchResponse


class SearchProvider(Protocol):
    async def search(
        self,
        query: str,
        *,
        count: int,
        offset: int,
        market: str,
        safe_search: str,
    ) -> SearchResponse: ...
