"""SearXNG search provider."""

from typing import Any

import httpx

from interview_api.core.config import settings
from interview_api.modules.experience.search.base import SearchResult
from interview_api.modules.experience.search.url_utils import normalize_url


class SearxngSearchProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.searxng_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.searxng_timeout_seconds

    async def search(
        self,
        query: str,
        *,
        time_window_hours: int,
        max_results: int,
    ) -> list[SearchResult]:
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "language": settings.experience_search_language,
            "safesearch": settings.experience_search_safesearch,
            "time_range": _time_range(time_window_hours),
        }
        if settings.searxng_engines.strip():
            params["engines"] = settings.searxng_engines.strip()

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get("/search", params=params)
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"SearXNG search timed out after {self.timeout_seconds}s") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"SearXNG request failed: {exc}") from exc

        if response.status_code == 403:
            raise RuntimeError(
                "SearXNG returned 403. Ensure docker/searxng/settings.yml "
                "search.formats includes json."
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"SearXNG returned HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("SearXNG returned non-JSON response") from exc

        items = payload.get("results")
        if not isinstance(items, list):
            raise RuntimeError("SearXNG response missing results list")

        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            normalized = normalize_url(url)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            results.append(
                SearchResult(
                    title=str(item.get("title") or "").strip(),
                    url=url,
                    snippet=_first_text(item, "content", "snippet"),
                    engine=_first_text(item, "engine", "engines"),
                    published_at=_first_text(item, "publishedDate", "published_date"),
                )
            )
            if len(results) >= max_results:
                break
        return results


def _time_range(time_window_hours: int) -> str:
    if time_window_hours <= 24:
        return "day"
    if time_window_hours <= 720:
        return "month"
    return "year"


def _first_text(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            value = ",".join(str(v) for v in value if v)
        if value:
            return str(value).strip()
    return None
