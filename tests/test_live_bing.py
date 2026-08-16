from __future__ import annotations

import os

import pytest

from dsh_bing_search.service import search_web


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_bing_search() -> None:
    if os.getenv("RUN_LIVE_BING") != "1":
        pytest.skip("set RUN_LIVE_BING=1 to enable the live Bing request")

    response = await search_web("DeepSeek Harness GitHub", count=5, market="en-US")
    assert response.status == "ok", response.model_dump_json(indent=2)
    assert response.results, response.model_dump_json(indent=2)
    assert all(item.url.startswith(("http://", "https://")) for item in response.results)
