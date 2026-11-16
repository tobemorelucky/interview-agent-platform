"""R31 formal: real Research Agent import of the three approved real JDs.

Only the two model-backed stages (job_jd extraction + quality assessment) issue
HTTP. Counts and tokens come from the governed ledger (`model_admissions`), not
from local counters. Hard limits are checked before every JD; a failure stops
the run with no retry.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.core.config import settings
from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.infrastructure.models.router import build_model_router
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.public_career.manual_import import ManualDocument, ManualImportService
from interview_api.modules.public_career.models import (
    Claim, ClaimEvidence, Company, Job, JobDescriptionVersion, JobRequirement,
    PublicDataRefreshTask, SourceSnapshot,
)
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "r34_target_jd"
MANIFEST = DATA / "source_manifest.json"
OUT = DATA / "real_run"

EXPECTED_DATABASE = "interview_agent_wb_jd_r31_20261116"
EXPECTED_PROVIDER = "modelscope"
EXPECTED_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash-1017"
MAX_HTTP = 10
MAX_TOKENS = 30000
ORDER = ["KYLINOS_AI_002", "NIO_AIA_025", "MOONTON_AGENT_005"]


class BudgetStop(RuntimeError):
    pass


async def ledger(factory) -> dict:
    async with factory() as db:
        rows = (await db.execute(select(ModelAdmission.call_key, ModelAdmission.route, ModelAdmission.status,
                                        ModelAdmission.reserved_tokens, ModelAdmission.actual_tokens,
                                        ModelAdmission.model))).all()
        steps = (await db.execute(select(ExperienceAgentStepRun.step_name, ExperienceAgentStepRun.status,
                                          ExperienceAgentStepRun.token_usage_json, ExperienceAgentStepRun.structured_retry,
                                          ExperienceAgentStepRun.error_message))).all()
    calls = len(rows)
    tokens = sum((row[4] if row[4] is not None else (row[3] or 0)) for row in rows)
    return {"http_attempts": calls,
            "tokens": tokens,
            "statuses": sorted({row[2] for row in rows}),
            "routes": sorted({row[1] for row in rows}),
            "models": sorted({row[5] for row in rows}),
            "steps": [{"step": step[0], "status": step[1], "usage": step[2],
                       "structured_retry": step[3], "error": step[4]} for step in steps]}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=EXPECTED_DATABASE)
    parser.add_argument("--report", default=str(OUT / "real_run_report.json"))
    parser.add_argument("--only", default=None,
                        help="comma separated jd_key list; used to finish the authorised scope without retrying a failed JD")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "")
    ledger_env = os.getenv("MODEL_LEDGER_DATABASE", "")
    assert database_url.endswith("/" + args.database), f"DATABASE_URL not the sandbox: {database_url}"
    assert ledger_env == args.database, f"MODEL_LEDGER_DATABASE not the sandbox: {ledger_env}"
    assert settings.public_research_official_domains == {}, "official domains must stay unconfigured"

    router = build_model_router()
    providers = {route: router.primary(route) for route in ("public_research_extract", "public_research_quality")}
    for route, provider in providers.items():
        assert provider.provider_name == EXPECTED_PROVIDER, (route, provider.provider_name)
        assert provider.model_name == EXPECTED_MODEL, (route, provider.model_name)
        assert getattr(provider, "governed", False) is True, route
    assert router.fallback_routes == {}, "no fallback routes allowed"

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    targets = {record["jd_key"]: record for record in manifest["records"]}

    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{args.database}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ManualImportService(factory)
    agent = PublicDataResearchAgent(factory, router)
    OUT.mkdir(parents=True, exist_ok=True)

    report = {"database": args.database, "started_at": datetime.now(timezone.utc).isoformat(),
              "provider": {route: {"provider": provider.provider_name, "model": provider.model_name}
                           for route, provider in providers.items()},
              "limits": {"max_http": MAX_HTTP, "max_tokens": MAX_TOKENS},
              "prompt_versions": {"job_jd": "1.0.1", "quality": "1.0.0",
                                  "note": "resolved by ManualSourcePromptRegistry"},
              "jds": {}, "stopped": None}

    order = [k.strip() for k in args.only.split(",")] if args.only else ORDER
    for key in order:
        before = await ledger(factory)
        if before["http_attempts"] >= MAX_HTTP or before["tokens"] >= MAX_TOKENS:
            report["stopped"] = {"reason": "BUDGET_EXHAUSTED_BEFORE_JD", "jd_key": key, **before}
            break
        target = targets[key]
        entry: dict = {"textarea": {"chars": target["text_chars"], "sha256": target["text_sha256"]}}
        document = ManualDocument.from_file(file=REPO / target["text_path"], source_url=target["source_url"],
                                            source_type="official", document_type="job_jd",
                                            company=target["company"], job_title=target["job_title"])
        prepared = await service.prepare(document)
        entry["prepared"] = {k: prepared[k] for k in ("refresh_task_id", "source_snapshot_id", "status", "reused")}
        try:
            result = await agent.run(prepared["refresh_task_id"])
        except BaseException as exc:  # never retried here
            entry["agent"] = {"status": "EXCEPTION", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            report["jds"][key] = entry
            report["stopped"] = {"reason": "AGENT_EXCEPTION", "jd_key": key,
                                 "error": entry["agent"]["error"], **(await ledger(factory))}
            break
        entry["agent"] = {k: result.get(k) for k in (
            "status", "company_id", "job_id", "jd_version_id", "publication_status",
            "manual_publication_decision", "manual_source_type", "error")}
        after = await ledger(factory)
        entry["ledger"] = {"http_attempts": after["http_attempts"] - before["http_attempts"],
                           "tokens": after["tokens"] - before["tokens"],
                           "statuses": after["statuses"]}
        report["jds"][key] = entry
        if result.get("status") != "COMPLETED":
            report["stopped"] = {"reason": "AGENT_NOT_COMPLETED", "jd_key": key,
                                 "error": result.get("error"), **after}
            break

    report["ledger_total"] = await ledger(factory)
    await engine.dispose()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"jds": {k: v["agent"] for k, v in report["jds"].items()},
                      "ledger": report["ledger_total"], "stopped": report["stopped"]},
                     ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"FATAL {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
