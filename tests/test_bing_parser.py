from pathlib import Path

from dsh_bing_search.providers.bing_parser import (
    detect_bing_block,
    detect_no_results,
    parse_bing_results,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bing_sample.html"


def test_parse_basic_bing_results() -> None:
    results = parse_bing_results(FIXTURE.read_text(encoding="utf-8"), count=10)
    assert [result.title for result in results] == ["Example A", "Example B"]
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "First result snippet."
    assert results[0].rank == 1


def test_parser_deduplicates_canonical_urls() -> None:
    html = """
    <ol id='b_results'>
      <li class='b_algo'><h2><a href='https://example.com/a?utm_source=x'>A</a></h2></li>
      <li class='b_algo'><h2><a href='https://example.com/a'>A duplicate</a></h2></li>
    </ol>
    """
    results = parse_bing_results(html, count=10)
    assert len(results) == 1


def test_detect_block_and_no_results_pages() -> None:
    assert detect_bing_block("<html>Verify you are human</html>")
    assert detect_no_results("<html>There are no results for this query</html>")


def test_parser_prefers_direct_data_url() -> None:
    html = """
    <ol id='b_results'>
      <li class='b_algo'>
        <h2><a href='https://www.bing.com/ck/a?u=not-decodable'
               data-url='https://example.net/direct?utm_source=bing'>Direct target</a></h2>
      </li>
    </ol>
    """
    results = parse_bing_results(html, count=10)
    assert len(results) == 1
    assert results[0].url == "https://example.net/direct"


def test_parser_drops_unresolved_bing_tracking_url() -> None:
    html = """
    <ol id='b_results'>
      <li class='b_algo'>
        <h2><a href='https://www.bing.com/ck/a?u=not-decodable'>Tracking only</a></h2>
      </li>
    </ol>
    """
    assert parse_bing_results(html, count=10) == []
