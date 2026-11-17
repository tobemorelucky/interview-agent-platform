"""SHOWCASE-R1 / part 2: import the six re-checked JDs with the real model.

Budget: 12 HTTP normal, 16 hard, 30,000 tokens normal, 45,000 hard — every number
measured as a delta against the governed ledger, so a rerun cannot borrow a
sibling's allowance. Two consecutive non-COMPLETED results stop the batch; fatal
ledger/quota/UNKNOWN errors stop it immediately. Evidence rejections are not
repaired: the same prompt re-run is not an engineering fix.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
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
    ManualDocument, ManualImportService,
)
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent  # noqa: E402

SANDBOX = "interview_agent_wb_jd_r31_20261116"
EXPECTED_PROVIDER = "modelscope"
EXPECTED_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash-1017"
MANIFEST = REPO / "data/r55_batch4/source_manifest.json"
OUT = REPO / "data/r57_batch4_run"
BUDGET = {"normal_http": 12, "max_http": 16, "normal_tokens": 30000, "max_tokens": 45000,
          "max_consecutive_failures": 2}


class BudgetStop(RuntimeError):
    pass


async def ledger(factory) -> dict:
    async with factory() as db:
        rows = (await db.execute(select(ModelAdmission.status, ModelAdmission.route,
                                        ModelAdmission.reserved_tokens, ModelAdmission.actual_tokens,
                                        ModelAdmission.model))).all()
        steps = (await db.execute(select(ExperienceAgentStepRun.id, ExperienceAgentStepRun.agent_run_id,
                                          ExperienceAgentStepRun.step_name,
                                          ExperienceAgentStepRun.status,
                                          ExperienceAgentStepRun.token_usage_json,
                                          ExperienceAgentStepRun.structured_retry,
                                          ExperienceAgentStepRun.error_message)
                                  .order_by(ExperienceAgentStepRun.id))).all()
    return {"http_attempts": len(rows),
            "tokens": sum((row[3] if row[3] is not None else (row[2] or 0)) for row in rows),
            "statuses": sorted({row[0] for row in rows}),
            "models": sorted({row[4] for row in rows}),
            "step_count": len(steps),
            "steps": [{"id": s[0], "run": s[1], "step": s[2], "status": s[3], "usage": s[4],
                       "structured_retry": s[5], "error": (s[6] or "")[:200]} for s in steps]}


def guard_environment(database: str) -> dict:
    url = os.getenv("DATABASE_URL", "")
    ledger_env = os.getenv("MODEL_LEDGER_DATABASE", "")
    if not url.endswith("/" + database):
        raise SystemExit(f"REFUSED: DATABASE_URL is not {database}")
    if ledger_env != database:
        raise SystemExit(f"REFUSED: MODEL_LEDGER_DATABASE={ledger_env!r} != {database}")
    router = build_model_router()
    resolved = {}
    for route in ("public_research_extract", "public_research_quality"):
        provider = router.primary(route)
        if provider.provider_name != EXPECTED_PROVIDER or provider.model_name != EXPECTED_MODEL:
            raise SystemExit(f"REFUSED: {route} -> {provider.provider_name}/{provider.model_name}")
        if not getattr(provider, "governed", False):
            raise SystemExit(f"REFUSED: {route} is not governed")
        resolved[route] = {"provider": provider.provider_name, "model": provider.model_name,
                           "timeout": getattr(provider, "timeout", None)}
    if router.fallback_routes:
        raise SystemExit(f"REFUSED: fallback routes configured: {list(router.fallback_routes)}")
    return {"database": database, "routes": resolved,
            "modelscope": settings.use_modelscope}


def delta(before: dict, after: dict) -> dict:
    return {"http": after["http_attempts"] - before["http_attempts"],
            "tokens": after["tokens"] - before["tokens"],
            "new_steps": after["step_count"] - before["step_count"]}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=SANDBOX)
    parser.add_argument("--report", default=str(OUT / "batch4_report.json"))
    parser.add_argument("--dry-run", action="store_true",
                        help="prepare the six documents only; zero model calls")
    args = parser.parse_args()
    env = guard_environment(args.database)

    engine = create_async_engine(
        f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{args.database}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ManualImportService(factory)
    agent = PublicDataResearchAgent(factory, build_model_router())

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    report: dict = {"round": "SHOWCASE-R1", "database": args.database, "environment": env,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "budget": BUDGET, "jds": {}, "failures": [], "stopped": None}

    base = await ledger(factory)
    report["ledger_baseline"] = {"http": base["http_attempts"], "tokens": base["tokens"]}
    spent_http = spent_tokens = 0
    consecutive = 0

    try:
        for record in manifest["records"]:
            key = record["jd_key"]
            if spent_http >= BUDGET["max_http"]:
                report["stopped"] = {"reason": "HTTP_HARD_LIMIT", "jd_key": key,
                                     "http": spent_http, "tokens": spent_tokens}
                break
            if spent_tokens >= BUDGET["max_tokens"]:
                report["stopped"] = {"reason": "TOKEN_HARD_LIMIT", "jd_key": key,
                                     "http": spent_http, "tokens": spent_tokens}
                break
            before = await ledger(factory)
            entry: dict = {"company": record["company"], "job_title": record["job_title"],
                           "direction": record["direction"], "job_id": record["job_id"],
                           "source_url": record["source_url"],
                           "text_chars": record["text_chars"], "text_sha256": record["text_sha256"]}
            document = ManualDocument.from_file(
                file=REPO / record["text_path"], source_url=record["source_url"],
                source_type="official", document_type="job_jd", company=record["company"],
                job_title=record["job_title"])
            prepared = await service.prepare(document)
            entry["prepared"] = {k: prepared[k] for k in
                                 ("refresh_task_id", "source_snapshot_id", "status", "reused")}
            if args.dry_run:
                entry["agent"] = {"status": "DRY_RUN"}
                report["jds"][key] = entry
                continue
            try:
                result = await agent.run(prepared["refresh_task_id"])
            except BaseException as exc:  # never retried
                entry["agent"] = {"status": "EXCEPTION",
                                  "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
                after = await ledger(factory)
                entry["ledger"] = delta(before, after)
                spent_http += entry["ledger"]["http"]
                spent_tokens += entry["ledger"]["tokens"]
                report["jds"][key] = entry
                report["stopped"] = {"reason": "AGENT_EXCEPTION", "jd_key": key,
                                     "error": entry["agent"]["error"]}
                break

            entry["agent"] = {k: result.get(k) for k in
                              ("status", "company_id", "job_id", "jd_version_id",
                               "publication_status", "error")}
            after = await ledger(factory)
            entry["ledger"] = delta(before, after)
            entry["steps"] = after["steps"][-entry["ledger"]["new_steps"]:] \
                if entry["ledger"]["new_steps"] else []
            spent_http += entry["ledger"]["http"]
            spent_tokens += entry["ledger"]["tokens"]
            report["jds"][key] = entry

            if result.get("status") == "COMPLETED":
                consecutive = 0
                continue
            message = str(result.get("error") or "")
            report["failures"].append({"jd_key": key, "error": message[:300]})
            fatal = [t for t in ("UNKNOWN", "QUOTA", "quota", "LEDGER", "ledger", "ownership",
                                 "denied") if t in message]
            if fatal:
                report["stopped"] = {"reason": "FATAL_ERROR", "jd_key": key, "error": message[:300]}
                break
            consecutive += 1
            if consecutive >= BUDGET["max_consecutive_failures"]:
                report["stopped"] = {"reason": "TWO_CONSECUTIVE_FAILURES", "jd_key": key}
                break
    finally:
        final = await ledger(factory)
        report["spent"] = {"http": spent_http, "tokens": spent_tokens,
                           "normal_http": BUDGET["normal_http"], "max_http": BUDGET["max_http"],
                           "max_tokens": BUDGET["max_tokens"]}
        report["ledger_total"] = {"http_attempts": final["http_attempts"],
                                  "tokens": final["tokens"],
                                  "statuses": final["statuses"], "models": final["models"]}
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        await engine.dispose()

    for key, entry in report["jds"].items():
        agent_state = entry.get("agent", {})
        ledger_row = entry.get("ledger", {})
        print(f"{key:<24} {str(agent_state.get('status')):<10} "
              f"http={ledger_row.get('http', '-'):<3} tokens={ledger_row.get('tokens', '-'):<6} "
              f"jd={agent_state.get('jd_version_id')} {str(agent_state.get('error'))[:60]}")
    print("spent", report["spent"])
    print("ledger_total", report["ledger_total"])
    print("stopped", report["stopped"])
    print("written", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
