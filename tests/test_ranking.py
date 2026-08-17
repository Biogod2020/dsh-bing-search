from __future__ import annotations

from dsh_bing_search.models import ImageCandidate
from dsh_bing_search.ranking import (
    host_reputation,
    image_query_tokens,
    rank_candidates,
)

FULL_TITLE = "广州市二中校服,广州二中校服,广州市第二中学校服_文秘苑图库"


def test_cjk_bigrams_are_query_relative() -> None:
    tokens = image_query_tokens("广州市第二中学校服")
    assert "二中" in tokens
    assert "校服" in tokens
    assert "广州" in tokens
    assert len(tokens) >= 6  # 8-char run yields 7 bigrams


def test_latin_tokens_skip_stopwords() -> None:
    tokens = image_query_tokens("cardiac action potential diagram of the heart")
    assert "cardiac" in tokens
    assert "diagram" in tokens
    assert "the" not in tokens
    assert "of" not in tokens


def test_good_domain_beats_bad_domain_for_identical_title() -> None:
    good = ImageCandidate(title=FULL_TITLE, murl="https://files.eduuu.com/a.jpg", purl="http://www.wenmiyuan.com/p.html")
    bad = ImageCandidate(title=FULL_TITLE, murl="https://files.eduuu.com/b.jpg", purl="http://www.dashangu.com/p.html")
    results = rank_candidates("广州市第二中学校服", [bad, good])
    assert results[0].murl == good.murl
    assert results[0].domain_hint == "good"
    assert results[1].domain_hint == "bad"
    assert results[0].score - results[1].score == 40  # +15 good vs -25 bad


def test_unrelated_title_scores_low() -> None:
    unrelated = ImageCandidate(
        title="广州旅游攻略大全", murl="https://x.com/u.jpg", purl="https://example.com/p"
    )
    results = rank_candidates("广州市第二中学校服", [unrelated])
    assert results[0].score < 20


def test_missing_title_is_penalised() -> None:
    titled = ImageCandidate(title=FULL_TITLE, murl="https://a.com/1.jpg", purl="https://example.com/p")
    untitled = ImageCandidate(title=None, murl="https://a.com/2.jpg", purl="https://example.com/p")
    results = rank_candidates("广州市第二中学校服", [untitled, titled])
    assert results[0].murl == titled.murl
    assert "no_title_text" in results[1].signals


def test_authority_suffix_is_good() -> None:
    hint, signals = host_reputation("www.gz.gov.cn")
    assert hint == "good"
    assert signals


def test_score_stays_within_bounds() -> None:
    junk = ImageCandidate(title="x", murl="https://x.com/junk.jpg", purl="http://www.dashangu.com/p")
    results = rank_candidates("广州市第二中学校服", [junk])
    assert 0 <= results[0].score <= 100
