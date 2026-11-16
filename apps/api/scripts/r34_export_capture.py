"""R31 formal: redacted capture/replay bundle for the real-model import.

Contains only public JD material, ledger rows and step metadata. No API keys, no
prompts with credentials, no user data.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.public_career.models import JobDescriptionVersion

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "r34_target_jd" / "real_run"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="interview_agent_wb_jd_r31_20261116")
    args = parser.parse_args()
    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{args.database}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        admissions = list((await db.scalars(select(ModelAdmission).order_by(ModelAdmission.created_at))).all())
        runs = list((await db.scalars(select(ExperienceAgentRun).order_by(ExperienceAgentRun.id))).all())
        steps = list((await db.scalars(select(ExperienceAgentStepRun).order_by(ExperienceAgentStepRun.id))).all())
        versions = list((await db.scalars(select(JobDescriptionVersion).order_by(JobDescriptionVersion.id))).all())

    bundle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": args.database,
        "redaction": {"contains": ["public JD text", "ledger rows", "step metadata", "structured extraction"],
                      "excludes": ["api keys", "authorization headers", "user data", "raw provider responses"]},
        "ledger": [{"call_key": a.call_key, "route": a.route, "model": a.model, "status": a.status,
                    "reserved_tokens": a.reserved_tokens, "actual_tokens": a.actual_tokens,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "finished_at": a.finished_at.isoformat() if a.finished_at else None} for a in admissions],
        "runs": [{"id": r.id, "refresh_task_id": r.refresh_task_id, "status": r.status,
                  "error": r.error_message, "started_at": r.started_at.isoformat() if r.started_at else None,
                  "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                  "output_keys": sorted((r.output_json or {}).keys())} for r in runs],
        "steps": [{"id": s.id, "agent_run_id": s.agent_run_id, "step": s.step_name, "status": s.status,
                   "prompt": f"{s.prompt_name}@{s.prompt_version}", "provider": s.provider_name,
                   "model": s.model_name, "usage": s.token_usage_json, "structured_retry": s.structured_retry,
                   "latency_ms": s.latency_ms, "error": s.error_message,
                   "input_json": s.input_json, "output_json": s.output_json} for s in steps],
        "jd_versions": [{"id": v.id, "job_id": v.job_id, "version": v.version_number,
                         "publication_status": v.publication_status,
                         "raw_text_sha256": sha256((v.raw_text or "").encode()).hexdigest(),
                         "structured_data": v.structured_data_json,
                         "metadata_keys": sorted((v.metadata_json or {}).keys())} for v in versions],
        "totals": {"http_attempts": len(admissions),
                   "tokens": sum((a.actual_tokens if a.actual_tokens is not None else (a.reserved_tokens or 0))
                                 for a in admissions),
                   "repairs": sum(1 for s in steps if s.structured_retry)},
    }
    await engine.dispose()
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "capture_replay.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(bundle["totals"], ensure_ascii=False))
    print("statuses", sorted({row["status"] for row in bundle["ledger"]}))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
