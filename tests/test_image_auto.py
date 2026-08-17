from __future__ import annotations

from dsh_bing_search.models import ImageResult, ImageSearchResponse
from dsh_bing_search.service import _pick_auto


def _result(score: int) -> ImageResult:
    return ImageResult(
        source_id=f"src-{score}", rank=1, murl=f"https://x.com/{score}.jpg", score=score, signals=[]
    )


def _resp(provider: str, scores: list[int]) -> ImageSearchResponse:
    return ImageSearchResponse(
        status="ok",
        provider=provider,
        query="q",
        requested_count=len(scores),
        returned_count=len(scores),
        results=[_result(s) for s in scores],
    )


def test_fast_path_keeps_bing_when_strong() -> None:
    bing = _resp("bing_images", [90, 80])
    commons = _resp("commons", [70])
    picked = _pick_auto(bing, commons, threshold=40)
    assert picked is bing
    assert not any(w.startswith("auto_fallback") for w in picked.warnings)


def test_falls_back_to_commons_when_bing_weak() -> None:
    bing = _resp("bing_images", [20])
    commons = _resp("commons", [85])
    picked = _pick_auto(bing, commons, threshold=40)
    assert picked is commons
    assert any(w.startswith("auto_fallback: bing_images top score 20/100") for w in picked.warnings)


def test_falls_back_when_bing_empty() -> None:
    bing = _resp("bing_images", [])
    commons = _resp("commons", [85])
    picked = _pick_auto(bing, commons, threshold=40)
    assert picked is commons
    assert any(w.startswith("auto_fallback: bing_images returned no usable results") for w in picked.warnings)


def test_keeps_bing_when_commons_not_better() -> None:
    bing = _resp("bing_images", [10])
    commons = _resp("commons", [5])
    picked = _pick_auto(bing, commons, threshold=40)
    assert picked is bing


def test_both_empty_returns_bing() -> None:
    bing = _resp("bing_images", [])
    commons = _resp("commons", [])
    picked = _pick_auto(bing, commons, threshold=40)
    assert picked is bing
