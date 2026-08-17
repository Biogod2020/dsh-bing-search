from __future__ import annotations

import re
from urllib.parse import urlsplit

from .config import settings
from .models import DomainHint, ImageCandidate, ImageResult
from .url_utils import source_id_for

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[A-Za-z0-9]{3,}")
_STOP = {"the", "and", "for", "with", "from", "this", "that", "you", "your"}
_GOOD_SUFFIXES = (".gov.cn", ".edu.cn", ".wikimedia.org", ".wikipedia.org")

_SCORE_OVERLAP_MAX = 55   # query-token overlap drives the score
_SCORE_FULL_TITLE = 20    # exact (whitespace-collapsed) query inside the title
_SCORE_PARTIAL_TITLE = 10 # two-plus matched tokens at >=50% coverage
_SCORE_NO_TITLE = -15     # no title text means the text layer cannot verify it
_SCORE_DOMAIN_GOOD = 15
_SCORE_DOMAIN_BAD = -25
_LOW_SCORE = 40           # below this the top image is treated as unverified


def image_query_tokens(query: str) -> list[str]:
    """Latin words (3+ chars, stopwords removed) plus CJK bigrams.

    Bigrams (rather than whole CJK runs) let titles like "广州二中校服" still
    match the query "广州市第二中学校服". Query-relative: no fixed keywords.
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for part in query.replace('"', " ").replace("'", " ").split():
        for run in _CJK_RE.findall(part):
            if len(run) >= 2:
                for i in range(len(run) - 1):
                    bigram = run[i : i + 2]
                    if bigram not in seen:
                        seen.add(bigram)
                        tokens.append(bigram)
        for word in _LATIN_RE.findall(part):
            key = word.lower()
            if key in _STOP or key in seen:
                continue
            seen.add(key)
            tokens.append(key)
    return tokens


def _domain_matches(host: str, entry: str) -> bool:
    """Match a config entry against the host, including any subdomain."""
    return host == entry or host.endswith("." + entry)


def host_reputation(host: str) -> tuple[DomainHint, list[str]]:
    """Explicit good/bad domain lists (env-extensible), then authority suffixes."""
    host = (host or "").lower().rstrip(".")
    if not host:
        return "neutral", []
    if any(_domain_matches(host, entry) for entry in settings.image_bad_domains):
        return "bad", [f"domain_reputation: bad ({host})"]
    if any(_domain_matches(host, entry) for entry in settings.image_good_domains):
        return "good", [f"domain_reputation: good ({host})"]
    if any(host.endswith(suffix) for suffix in _GOOD_SUFFIXES):
        return "good", [f"domain_reputation: good ({host}, authority suffix)"]
    return "neutral", []


def rank_candidates(query: str, candidates: list[ImageCandidate]) -> list[ImageResult]:
    """Pure-text scoring shared by every image platform.

    Score (0-100): token overlap with title/purl/murl (0-55) + title evidence
    (+20 full query in title / +10 partial) + domain reputation (+15 good /
    -25 bad), minus 15 when the title is missing. Every point is explained in
    ``signals`` so a non-vision model can decide without seeing the image.
    """
    tokens = image_query_tokens(query)
    collapsed = "".join(query.split()).lower()
    results: list[ImageResult] = []
    for index, candidate in enumerate(candidates, start=1):
        signals: list[str] = []
        score = 0
        title = (candidate.title or "").strip()
        hay = " ".join(filter(None, (title, candidate.purl or "", candidate.murl))).lower()

        matched: list[str] = []
        ratio = 0.0
        if tokens:
            matched = [token for token in tokens if token in hay]
            ratio = len(matched) / len(tokens)
            score += round(ratio * _SCORE_OVERLAP_MAX)
            if matched:
                signals.append(f"query_tokens_matched: {','.join(matched[:8])}")
            else:
                signals.append("no_query_tokens_matched")
        else:
            signals.append("no_query_tokens")

        if title:
            if collapsed in "".join(title.split()).lower():
                score += _SCORE_FULL_TITLE
                signals.append("full_query_in_title")
            elif len(matched) >= 2 and ratio >= 0.5:
                score += _SCORE_PARTIAL_TITLE
                signals.append("partial_title_overlap")
        else:
            score += _SCORE_NO_TITLE
            signals.append("no_title_text")

        host = urlsplit(candidate.purl or candidate.murl).hostname or ""
        hint, domain_signals = host_reputation(host)
        if hint == "good":
            score += _SCORE_DOMAIN_GOOD
        elif hint == "bad":
            score += _SCORE_DOMAIN_BAD
        signals.extend(domain_signals)

        results.append(
            ImageResult(
                source_id=source_id_for(candidate.murl),
                rank=index,
                title=candidate.title,
                murl=candidate.murl,
                turl=candidate.turl,
                purl=candidate.purl,
                md5=candidate.md5,
                width=candidate.width,
                height=candidate.height,
                score=max(0, min(100, score)),
                domain_hint=hint,
                signals=signals,
            )
        )

    results.sort(key=lambda item: (-item.score, item.rank))
    for final_rank, item in enumerate(results, start=1):
        item.rank = final_rank
    return results


def low_score_threshold() -> int:
    return _LOW_SCORE
