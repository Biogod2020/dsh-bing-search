from __future__ import annotations

import re
from dataclasses import dataclass

from .models import QualityLabel, SearchResult

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[A-Za-z0-9]{3,}")
_DICT_TITLE_RE = re.compile(
    r"(百科|汉典|词典|字典|拼音|definition & meaning|english meaning)",
    re.IGNORECASE,
)
_STOP = {"the", "and", "for", "with", "from", "this", "that"}


@dataclass(frozen=True, slots=True)
class Quality:
    score: float
    label: QualityLabel
    reasons: tuple[str, ...]


def query_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for part in query.replace('"', " ").replace("'", " ").split():
        part = part.strip()
        if not part:
            continue
        cjk = _CJK_RE.findall(part)
        latin = [w.lower() for w in _LATIN_RE.findall(part) if w.lower() not in _STOP]
        pieces = cjk + latin
        if not pieces:
            continue
        for piece in pieces:
            key = piece.lower()
            if key not in seen:
                seen.add(key)
                tokens.append(piece)
    return tokens


def _blob(results: list[SearchResult]) -> str:
    parts: list[str] = []
    for item in results:
        parts.append(item.title)
        if item.snippet:
            parts.append(item.snippet)
    return re.sub(r"\s+", "", " ".join(parts)).lower()


def _label(score: float) -> QualityLabel:
    if score >= 0.55:
        return "good"
    if score >= 0.30:
        return "weak"
    return "poor"


def score_results(query: str, results: list[SearchResult]) -> Quality:
    """Generic overlap score. Does not depend on a specific engine."""
    if not results:
        return Quality(0.0, "poor", ("empty_results",))

    tokens = query_tokens(query)
    blob = _blob(results)
    reasons: list[str] = []

    dict_hits = sum(1 for item in results if _DICT_TITLE_RE.search(item.title))
    if results and dict_hits / len(results) >= 0.6:
        reasons.append("dictionary_titles")

    if not tokens:
        score = 0.4 if not reasons else 0.15
        return Quality(score, _label(score), tuple(reasons) or ("no_tokens",))

    hits = [token for token in tokens if token.lower() in blob]
    overlap = len(hits) / len(tokens)

    first = tokens[0]
    later = tokens[1:]
    later_hit = any(token.lower() in blob for token in later)
    first_head = first[:1] if _CJK_RE.search(first) else first.lower()
    first_only = bool(later) and not later_hit and first_head.lower() in blob
    if first_only:
        reasons.append("first_token_collapse")
        overlap = min(overlap, 0.2)

    if overlap < 0.15 and not first_only:
        reasons.append("low_overlap")

    if "dictionary_titles" in reasons:
        overlap = min(overlap, 0.2)

    score = round(max(0.0, min(1.0, overlap)), 3)
    if not reasons and score < 0.3:
        reasons.append("low_overlap")
    return Quality(score, _label(score), tuple(reasons))


def apply_quality(query: str, results: list[SearchResult]) -> tuple[float, QualityLabel, list[str]]:
    quality = score_results(query, results)
    warnings: list[str] = []
    if quality.label == "poor":
        detail = ",".join(quality.reasons) or "unrelated"
        warnings.append(
            f"quality_poor ({detail}): do not treat these titles as answers; retry a shorter query or another source."
        )
    elif quality.label == "weak":
        detail = ",".join(quality.reasons) or "partial"
        warnings.append(
            f"quality_weak ({detail}): only partial overlap with the query; verify before citing."
        )
    return quality.score, quality.label, warnings
