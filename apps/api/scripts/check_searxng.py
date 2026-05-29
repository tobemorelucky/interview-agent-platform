"""Check SearXNG JSON search connectivity for Phase 4 Step 5."""

import asyncio

import httpx

from interview_api.core.config import settings


async def main() -> None:
    base_url = settings.searxng_base_url.rstrip("/")
    params = {
        "q": "腾讯 面经",
        "format": "json",
        "language": settings.experience_search_language,
        "safesearch": settings.experience_search_safesearch,
    }
    print(f"SearXNG base URL: {base_url}")
    try:
        async with httpx.AsyncClient(timeout=settings.searxng_timeout_seconds) as client:
            response = await client.get(f"{base_url}/search", params=params)
    except httpx.TimeoutException:
        print(f"request timed out after {settings.searxng_timeout_seconds}s")
        return
    except httpx.HTTPError as exc:
        print(f"request failed: {exc}")
        return

    print(f"status code: {response.status_code}")
    if response.status_code == 403:
        print("请确认 SearXNG 已启用 json output format。")
        return
    if response.status_code >= 400:
        print(response.text[:500])
        return

    payload = response.json()
    results = payload.get("results") or []
    for index, item in enumerate(results[:3], start=1):
        print(f"{index}. {item.get('title') or '-'}")
        print(f"   {item.get('url') or '-'}")


if __name__ == "__main__":
    asyncio.run(main())
