"""R31: post-rehearsal verification + budget measurement (zero HTTP)."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.core.config import settings
from interview_api.modules.public_career.models import (
    Claim, ClaimEvidence, Company, Job, JobDescriptionVersion, JobRequirement,
    PublicDataRefreshTask, Source, SourceSnapshot,
)
from interview_api.modules.public_career.research.prompts import ManualSourcePromptRegistry

REPO = Path(__file__).resolve().parents[3]
REPORT = REPO / "data" / "r34_target_jd" / "replay_report.json"


def norm(text: str) -> str:
    return re.sub(r"[\s\u3000]", "", text or "")


def factory_for(database: str):
    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{database}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def verify_evidence(factory, jd_id: int) -> dict:
    """Re-check offsets and literal containment in *positional* order."""
    async with factory() as db:
        jd = await db.get(JobDescriptionVersion, jd_id)
        requirements = list((await db.scalars(select(JobRequirement).where(
            JobRequirement.job_description_version_id == jd_id).order_by(JobRequirement.id))).all())
        snapshot = await db.get(SourceSnapshot, jd.source_snapshot_id)
        rows = []
        for requirement in requirements:
            evidence = list((await db.scalars(select(ClaimEvidence).where(
                ClaimEvidence.claim_id == requirement.claim_id))).all())
            evidence.sort(key=lambda e: ((e.source_location_json or {}).get("start_char") or 0))
            exact, joined = True, ""
            for item in evidence:
                loc = item.source_location_json or {}
                start, end = loc.get("start_char"), loc.get("end_char")
                text = snapshot.content_text or ""
                if not isinstance(start, int) or not isinstance(end, int) or text[start:end] != item.excerpt:
                    exact = False
                joined += item.excerpt
            rows.append({"id": requirement.id, "importance": requirement.importance,
                         "type": requirement.requirement_type,
                         "statement": requirement.statement,
                         "offset_exact": exact, "literal": norm(requirement.statement) in norm(joined),
                         "evidence_rows": len(evidence)})
        return {
            "jd_id": jd_id, "job_id": jd.job_id, "version": jd.version_number,
            "publication_status": jd.publication_status,
            "raw_text_matches_snapshot": (jd.raw_text or "") == (snapshot.content_text or ""),
            "raw_text_sha256": sha256((jd.raw_text or "").encode()).hexdigest(),
            "snapshot_sha256_ok": sha256((snapshot.content_text or "").encode()).hexdigest() == snapshot.content_hash,
            "metadata": {
                "identity_claim_id": (jd.metadata_json or {}).get("identity_claim_id"),
                "attribute_claims": len((jd.metadata_json or {}).get("attribute_claims") or []),
                "quality_gate": (jd.metadata_json or {}).get("quality_gate"),
                "manual_source_type": ((jd.metadata_json or {}).get("manual_source") or {}).get("source_type"),
                "credibility": (jd.metadata_json or {}).get("credibility"),
                "independent_content_count": (jd.metadata_json or {}).get("independent_content_count"),
            },
            "requirements": rows,
            "requirement_total": len(rows),
            "offset_exact": sum(1 for r in rows if r["offset_exact"]),
            "literal": sum(1 for r in rows if r["literal"]),
        }


async def main() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    engine, factory = factory_for(report["database"])
    out = {"checked_at": datetime.now(timezone.utc).isoformat(), "database": report["database"], "jds": {}}
    for key, entry in report["jds"].items():
        jd_id = entry["agent_result"]["jd_version_id"]
        out["jds"][key] = await verify_evidence(factory, jd_id)

    # Skill recommendation explainability: full public output for one job.
    from interview_api.modules.interview.skill_recommendation import recommend_jobs
    async with factory() as db:
        jobs = (await db.execute(select(Job.id, Job.canonical_title))).all()
        recommendations = await recommend_jobs(db, [job_id for job_id, _ in jobs])
    out["skill_recommendations"] = [{"job_id": job_id, "job_title": title,
                                     "output": recommendations.get(job_id)} for job_id, title in jobs]

    # Payload measurement for the real-call budget.
    registry = ManualSourcePromptRegistry()
    prompts = {"job_jd@1.0.1": registry.get("job_jd").system, "quality@1.0.0": registry.get("quality").system}
    out["prompts"] = {name: {"chars": len(text), "sha256": sha256(text.encode()).hexdigest()[:16]}
                      for name, text in prompts.items()}
    payload = {}
    async with factory() as db:
        for key, entry in report["jds"].items():
            snapshot = await db.get(SourceSnapshot, entry["prepared"]["source_snapshot_id"])
            jd = await db.get(JobDescriptionVersion, entry["agent_result"]["jd_version_id"])
            fixtures = entry["fixture"]["requirements"]
            payload[key] = {
                "evidence_chars": len(snapshot.content_text or ""),
                "structured_output_chars": len(json.dumps(jd.structured_data_json, ensure_ascii=False)),
                "requirements": fixtures,
            }
    out["payload"] = payload
    await engine.dispose()

    Path(REPO / "data" / "r34_target_jd" / "verify_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for key, row in out["jds"].items():
        print(f"{key:20} req={row['requirement_total']:>2} offset_exact={row['offset_exact']:>2} "
              f"literal={row['literal']:>2} raw_text_match={row['raw_text_matches_snapshot']} "
              f"status={row['publication_status']}")
    print("prompts", {k: v["chars"] for k, v in out["prompts"].items()})
    print("payload", json.dumps(payload, ensure_ascii=False))
    for item in out["skill_recommendations"]:
        print(item["job_title"], "->", item["output"].get("status"), item["output"].get("recommended_skill_id"),
              "|", (item["output"].get("reason") or "")[:60])


if __name__ == "__main__":
    asyncio.run(main())
