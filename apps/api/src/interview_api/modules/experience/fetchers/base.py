"""Fetcher abstractions for URL content fetching."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    title: str | None
    raw_text: str | None
    error_message: str | None = None
    fetch_method: str = "httpx"

    @property
    def ok(self) -> bool:
        return bool(self.raw_text) and not self.error_message


class ContentFetcher(ABC):
    @abstractmethod
    async def fetch(self, url: str) -> FetchResult:
        """Fetch and extract a URL into raw text."""
