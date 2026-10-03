"""One official JOB_JD smoke using existing live tools and configured models.

No Replay, model overrides, prompt changes or relaxed validators. Requires an
isolated database at the current Alembic head. A failed stage stops this smoke.
"""

import argparse
import asyncio
import json
import re
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.core.url_safety import URLSafetyPolicy
from interview_api.modules.experience.fetchers.httpx_fetcher import HttpxContentFetcher
from interview_api.modules.experience.search.searxng import SearxngSearchProvider
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.public_career.models import (
    PublicDataRefreshTask, SourceSnapshot, JobDescriptionVersion, JobRequirement, ClaimEvidence,
)
from interview_api.modules.public_career.refresh_service import PublicDataRefreshService, RefreshTaskStatus
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.tool_models import ToolInvocation
from interview_api.modules.public_career.tools.contracts import ToolContext
from interview_api.modules.public_career.tools.factory import (
    build_live_tool_gateway, build_tool_gateway, SEARCH_TOOL_NAME, FETCH_TOOL_NAME,
)


async def run(database_url: str, query: str, domain: str, path_pattern: str,
              proxy: str | None) -> dict:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    client = None
    task_id = None
    stage = "setup"
    report = {"query": query, "official_domain": domain, "replay": False}
    try:
        if proxy:
            # Same validated smoke-only network setup as M2A: public DoH with
            # the original private/reserved-IP and redirect checks intact.
            client = httpx.AsyncClient(proxy=proxy, timeout=20, follow_redirects=False)

            async def public_dns(host: str, _port: int) -> list[str]:
                response = await client.get("https://cloudflare-dns.com/dns-query",
                    params={"name": host, "type": "A"}, headers={"Accept": "application/dns-json"})
                response.raise_for_status()
                return [item["data"] for item in response.json().get("Answer", []) if item["type"] == 1]

            gateway = build_tool_gateway(factory, search_provider=SearxngSearchProvider(),
                fetch_provider=HttpxContentFetcher(client=client, safety_policy=URLSafetyPolicy(public_dns)))
        else:
            gateway = build_live_tool_gateway(factory)

        async with factory() as db:
            task = PublicDataRefreshTask(target_type="JOB_JD", target_ref=query,
                                         reason="M2B real configured model acceptance smoke")
            db.add(task)
            await db.commit()
            task_id = task.id
            report["refresh_task_id"] = task_id
            control = PublicDataRefreshService(db, gateway)
            context = ToolContext(refresh_task_id=task_id)
            await control.transition(task, RefreshTaskStatus.SEARCHING)
            await db.commit()
            stage = "live_search"
            search = await gateway.invoke(SEARCH_TOOL_NAME,
                {"query": query, "time_window_hours": 720, "max_results": 10}, context)
            report["search_results"] = [
                {"title": result["title"], "url": result["url"], "engine": result.get("engine")}
                for result in search.data["results"]]
            stage = "official_job_selection"
            selected = next((result for result in search.data["results"]
                if (urlsplit(result["url"]).hostname == domain or
                    (urlsplit(result["url"]).hostname or "").endswith("." + domain))
                and re.search(path_pattern, urlsplit(result["url"]).path)), None)
            if selected is None:
                raise ValueError("no_official_job_search_result: real search returned no matching official job detail URL")
            report["selected_url"] = selected["url"]
            await control.transition(task, RefreshTaskStatus.FETCHING)
            await db.commit()
            stage = "live_fetch"
            fetched = await gateway.invoke(FETCH_TOOL_NAME, {"url": selected["url"]}, context)
            refs = fetched.data["source_snapshot_refs"]
            snapshot = await db.get(SourceSnapshot, refs[0]["source_snapshot_id"])
            assert snapshot is not None and snapshot.content_text
            report.update(source_id=snapshot.source_id, source_snapshot_id=snapshot.id,
                          body_chars=len(snapshot.content_text), content_hash=snapshot.content_hash)
            await control.transition(task, RefreshTaskStatus.READY_FOR_EXTRACTION)
            await db.commit()

        stage = "public_data_research_agent"
        # Router is built from current Settings inside the production Agent.
        report["agent_result"] = await PublicDataResearchAgent(factory).run(task_id)
        if report["agent_result"]["status"] != "COMPLETED":
            raise RuntimeError(report["agent_result"].get("error", "public_research_failed"))
        stage = "persisted_evidence_verification"
        async with factory() as db:
            result = report["agent_result"]
            jd = await db.get(JobDescriptionVersion, result["jd_version_id"])
            requirements = list((await db.scalars(select(JobRequirement).where(
                JobRequirement.job_description_version_id == jd.id))).all())
            assert requirements and jd.source_snapshot_id == snapshot.id
            evidence_count = 0
            for requirement in requirements:
                assert requirement.claim_id is not None
                evidence = list((await db.scalars(select(ClaimEvidence).where(
                    ClaimEvidence.claim_id == requirement.claim_id))).all())
                assert evidence
                for item in evidence:
                    body = await db.get(SourceSnapshot, item.source_snapshot_id)
                    loc = item.source_location_json
                    assert body.id == snapshot.id
                    assert body.content_text[loc["start_char"]:loc["end_char"]] == item.excerpt
                    evidence_count += 1
            report.update(requirement_count=len(requirements), requirement_evidence_count=evidence_count,
                          jd_version_number=jd.version_number, evidence_verified=True)
        report["success"] = True
    except Exception as exc:
        report.update(success=False, failed_stage=stage,
                      error_code=getattr(exc, "code", type(exc).__name__), error=str(exc)[:2000])
        if task_id is not None:
            async with factory() as db:
                task = await db.get(PublicDataRefreshTask, task_id)
                if task.status not in {"FAILED", "COMPLETED"}:
                    await PublicDataRefreshService(db, None).transition(task, RefreshTaskStatus.FAILED,
                        error_message=f"{stage}: {report['error']}")
                    await db.commit()
    finally:
        if task_id is not None:
            async with factory() as db:
                task = await db.get(PublicDataRefreshTask, task_id)
                report["task_status"] = task.status
                invocations = list((await db.scalars(select(ToolInvocation).where(
                    ToolInvocation.refresh_task_id == task_id).order_by(ToolInvocation.id))).all())
                report["tool_invocations"] = [{"id": item.id, "tool": item.tool_name,
                    "mode": item.provider_mode, "status": item.status, "error": item.error_code}
                    for item in invocations]
                assert all(item.provider_mode == "LIVE" for item in invocations)
                runs = list((await db.scalars(select(ExperienceAgentRun).where(
                    ExperienceAgentRun.refresh_task_id == task_id))).all())
                steps = list((await db.scalars(select(ExperienceAgentStepRun).where(
                    ExperienceAgentStepRun.agent_run_id.in_([item.id for item in runs]))
                    .order_by(ExperienceAgentStepRun.id))).all())
                report["agent_steps"] = [{"id": item.id, "prompt": item.prompt_name,
                    "version": item.prompt_version, "provider": item.provider_name, "model": item.model_name,
                    "status": item.status, "structured_retry": item.structured_retry,
                    "usage": item.token_usage_json, "error": item.error_message} for item in steps]
                assert all(item.provider_name != "replay" for item in steps)
        if client is not None:
            await client.aclose()
        await engine.dispose()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--query", default="site:canonical.com/careers/ Python Engineer")
    parser.add_argument("--official-domain", default="canonical.com")
    parser.add_argument("--job-path-pattern", default=r"^/careers/\d+")
    parser.add_argument("--proxy", help="Optional existing smoke proxy; never changes model configuration")
    args = parser.parse_args()
    output = asyncio.run(run(args.database_url, args.query, args.official_domain,
                             args.job_path_pattern, args.proxy))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(0 if output["success"] else 1)
