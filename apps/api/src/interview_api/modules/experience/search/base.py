"""Search provider contracts for interview experience discovery."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str | None = None
    engine: str | None = None
    published_at: str | None = None


class SearchProvider(Protocol):
    async def search(
        self,
        query: str,
        *,
        time_window_hours: int,
        max_results: int,
    ) -> list[SearchResult]:
        """Search external engines and return URL-level candidates."""
        ...
