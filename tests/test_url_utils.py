import base64

import pytest

from dsh_bing_search.url_utils import (
    canonicalize_url,
    normalize_market,
    source_id_for,
    unwrap_bing_url,
    validate_public_http_url,
)


def test_canonicalize_strips_tracking_and_fragment() -> None:
    assert (
        canonicalize_url("https://Example.com/a?utm_source=x&x=1#frag")
        == "https://example.com/a?x=1"
    )


def test_source_id_is_stable_under_tracking_changes() -> None:
    assert source_id_for("https://example.com/a?utm_source=x") == source_id_for(
        "https://example.com/a"
    )


def test_unwrap_bing_base64_redirect() -> None:
    target = "https://example.com/article?q=1"
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    wrapped = f"https://www.bing.com/ck/a?u=a1{encoded}"
    assert unwrap_bing_url(wrapped) == target


def test_market_normalization() -> None:
    assert normalize_market("ZH-cn") == "zh-CN"
    with pytest.raises(ValueError):
        normalize_market("zh")


@pytest.mark.asyncio
async def test_private_urls_are_rejected() -> None:
    with pytest.raises(ValueError):
        await validate_public_http_url("http://127.0.0.1/test")
    with pytest.raises(ValueError):
        await validate_public_http_url("http://localhost/test")
