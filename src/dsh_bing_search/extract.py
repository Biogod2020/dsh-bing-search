from __future__ import annotations

import re

from bs4 import BeautifulSoup
from trafilatura import extract as trafilatura_extract

_HORIZONTAL_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def extract_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for selector, attribute in (
        ('meta[property="og:title"]', "content"),
        ('meta[name="twitter:title"]', "content"),
    ):
        node = soup.select_one(selector)
        value = node.get(attribute) if node else None
        if isinstance(value, str) and value.strip():
            return value.strip()

    if soup.title:
        title = soup.title.get_text(" ", strip=True)
        if title:
            return title
    heading = soup.find("h1")
    return heading.get_text(" ", strip=True) if heading else None


def html_to_readable_text(html: str) -> str:
    try:
        text = trafilatura_extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
            favor_precision=True,
            deduplicate=True,
        )
    except Exception:
        text = None

    if not text:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg", "template"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)

    text = text.replace("\x00", "")
    text = _HORIZONTAL_SPACE_RE.sub(" ", text)
    return _BLANK_LINES_RE.sub("\n\n", text).strip()
