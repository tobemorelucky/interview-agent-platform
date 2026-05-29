"""Check SearXNG JSON search connectivity for Phase 4 Step 5."""

import asyncio
import sys
from typing import Any

import httpx

from interview_api.core.config import settings


async def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "github"
    base_url = settings.searxng_base_url.rstrip("/")
    engines = settings.searxng_engines.strip()
    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "language": settings.experience_search_language,
        "safesearch": settings.experience_search_safesearch,
    }
    if engines:
        params["engines"] = engines

    print(f"SEARXNG_BASE_URL={base_url}")
    print(f"SEARXNG_ENGINES={engines or '(default)'}")
    print(f"SEARXNG_TIMEOUT_SECONDS={settings.searxng_timeout_seconds}")
    print(f"query={query}")

    try:
        async with httpx.AsyncClient(timeout=settings.searxng_timeout_seconds) as client:
            response = await client.get(f"{base_url}/search", params=params)
    except httpx.TimeoutException:
        print(f"request timed out after {settings.searxng_timeout_seconds}s")
        print("SearXNG is not reachable. Ensure docker compose up -d searxng has been run.")
        return
    except httpx.HTTPError as exc:
        print(f"request failed: {exc}")
        print("SearXNG is not reachable. Ensure docker compose up -d searxng has been run.")
        return

    print(f"HTTP status code={response.status_code}")
    if response.status_code == 403:
        print("Ensure docker/searxng/settings.yml search.formats includes json.")
        return
    if response.status_code >= 400:
        print(response.text[:500])
        return

    try:
        payload = response.json()
    except ValueError:
        print("SearXNG returned a non-JSON response.")
        return

    results = payload.get("results") or []
    unresponsive = payload.get("unresponsive_engines") or []
    print(f"results count={len(results)}")
    print(f"unresponsive_engines={unresponsive}")

    if not results:
        print("SearXNG reachable, but selected/default engines returned no results.")
        return

    for index, item in enumerate(results[:3], start=1):
        print(f"{index}. {item.get('title') or '-'}")
        print(f"   {item.get('url') or '-'}")


if __name__ == "__main__":
    asyncio.run(main())
