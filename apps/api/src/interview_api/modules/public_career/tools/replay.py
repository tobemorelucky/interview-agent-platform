"""Offline-only providers backed exclusively by local fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from interview_api.modules.experience.search.url_utils import normalize_url
from interview_api.modules.public_career.tools.contracts import ProviderMode
from interview_api.modules.public_career.tools.providers import (
    FetchProvider,
    FetchResult,
    SearchProvider,
    SearchResult,
)


_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> Any:
    with (_FIXTURE_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


class ReplaySearchProvider(SearchProvider):
    """Returns fixture data and contains no network client or network code path."""

    def __init__(self, fixtures: list[dict[str, Any]] | None = None) -> None:
        self._fixtures = fixtures if fixtures is not None else _load_fixture("search.json")

    @property
    def provider_name(self) -> str:
        return "replay_search"

    @property
    def provider_mode(self) -> ProviderMode:
        return ProviderMode.REPLAY

    async def search(
        self,
        query: str,
        *,
        time_window_hours: int,
        max_results: int,
    ) -> list[SearchResult]:
        del time_window_hours
        ordered = sorted(
            self._fixtures,
            key=lambda item: (
                normalize_url(str(item["url"])),
                str(item.get("title") or "").casefold(),
            ),
        )
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item["url"]),
                snippet=item.get("snippet"),
                engine="replay",
                published_at=item.get("published_at"),
                metadata={"fixture": True, "query": query},
            )
            for item in ordered[:max_results]
        ]


class ReplayFetchProvider(FetchProvider):
    """Fetches exact fixture URLs only; an unmatched URL fails without networking."""

    def __init__(self, fixtures: dict[str, dict[str, Any]] | None = None) -> None:
        raw = fixtures if fixtures is not None else _load_fixture("fetch.json")
        self._fixtures = {normalize_url(url): value for url, value in raw.items()}

    @property
    def provider_name(self) -> str:
        return "replay_fetch"

    @property
    def provider_mode(self) -> ProviderMode:
        return ProviderMode.REPLAY

    async def fetch(self, url: str) -> FetchResult:
        canonical_url = normalize_url(url)
        fixture = self._fixtures.get(canonical_url)
        if fixture is None:
            return FetchResult(
                url=url,
                final_url=canonical_url or None,
                status_code=None,
                content_type=None,
                title=None,
                raw_text=None,
                error_code="replay_fixture_not_found",
                error_message="No replay fixture exists for this URL",
                fetch_method="replay",
            )
        return FetchResult(
            url=url,
            final_url=canonical_url,
            status_code=int(fixture.get("status_code", 200)),
            content_type=str(fixture.get("content_type", "text/html")),
            title=fixture.get("title"),
            raw_text=str(fixture["content"]),
            fetch_method="replay",
            metadata={"fixture": True},
        )

