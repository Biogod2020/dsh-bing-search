from dsh_bing_search.models import SearchResult
from dsh_bing_search.quality import apply_quality, score_results


def _hits(*titles: str) -> list[SearchResult]:
    return [
        SearchResult(source_id=f"src_{index}", rank=index, title=title, url=f"https://example.com/{index}")
        for index, title in enumerate(titles, start=1)
    ]


def test_good_overlap() -> None:
    quality = score_results(
        "A Visual Guide to DiffusionGemma",
        _hits("A Visual Guide to DiffusionGemma - by Maarten Grootendorst"),
    )
    assert quality.label == "good"
    assert quality.score >= 0.55


def test_first_token_collapse_is_poor() -> None:
    quality = score_results(
        "魏子涵 西京医院 神经内科",
        _hits("魏 （汉字）_百度百科", "WEY - 魏 牌官网", "魏 姓（中国姓氏之一）_百度百科"),
    )
    assert quality.label == "poor"
    assert "first_token_collapse" in quality.reasons or "dictionary_titles" in quality.reasons


def test_ticket_dictionary_is_poor() -> None:
    quality = score_results(
        "高铁票怎么退",
        _hits("高 （汉语文字）_百度百科", "高 德地图", "高 的意思"),
    )
    assert quality.label == "poor"


def test_missing_person_name_is_called_out() -> None:
    quality = score_results(
        "魏子涵 颞叶癫痫 脑电微状态 空军军医大学",
        _hits(
            "颞叶癫痫中的脑电图微状态分析：一项按手术结果分层的比较研究",
            "脑电微状态分析在癫痫中的研究进展",
        ),
    )
    assert "魏子涵" in quality.missing
    assert "空军军医大学" in quality.missing
    _, _, warnings = apply_quality(
        "魏子涵 颞叶癫痫 脑电微状态 空军军医大学",
        _hits("颞叶癫痫中的脑电图微状态分析"),
    )
    assert any("魏子涵" in item for item in warnings)


def test_apply_quality_warns_the_model() -> None:
    score, label, warnings = apply_quality(
        "python asyncio timeout",
        _hits("Welcome to Python.org", "Download Python"),
    )
    assert label == "poor"
    assert score < 0.3
    assert any(item.startswith("quality_poor") for item in warnings)
