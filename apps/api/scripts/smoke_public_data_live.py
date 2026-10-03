"""LLM-free live public-data smoke; requires an isolated migrated database."""

import argparse
import asyncio
import json

import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.modules.public_career.models import PublicDataRefreshTask, SourceSnapshot
from interview_api.modules.public_career.refresh_service import PublicDataRefreshService
from interview_api.modules.public_career.tool_models import ToolInvocation
from interview_api.modules.public_career.tools.contracts import ToolContext
from interview_api.modules.public_career.tools.factory import build_live_tool_gateway, FETCH_TOOL_NAME
from interview_api.modules.public_career.tools.factory import build_tool_gateway
from interview_api.modules.experience.search.searxng import SearxngSearchProvider
from interview_api.modules.experience.fetchers.httpx_fetcher import HttpxContentFetcher
from interview_api.core.url_safety import URLSafetyPolicy


async def run(database_url: str, query: str, proxy: str | None = None) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    live_client = None
    try:
        live_client = httpx.AsyncClient(proxy=proxy, timeout=20, follow_redirects=False) if proxy else None
        async def public_dns(host: str, _port: int) -> list[str]:
            assert live_client is not None
            response = await live_client.get(
                "https://cloudflare-dns.com/dns-query",
                params={"name": host, "type": "A"},
                headers={"Accept": "application/dns-json"},
            )
            response.raise_for_status()
            return [item["data"] for item in response.json().get("Answer", []) if item["type"] == 1]
        gateway = build_tool_gateway(
            factory, search_provider=SearxngSearchProvider(),
            fetch_provider=HttpxContentFetcher(client=live_client, safety_policy=URLSafetyPolicy(public_dns)),
        ) if proxy else build_live_tool_gateway(factory)
        async with factory() as db:
            task = PublicDataRefreshTask(target_type="LIVE_SMOKE", target_ref=query)
            db.add(task)
            await db.commit()
            task = await PublicDataRefreshService(db, gateway).execute_to_ready(
                task, query=query, time_window_hours=720, max_results=5
            )
            calls = list((await db.scalars(select(ToolInvocation).where(
                ToolInvocation.refresh_task_id == task.id
            ).order_by(ToolInvocation.id))).all())
            output = {
                "task_id": task.id, "status": task.status,
                "calls": [{"id": c.id, "tool": c.tool_name,
                           "mode": c.provider_mode, "status": c.status,
                           "error": c.error_code,
                           "results": c.response_json.get("data", {}).get("results", []),
                           "snapshot_refs": c.response_json.get("source_snapshot_refs", [])}
                          for c in calls],
            }
            assert task.status == "READY_FOR_EXTRACTION", task.error_message
            assert all(c.provider_mode.lower() == "live" for c in calls)
            fetched = next(c for c in calls if c.tool_name == FETCH_TOOL_NAME and c.status == "SUCCEEDED")
            url = fetched.request_json["url"]
            assert "replay-career-source" not in url
            repeated = await gateway.invoke(FETCH_TOOL_NAME, {"url": url}, ToolContext(refresh_task_id=task.id))
            refs = fetched.response_json["source_snapshot_refs"]
            assert repeated.data["source_snapshot_refs"] == refs, "Live body changed between fetches"
            snapshot = await db.get(SourceSnapshot, refs[0]["source_snapshot_id"])
            assert snapshot and len(snapshot.content_text or "") >= 200
            output["repeat_deduped"] = True
            output["repeat_invocation_id"] = repeated.data["tool_invocation_id"]
            output["fetched_url"] = url
            output["body_chars"] = len(snapshot.content_text or "")
            output["body_preview"] = (snapshot.content_text or "")[:400]
            print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        if proxy and live_client is not None:
            await live_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--query", default="Python software engineer graduate job requirements")
    parser.add_argument("--proxy", help="Local proxy for Fake-IP environments; uses public DoH with IP safety checks")
    args = parser.parse_args()
    asyncio.run(run(args.database_url, args.query, args.proxy))
