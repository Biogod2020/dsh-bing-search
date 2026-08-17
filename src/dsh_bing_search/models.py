from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "blocked", "error"]
SafeSearch = Literal["Strict", "Moderate", "Off"]
Provider = Literal["bing", "duckduckgo"]
QualityLabel = Literal["good", "weak", "poor"]


class SearchResult(BaseModel):
    source_id: str = Field(description="Stable ID derived from the canonical result URL.")
    rank: int = Field(ge=1)
    title: str
    url: str
    display_url: str | None = None
    snippet: str | None = None


class SearchResponse(BaseModel):
    status: Status
    provider: Provider = "bing"
    query: str
    requested_count: int = 0
    returned_count: int = 0
    offset: int = 0
    market: str = "en-US"
    safe_search: SafeSearch = "Moderate"
    results: list[SearchResult] = Field(default_factory=list)
    quality_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="0-1 overlap of the query with titles/snippets. Below 0.3 is not trustworthy.",
    )
    quality_label: QualityLabel = Field(
        default="poor",
        description="good / weak / poor. If poor, do not treat results as answers.",
    )
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: int | None = None
    error: str | None = None


class OpenResponse(BaseModel):
    status: Status
    source_id: str | None = None
    requested_url: str
    final_url: str | None = None
    title: str | None = None
    content_type: str | None = None
    text: str | None = None
    truncated: bool = False
    fetched_bytes: int = 0
    elapsed_ms: int | None = None
    error: str | None = None


class FindMatch(BaseModel):
    index: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str


class FindResponse(BaseModel):
    status: Status
    source_id: str | None = None
    url: str
    pattern: str
    total_matches: int = 0
    matches: list[FindMatch] = Field(default_factory=list)
    error: str | None = None
