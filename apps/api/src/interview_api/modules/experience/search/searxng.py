"""SearXNG adapter for the shared public SearchProvider contract."""

from typing import Any

import httpx

from interview_api.core.config import settings
from interview_api.modules.experience.search.url_utils import normalize_url
from interview_api.modules.public_career.tools.contracts import ProviderMode
from interview_api.modules.public_career.tools.errors import ToolExecutionError
from interview_api.modules.public_career.tools.providers import SearchProvider, SearchResult


class SearxngSearchProvider(SearchProvider):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.searxng_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.searxng_timeout_seconds
        self._client = client

    @property
    def provider_name(self) -> str:
        return "searxng"

    @property
    def provider_mode(self) -> ProviderMode:
        return ProviderMode.LIVE

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
        }
        if settings.experience_search_enable_time_range:
            params["time_range"] = _time_range(time_window_hours)
        if settings.searxng_engines.strip():
            params["engines"] = settings.searxng_engines.strip()

        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            response = await client.get(f"{self.base_url}/search", params=params)
        except httpx.TimeoutException as exc:
            raise ToolExecutionError(
                "search_timeout",
                f"SearXNG search timed out after {self.timeout_seconds}s",
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError(
                "search_request_failed", "SearXNG request failed"
            ) from exc
        finally:
            if self._client is None:
                await client.aclose()

        if response.status_code == 403:
            raise ToolExecutionError(
                "search_forbidden",
                "SearXNG returned 403. Ensure docker/searxng/settings.yml "
                "search.formats includes json.",
            )
        if response.status_code >= 400:
            raise ToolExecutionError(
                "search_http_error",
                f"SearXNG returned HTTP {response.status_code}",
                details={"status_code": response.status_code},
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolExecutionError(
                "search_invalid_response", "SearXNG returned non-JSON response"
            ) from exc

        items = payload.get("results")
        if not isinstance(items, list):
            raise ToolExecutionError(
                "search_invalid_response", "SearXNG response missing results list"
            )
        # Some enabled engines (e.g. Wikipedia) return URL observations as
        # infoboxes. Treat their links as search candidates, never fetched bodies.
        items = list(items)
        for box in payload.get("infoboxes") or []:
            if not isinstance(box, dict):
                continue
            for link in box.get("urls") or []:
                if isinstance(link, dict) and link.get("url"):
                    items.append({
                        "url": link["url"],
                        "title": box.get("infobox") or link.get("title"),
                        "content": box.get("content"),
                        "engine": box.get("engine"),
                    })

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
                    url=normalized,
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
