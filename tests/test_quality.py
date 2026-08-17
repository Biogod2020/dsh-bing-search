from dsh_bing_search.models import SearchResult
from dsh_bing_search.quality import apply_quality, score_results


def _hits(*titles: str) -> list[SearchResult]:
    return [
        SearchResult(source_id=f"src_{index}", rank=index, title=title, url=f"https://example.com/{index}")
        for index, title in enumerate(titles, start=1)
    ]


def test_good_overlap() -> None:
    quality = score_results(
        "DeepSeek Harness GitHub",
        _hits("DeepSeek Harness - GitHub"),
    )
    assert quality.label == "good"
    assert quality.score >= 0.55


def test_first_token_collapse_is_poor() -> None:
    quality = score_results(
        "高铁票怎么退",
        _hits("高 （汉字）_百度百科", "高 德地图", "高 姓（中国姓氏）_百度百科"),
    )
    assert quality.label == "poor"
    assert "first_token_collapse" in quality.reasons or "dictionary_titles" in quality.reasons


def test_ticket_dictionary_is_poor() -> None:
    quality = score_results(
        "高铁票怎么退",
        _hits("高 （汉语文字）_百度百科", "高 德地图", "高 的意思"),
    )
    assert quality.label == "poor"


def test_apply_quality_warns_the_model() -> None:
    score, label, warnings = apply_quality(
        "python asyncio timeout",
        _hits("Welcome to Python.org", "Download Python"),
    )
    assert label == "poor"
    assert score < 0.3
    assert any(item.startswith("quality_poor") for item in warnings)
