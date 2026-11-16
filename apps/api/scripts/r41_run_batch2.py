"""Round 34: the two authorised real-model sub-runs, with separate budgets.

  --mode nio      NIO identity revision:      2 HTTP / 8,000 tokens (hard)
  --mode batch2   five new real JDs:         10 HTTP normal, 14 hard / 40,000 tokens

Budgets are measured as a *delta* against the governed ledger at process start, so
a rerun cannot silently consume the other sub-run's allowance. Any UNKNOWN,
quota error, ledger-ownership error or non-COMPLETED result stops that sub-run;
nothing is retried and no provider is switched.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

import interview_api.modules.models  # noqa: F401,E402
from interview_api.core.config import settings  # noqa: E402
from interview_api.infrastructure.models.governance_models import ModelAdmission  # noqa: E402
from interview_api.infrastructure.models.router import build_model_router  # noqa: E402
from interview_api.modules.experience.models import ExperienceAgentStepRun  # noqa: E402
from interview_api.modules.public_career.manual_import import (  # noqa: E402
    ManualDocument, ManualImportService, manual_manifest,
)
from interview_api.modules.public_career.models import PublicDataRefreshTask  # noqa: E402
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent  # noqa: E402

SANDBOX = "interview_agent_wb_jd_r31_20261116"
EXPECTED_PROVIDER = "modelscope"
EXPECTED_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash-1017"

NIO_TEXT = REPO / "data/r34_target_jd/text/NIO_AIA_025.txt"
NIO_URL = "https://nio.jobs.feishu.cn/campus/position/7673028675729656105/detail"
BATCH2 = REPO / "data/r40_batch2/source_manifest.json"
OUT = REPO / "data/r40_batch2/real_run"

BUDGETS = {"nio": {"max_http": 2, "max_tokens": 8000, "normal_http": 2},
           "batch2": {"max_http": 14, "max_tokens": 40000, "normal_http": 10}}


class BudgetStop(RuntimeError):
    pass


async def ledger(factory) -> dict:
    async with factory() as db:
        rows = (await db.execute(select(ModelAdmission.status, ModelAdmission.route,
                                        ModelAdmission.reserved_tokens, ModelAdmission.actual_tokens,
                                        ModelAdmission.model))).all()
        steps = (await db.execute(select(ExperienceAgentStepRun.step_name, ExperienceAgentStepRun.status,
                                          ExperienceAgentStepRun.token_usage_json,
                                          ExperienceAgentStepRun.structured_retry,
                                          ExperienceAgentStepRun.error_message))).all()
    return {"http_attempts": len(rows),
            "tokens": sum((row[3] if row[3] is not None else (row[2] or 0)) for row in rows),
            "statuses": sorted({row[0] for row in rows}),
            "routes": sorted({row[1] for row in rows}),
            "models": sorted({row[4] for row in rows}),
            "steps": [{"step": s[0], "status": s[1], "usage": s[2], "structured_retry": s[3],
                       "error": s[4]} for s in steps]}


def guard_environment(database: str) -> dict:
    url = os.getenv("DATABASE_URL", "")
    ledger_env = os.getenv("MODEL_LEDGER_DATABASE", "")
    if not url.endswith("/" + database):
        raise SystemExit(f"REFUSED: DATABASE_URL is not {database}")
    if ledger_env != database:
        raise SystemExit(f"REFUSED: MODEL_LEDGER_DATABASE={ledger_env!r} != {database}")
    if settings.public_research_official_domains != {}:
        raise SystemExit("REFUSED: official domains must stay unconfigured")
    router = build_model_router()
    resolved = {}
    for route in ("public_research_extract", "public_research_quality"):
        provider = router.primary(route)
        if provider.provider_name != EXPECTED_PROVIDER or provider.model_name != EXPECTED_MODEL:
            raise SystemExit(f"REFUSED: {route} -> {provider.provider_name}/{provider.model_name}")
        if not getattr(provider, "governed", False):
            raise SystemExit(f"REFUSED: {route} is not governed")
        resolved[route] = {"provider": provider.provider_name, "model": provider.model_name}
    if router.fallback_routes:
        raise SystemExit(f"REFUSED: fallback routes configured: {router.fallback_routes}")
    return {"database": database, "routes": resolved}


def delta(before: dict, after: dict) -> dict:
    return {"http": after["http_attempts"] - before["http_attempts"],
            "tokens": after["tokens"] - before["tokens"],
            "statuses": sorted(set(after["statuses"]) - set(before["statuses"])) or after["statuses"],
            "tokens_after": after["tokens"], "http_after": after["http_attempts"]}


async def run_nio(service, agent, factory, report: dict, budget: dict, *, prep_only: bool = False) -> None:
    async with factory() as db:
        failed = (await db.scalars(select(PublicDataRefreshTask).where(
            PublicDataRefreshTask.status == "FAILED").order_by(PublicDataRefreshTask.id))).all()
        candidates = []
        for task in failed:
            manifest = manual_manifest(task)
            if manifest and manifest.get("canonical_url") == NIO_URL:
                candidates.append((task, manifest))
    if not candidates:
        raise SystemExit("no FAILED NIO task found")
    task, manifest = candidates[0]
    report["predecessor"] = {"task_id": task.id, "status": task.status,
                             "frozen_job_title": manifest["job_title"],
                             "content_sha256": manifest["content_sha256"],
                             "error": (task.error_message or "")[:120]}

    literal = NIO_TEXT.read_text(encoding="utf-8").splitlines()[0].split(" - ")[0].strip()
    report["revision_requested"] = {"job_title": literal, "source_url": NIO_URL}
    document = ManualDocument.from_file(file=NIO_TEXT, source_url=NIO_URL, source_type="official",
                                        document_type="job_jd", company="蔚来", job_title=literal)

    before = await ledger(factory)
    # Zero-HTTP preparation, run twice: the second call proves idempotency before
    # a single token is spent.
    first = await service.prepare_identity_revision(
        document, predecessor_task_id=task.id, authorized_by="user:R34-approval",
        reason="R34：官方字面标题为「校招-车端 AI Agent 与应用生态系统架构工程师」，R31 冻结的是去前缀展示名；"
               "按 R33 已验收的显式修订流程重建冻结输入，不改原任务与正文。")
    second = await service.prepare_identity_revision(
        document, predecessor_task_id=task.id, authorized_by="user:R34-approval",
        reason="idempotency probe")
    report["revision"] = {"task_id": first["refresh_task_id"], "revision_of": first["revision_of"],
                          "status": first["status"], "reused": first["reused"],
                          "second_call_task_id": second["refresh_task_id"],
                          "second_call_reused": second["reused"],
                          "idempotent": second["refresh_task_id"] == first["refresh_task_id"]}
    if not report["revision"]["idempotent"]:
        raise SystemExit("revision is not idempotent, refusing to spend budget")

    after_prep = await ledger(factory)
    if after_prep["http_attempts"] != before["http_attempts"]:
        raise SystemExit("preparation issued a model call; that must never happen")

    if prep_only:
        report["prep_only"] = True
        report["ledger"] = delta(before, after_prep)
        return

    result = await agent.run(first["refresh_task_id"])
    after = await ledger(factory)
    report["run"] = {"status": result.get("status"), "company_id": result.get("company_id"),
                     "job_id": result.get("job_id"), "jd_version_id": result.get("jd_version_id"),
                     "publication_status": result.get("publication_status"),
                     "error": result.get("error")}
    report["ledger"] = delta(before, after)
    if report["ledger"]["http"] > budget["normal_http"] or report["ledger"]["tokens"] > budget["max_tokens"]:
        report["budget_breach"] = True
    if result.get("status") != "COMPLETED":
        report["stopped"] = {"reason": "NIO_NOT_COMPLETED", "error": result.get("error")}


async def run_batch2(service, agent, factory, report: dict, budget: dict) -> None:
    manifest = json.loads(BATCH2.read_text(encoding="utf-8"))
    base = await ledger(factory)
    report["ledger_baseline"] = {"http": base["http_attempts"], "tokens": base["tokens"]}
    spent_http = 0
    spent_tokens = 0
    for record in manifest["records"]:
        key = record["jd_key"]
        if spent_http >= budget["max_http"] or spent_tokens >= budget["max_tokens"]:
            report["stopped"] = {"reason": "BUDGET_EXHAUSTED_BEFORE_JD", "jd_key": key,
                                 "http": spent_http, "tokens": spent_tokens}
            break
        before = await ledger(factory)
        entry: dict = {"company": record["company"], "job_title": record["job_title"],
                       "text_chars": record["text_chars"], "text_sha256": record["text_sha256"],
                       "job_id": record["job_id"]}
        document = ManualDocument.from_file(file=REPO / record["text_path"],
                                            source_url=record["source_url"], source_type="official",
                                            document_type="job_jd", company=record["company"],
                                            job_title=record["job_title"])
        prepared = await service.prepare(document)
        entry["prepared"] = {k: prepared[k] for k in
                             ("refresh_task_id", "source_snapshot_id", "status", "reused")}
        try:
            result = await agent.run(prepared["refresh_task_id"])
        except BaseException as exc:  # never retried
            entry["agent"] = {"status": "EXCEPTION", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            after = await ledger(factory)
            entry["ledger"] = delta(before, after)
            spent_http += entry["ledger"]["http"]
            spent_tokens += entry["ledger"]["tokens"]
            report["jds"][key] = entry
            report["stopped"] = {"reason": "AGENT_EXCEPTION", "jd_key": key, "error": entry["agent"]["error"]}
            break
        entry["agent"] = {k: result.get(k) for k in
                          ("status", "company_id", "job_id", "jd_version_id", "publication_status",
                           "error")}
        after = await ledger(factory)
        entry["ledger"] = delta(before, after)
        spent_http += entry["ledger"]["http"]
        spent_tokens += entry["ledger"]["tokens"]
        report["jds"][key] = entry
        if result.get("status") != "COMPLETED":
            # A content/evidence rejection stays FAILED and does not block the next
            # JD; only an undecidable send state or a budget breach stops the batch.
            message = str(result.get("error") or "")
            fatal = [token for token in ("UNKNOWN", "QUOTA", "quota", "LEDGER", "ledger", "ownership")
                     if token in message]
            if fatal:
                report["stopped"] = {"reason": "FATAL_ERROR", "jd_key": key, "error": message}
                break
            report.setdefault("failed", []).append({"jd_key": key, "error": message})
    report["spent"] = {"http": spent_http, "tokens": spent_tokens,
                       "normal_http": budget["normal_http"], "max_http": budget["max_http"],
                       "max_tokens": budget["max_tokens"]}
    final = await ledger(factory)
    report["ledger_total"] = {"http_attempts": final["http_attempts"], "tokens": final["tokens"],
                              "statuses": final["statuses"], "models": final["models"]}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("nio", "batch2"))
    parser.add_argument("--database", default=SANDBOX)
    parser.add_argument("--report", default=None)
    parser.add_argument("--prep-only", action="store_true",
                        help="NIO mode: create and verify the revision task without any model call")
    args = parser.parse_args()
    budget = BUDGETS[args.mode]
    env = guard_environment(args.database)

    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{args.database}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ManualImportService(factory)
    agent = PublicDataResearchAgent(factory, build_model_router())

    report: dict = {"mode": args.mode, "database": args.database, "environment": env,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "budget": budget, "jds": {}}
    try:
        if args.mode == "nio":
            await run_nio(service, agent, factory, report, budget, prep_only=args.prep_only)
        else:
            await run_batch2(service, agent, factory, report, budget)
    finally:
        await engine.dispose()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    path = Path(args.report or (OUT / f"{args.mode}_report.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str)[:4000])
    print("written", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
