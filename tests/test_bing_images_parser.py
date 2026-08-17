from __future__ import annotations

import json

from dsh_bing_search.providers.bing_images import (
    _M_BLOB_RE,
    BingImagesProvider,
    build_headers,
    build_params,
)


def _blob(murl: str, turl: str = "", purl: str = "", title: str = "", md5: str = "") -> str:
    data = {"murl": murl, "turl": turl, "purl": purl, "t": title, "md5": md5}
    return json.dumps(data).replace('"', "&quot;")


HTML = (
    '<div><a class="iusc" m="{blob1}"></a>'
    '<a class="iusc" m="{blob2}"></a>'
    '<a class="iusc" m="{dup}"></a></div>'
)


def test_blob_regex_matches_escaped_json() -> None:
    page = HTML.format(
        blob1=_blob("https://cdn.example.com/img/a.jpg", title="校服 广州市二中"),
        blob2=_blob("http://other.example.net/b.png"),
        dup=_blob("https://cdn.example.com/img/a.jpg"),
    )
    assert len(_M_BLOB_RE.findall(page)) == 3


def test_parse_deduplicates_and_maps_fields() -> None:
    page = HTML.format(
        blob1=_blob(
            "https://cdn.example.com/img/a.jpg",
            turl="https://ts.bing.com/th?id=1",
            purl="http://www.wenmiyuan.com/p.html",
            title="广州市二中校服",
            md5="abc123",
        ),
        blob2=_blob("http://other.example.net/b.png"),
        dup=_blob("https://cdn.example.com/img/a.jpg"),
    )
    provider = BingImagesProvider()
    candidates = provider._parse_page(page, count=10)
    assert len(candidates) == 2  # duplicated murl merged
    first = candidates[0]
    assert first.murl == "https://cdn.example.com/img/a.jpg"
    assert first.title == "广州市二中校服"
    assert first.turl == "https://ts.bing.com/th?id=1"
    assert first.purl == "http://www.wenmiyuan.com/p.html"
    assert first.md5 == "abc123"


def test_bad_blobs_are_skipped() -> None:
    page = (
        '<a class="iusc" m="' + json.dumps({"murl": None}).replace('"', "&quot;") + '"></a>'
        '<a class="iusc" m="{}"></a>'
        '<a class="iusc" m="not a blob at all"></a>'
    )
    provider = BingImagesProvider()
    assert provider._parse_page(page, count=10) == []


def test_cc_derived_from_market() -> None:
    params = build_params("cards", market="en-US", cc_override="")
    assert params["cc"] == "US"


def test_cc_override_wins() -> None:
    params = build_params("cards", market="en-US", cc_override="CN")
    assert params["cc"] == "CN"


def test_no_cc_without_region() -> None:
    params = build_params("cards", market="en", cc_override="")
    assert "cc" not in params


def test_headers_use_market_language() -> None:
    headers = build_headers("zh-CN")
    assert "zh-CN" in headers["Accept-Language"]
    assert "zh;q=0.9" in headers["Accept-Language"]
