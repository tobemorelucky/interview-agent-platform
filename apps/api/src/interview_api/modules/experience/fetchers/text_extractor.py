"""HTML text extraction helpers."""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
import trafilatura


MIN_TEXT_CHARS = 200


@dataclass(frozen=True)
class ExtractedText:
    title: str | None
    raw_text: str | None
    error_message: str | None = None


def extract_text_from_html(html: str, *, url: str | None = None) -> ExtractedText:
    title = _extract_title(html)

    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_precision=False,
    )
    text = _clean_text(text or "")

    if not text or len(text) < MIN_TEXT_CHARS:
        fallback_text = _extract_with_bs4(html)
        if len(fallback_text) > len(text):
            text = fallback_text

    if not text:
        return ExtractedText(title=title, raw_text=None, error_message="empty_text")
    if len(text) < MIN_TEXT_CHARS:
        return ExtractedText(title=title, raw_text=None, error_message="text_too_short")
    return ExtractedText(title=title, raw_text=text)


def _extract_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        return _clean_text(soup.title.string)
    return None


def _extract_with_bs4(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in [
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "header",
        "form",
    ]:
        for node in soup.find_all(tag):
            node.decompose()
    text = soup.get_text("\n")
    return _clean_text(text)


def _clean_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()
