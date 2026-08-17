from dsh_bing_search.providers.ddg import detect_ddg_block, parse_ddg_results
from dsh_bing_search.url_utils import unwrap_ddg_url


def test_unwrap_ddg_redirect() -> None:
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fnewsletter.maartengrootendorst.com%2Fp%2Fa-visual-guide-to-diffusiongemma"
    assert unwrap_ddg_url(href) == "https://newsletter.maartengrootendorst.com/p/a-visual-guide-to-diffusiongemma"


def test_parse_ddg_html() -> None:
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Example A</a>
      <a class="result__snippet">Snippet A</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.com/b">Example B</a>
    </div>
    """
    results = parse_ddg_results(html, count=8)
    assert [item.title for item in results] == ["Example A", "Example B"]
    assert results[0].url == "https://example.com/a"
    assert results[0].display_url == "example.com"
    assert results[0].snippet == "Snippet A"


def test_parse_ddg_drops_mobile_duplicate() -> None:
    html = """
    <a class="result__a" href="https://www.ebiotrade.com/newsf/2026-2/x">Desktop</a>
    <a class="result__a" href="https://m.ebiotrade.com/newsf/2026-2/x">Mobile</a>
    """
    results = parse_ddg_results(html, count=8)
    assert len(results) == 1
    assert results[0].url.startswith("https://www.ebiotrade.com")


def test_ddg_202_is_blocked() -> None:
    assert detect_ddg_block("<html></html>", 202)
    assert not detect_ddg_block("<div class='result'></div>", 200)
