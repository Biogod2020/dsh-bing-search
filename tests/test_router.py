from __future__ import annotations

from dsh_bing_search.models import SearchResponse, SearchResult
from dsh_bing_search.service import _search_with_providers, probe_duckduckgo


class FakeProvider:
    def __init__(self, response: SearchResponse) -> None:
        self.response = response
        self.calls = 0

    async def search(self, query: str, **kwargs: object) -> SearchResponse:
        self.calls += 1
        return self.response.model_copy(deep=True)


def _ok(
    provider: str,
    title: str = "A Visual Guide to DiffusionGemma - by Maarten Grootendorst",
    query: str = "A Visual Guide to DiffusionGemma",
) -> SearchResponse:
    return SearchResponse(
        status="ok",
        provider=provider,  # type: ignore[arg-type]
        query=query,
        requested_count=3,
        returned_count=1,
        results=[
            SearchResult(
                source_id="src_1",
                rank=1,
                title=title,
                url="https://newsletter.maartengrootendorst.com/p/a-visual-guide-to-diffusiongemma",
            )
        ],
    )


def _junk(provider: str, query: str = "魏子涵 西京医院") -> SearchResponse:
    return SearchResponse(
        status="ok",
        provider=provider,  # type: ignore[arg-type]
        query=query,
        requested_count=3,
        returned_count=1,
        results=[
            SearchResult(
                source_id="src_j",
                rank=1,
                title="魏 （汉字）_百度百科",
                url="https://baike.baidu.com/item/wei",
            )
        ],
    )


async def test_prefers_reachable_ddg(monkeypatch) -> None:
    async def _yes() -> bool:
        return True

    monkeypatch.setattr("dsh_bing_search.service.probe_duckduckgo", _yes)
    ddg = FakeProvider(_ok("duckduckgo"))
    bing = FakeProvider(_ok("bing", "Unrelated Bing Title"))
    response = await _search_with_providers(
        "A Visual Guide to DiffusionGemma",
        count=3,
        offset=0,
        market="en-US",
        safe_search="Moderate",
        ddg=ddg,
        bing=bing,
    )
    assert response.provider == "duckduckgo"
    assert response.quality_label == "good"
    assert ddg.calls == 1
    assert bing.calls == 0


async def test_falls_back_when_ddg_unreachable(monkeypatch) -> None:
    async def _no() -> bool:
        return False

    monkeypatch.setattr("dsh_bing_search.service.probe_duckduckgo", _no)
    ddg = FakeProvider(_ok("duckduckgo"))
    bing = FakeProvider(_ok("bing"))
    response = await _search_with_providers(
        "A Visual Guide to DiffusionGemma",
        count=3,
        offset=0,
        market="en-US",
        safe_search="Moderate",
        ddg=ddg,
        bing=bing,
    )
    assert response.provider == "bing"
    assert ddg.calls == 0
    assert bing.calls == 1
    assert any("duckduckgo_unreachable" in item for item in response.warnings)


async def test_falls_back_when_ddg_quality_is_poor(monkeypatch) -> None:
    async def _yes() -> bool:
        return True

    monkeypatch.setattr("dsh_bing_search.service.probe_duckduckgo", _yes)
    ddg = FakeProvider(_junk("duckduckgo"))
    bing = FakeProvider(_ok("bing", "A Visual Guide to DiffusionGemma", query="魏子涵 西京医院"))
    # bing title won't match 魏 query — both poor; still records fallback
    response = await _search_with_providers(
        "魏子涵 西京医院",
        count=3,
        offset=0,
        market="zh-CN",
        safe_search="Moderate",
        ddg=ddg,
        bing=bing,
    )
    assert bing.calls == 1
    assert any("fell_back_from_duckduckgo" in item or "quality_poor" in item for item in response.warnings)


async def test_probe_is_cached(monkeypatch) -> None:
    calls = {"n": 0}

    async def _probe() -> bool:
        calls["n"] += 1
        return True

    import dsh_bing_search.service as service

    monkeypatch.setattr(service._DDG, "probe", _probe)
    service._ddg_probe_ok = None
    service._ddg_probe_at = 0.0
    assert await probe_duckduckgo(force=True) is True
    assert await probe_duckduckgo() is True
    assert calls["n"] == 1
