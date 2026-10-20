"""Search and fetch provider contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from interview_api.modules.public_career.tools.contracts import ProviderMode


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str | None = None
    engine: str | None = None
    published_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Actual provider-returned webpage body, never synthesized from snippet/summary.
    content: str | None = None


class SearchProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def provider_mode(self) -> ProviderMode: ...

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        time_window_hours: int,
        max_results: int,
    ) -> list[SearchResult]: ...


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    title: str | None
    raw_text: str | None
    error_code: str | None = None
    error_message: str | None = None
    fetch_method: str = "httpx"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.raw_text) and not self.error_code and not self.error_message


class FetchProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def provider_mode(self) -> ProviderMode: ...

    @abstractmethod
    async def fetch(self, url: str) -> FetchResult: ...

