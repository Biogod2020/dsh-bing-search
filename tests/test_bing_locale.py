from dsh_bing_search.providers.bing_locale import (
    bing_images_url,
    bing_search_url,
    bing_setlang,
    prefers_chinese,
)


def test_chinese_market_and_query_use_cn() -> None:
    assert prefers_chinese("zh-CN", "hello")
    assert prefers_chinese("en-US", "上海今天天气")
    assert not prefers_chinese("en-US", "DeepSeek Harness GitHub")
    assert bing_search_url("zh-CN", "foo", "https://www.bing.com/search") == "https://cn.bing.com/search"
    assert bing_search_url("en-US", "foo", "https://www.bing.com/search") == "https://www.bing.com/search"
    assert bing_search_url("en-US", "高铁票", "https://www.bing.com/search") == "https://cn.bing.com/search"


def test_images_follow_the_same_host_split() -> None:
    assert (
        bing_images_url("zh-CN", "校服", "https://www.bing.com/images/search")
        == "https://cn.bing.com/images/search"
    )
    assert (
        bing_images_url("en-US", "transformer", "https://www.bing.com/images/search")
        == "https://www.bing.com/images/search"
    )
    assert (
        bing_images_url("zh-CN", "校服", "https://example.test/images")
        == "https://example.test/images"
    )


def test_explicit_override_wins() -> None:
    assert (
        bing_search_url("zh-CN", "上海", "https://example.test/search") == "https://example.test/search"
    )


def test_setlang() -> None:
    assert bing_setlang("zh-CN") == "zh-Hans"
    assert bing_setlang("zh-TW") == "zh-Hant"
    assert bing_setlang("en-US") == "en"
