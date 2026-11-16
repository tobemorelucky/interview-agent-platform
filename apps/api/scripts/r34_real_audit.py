"""R31 formal: audit the real-model import, dry-check publish readiness, prove idempotency.

Read-only except an idempotency re-prepare that must not create a new task.
Mirrors PublicCareerReviewService.publish_jd preconditions *without* publishing.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.public_career.manual_import import ManualDocument, ManualImportService
from interview_api.modules.public_career.models import (
    Claim, ClaimEvidence, Company, Job, JobDescriptionVersion, JobRequirement,
    PublicDataRefreshTask, Source, SourceSnapshot,
)
from interview_api.modules.public_career.fingerprints import normalize_text

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "r34_target_jd"
MANIFEST = DATA / "source_manifest.json"


def norm(text: str) -> str:
    return "".join(normalize_text(text or "").split())


async def audit(factory, jd_id: int) -> dict:
    async with factory() as db:
        jd = await db.get(JobDescriptionVersion, jd_id)
        job = await db.get(Job, jd.job_id)
        company = await db.get(Company, job.company_id)
        snapshot = await db.get(SourceSnapshot, jd.source_snapshot_id)
        source = await db.get(Source, snapshot.source_id) if snapshot else None
        requirements = list((await db.scalars(select(JobRequirement).where(
            JobRequirement.job_description_version_id == jd_id).order_by(JobRequirement.id))).all())
        rows = []
        for requirement in requirements:
            claim = await db.get(Claim, requirement.claim_id) if requirement.claim_id else None
            evidence = list((await db.scalars(select(ClaimEvidence).where(
                ClaimEvidence.claim_id == requirement.claim_id))).all()) if claim else []
            evidence.sort(key=lambda e: ((e.source_location_json or {}).get("start_char") or 0))
            offsets_ok, joined, excerpts = True, "", []
            for item in evidence:
                loc = item.source_location_json or {}
                start, end = loc.get("start_char"), loc.get("end_char")
                text = snapshot.content_text or ""
                if not isinstance(start, int) or not isinstance(end, int) or text[start:end] != item.excerpt:
                    offsets_ok = False
                if sha256(text.encode()).hexdigest() != snapshot.content_hash:
                    offsets_ok = False
                joined += item.excerpt
                excerpts.append({"start": start, "end": end, "excerpt": item.excerpt})
            rows.append({
                "id": requirement.id, "statement": requirement.statement,
                "requirement_type": requirement.requirement_type, "importance": requirement.importance,
                "claim_id": requirement.claim_id, "claim_statement": claim.statement if claim else None,
                "evidence_rows": len(evidence), "offsets_ok": offsets_ok,
                "literal_in_evidence": norm(requirement.statement) in norm(joined),
                "literal_in_raw_text": norm(requirement.statement) in norm(jd.raw_text or ""),
                "evidence": excerpts,
            })
        attributes = (jd.metadata_json or {}).get("attribute_claims") or []
        attr_rows = []
        for item in attributes:
            claim = await db.get(Claim, item["claim_id"]) if item.get("claim_id") else None
            attr_rows.append({"field": item.get("field"), "claim_id": item.get("claim_id"),
                              "statement": claim.statement if claim else None,
                              "literal": bool(claim) and norm(claim.statement) in norm(snapshot.content_text or "")})
        identity_claim = await db.get(Claim, (jd.metadata_json or {}).get("identity_claim_id"))
        identity_evidence = list((await db.scalars(select(ClaimEvidence).where(
            ClaimEvidence.claim_id == identity_claim.id))).all()) if identity_claim else []

        return {
            "jd_version_id": jd.id, "job_id": jd.job_id, "version_number": jd.version_number,
            "company": company.canonical_name, "job_title": job.canonical_title,
            "publication_status": jd.publication_status,
            "company_status": company.status, "job_status": job.status,
            "source_url": source.canonical_url if source else None,
            "snapshot_chars": len(snapshot.content_text or ""),
            "snapshot_sha256": snapshot.content_hash,
            "snapshot_sha256_ok": sha256((snapshot.content_text or "").encode()).hexdigest() == snapshot.content_hash,
            "raw_text_equals_snapshot": (jd.raw_text or "") == (snapshot.content_text or ""),
            "raw_text_sha256": sha256((jd.raw_text or "").encode()).hexdigest(),
            "content_fingerprint": jd.content_fingerprint,
            "structured_requirements": len((jd.structured_data_json or {}).get("requirements") or []),
            "structured_attributes": len((jd.structured_data_json or {}).get("attributes") or []),
            "quality_assessment": (jd.metadata_json or {}).get("quality_assessment"),
            "quality_gate": (jd.metadata_json or {}).get("quality_gate"),
            "credibility": (jd.metadata_json or {}).get("credibility"),
            "manual_source_type": ((jd.metadata_json or {}).get("manual_source") or {}).get("source_type"),
            "identity_claim": {"statement": identity_claim.statement if identity_claim else None,
                               "evidence_rows": len(identity_evidence),
                               "evidence": [e.excerpt for e in identity_evidence]},
            "requirements": rows,
            "attribute_claims": attr_rows,
            "counts": {"total": len(rows),
                       "REQUIRED": sum(1 for r in rows if r["importance"] == "REQUIRED"),
                       "PREFERRED": sum(1 for r in rows if r["importance"] == "PREFERRED"),
                       "other": sum(1 for r in rows if r["importance"] not in {"REQUIRED", "PREFERRED"}),
                       "missing_claim": sum(1 for r in rows if not r["claim_id"]),
                       "offsets_bad": sum(1 for r in rows if not r["offsets_ok"]),
                       "literal_bad": sum(1 for r in rows if not r["literal_in_evidence"])},
        }


async def publish_readiness(factory, jd_id: int) -> dict:
    """Mirror review.publish_jd checks; no write."""
    async with factory() as db:
        jd = await db.get(JobDescriptionVersion, jd_id)
        job = await db.get(Job, jd.job_id)
        company = await db.get(Company, job.company_id)
        requirements = list((await db.scalars(select(JobRequirement).where(
            JobRequirement.job_description_version_id == jd_id))).all())
        claim_ids = [r.claim_id for r in requirements] + [(jd.metadata_json or {}).get("identity_claim_id")]
        claim_ids += [item["claim_id"] for item in (jd.metadata_json or {}).get("attribute_claims", [])]
        problems = []
        if jd.publication_status not in {"WAITING_REVIEW", "APPROVED"}:
            problems.append(f"STATE:{jd.publication_status}")
        if job.status not in {"ACTIVE", "WAITING_REVIEW"} or company.status not in {"ACTIVE", "WAITING_REVIEW"}:
            problems.append("CLOSED_JOB")
        if not requirements or any(c is None for c in claim_ids):
            problems.append("JD_EVIDENCE_REQUIRED")
        for claim_id in set(claim_ids):
            claim = await db.get(Claim, claim_id)
            evidence = list((await db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == claim_id))).all())
            if not claim or not evidence:
                problems.append(f"MISSING_EVIDENCE:{claim_id}")
                continue
            for item in evidence:
                snapshot = await db.get(SourceSnapshot, item.source_snapshot_id)
                loc = item.source_location_json or {}
                start, end = loc.get("start_char"), loc.get("end_char")
                if (not snapshot or not isinstance(start, int) or not isinstance(end, int)
                        or not 0 <= start < end <= len(snapshot.content_text or "")
                        or snapshot.content_text[start:end] != item.excerpt
                        or sha256(snapshot.content_text.encode()).hexdigest() != snapshot.content_hash):
                    problems.append(f"BAD_OFFSET:{claim_id}")
        return {"jd_version_id": jd.id, "publishable": not problems, "problems": problems}


async def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    targets = {record["jd_key"]: record for record in manifest["records"]}
    engine = create_async_engine("postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_wb_jd_r31_20261116")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        rows = (await db.execute(select(JobDescriptionVersion.id, Company.canonical_name, Job.canonical_title)
                .join(Job, Job.id == JobDescriptionVersion.job_id)
                .join(Company, Company.id == Job.company_id))).all()
        tasks = list((await db.scalars(select(PublicDataRefreshTask).order_by(PublicDataRefreshTask.id))).all())
        audit_count = await db.scalar(select(func.count()).select_from(AuditLog))
        admissions = await db.scalar(select(func.count()).select_from(ClaimEvidence))

    out = {"checked_at": datetime.now(timezone.utc).isoformat(),
           "jd_versions": [{"jd_version_id": r[0], "company": r[1], "job": r[2]} for r in rows],
           "tasks": [{"id": t.id, "status": t.status, "error": (t.error_message or "")[:160]} for t in tasks],
           "audit_logs": audit_count, "claim_evidence_rows": admissions,
           "jds": {}, "readiness": {}, "idempotency": {}}

    for jd_version_id, company, _title in rows:
        out["jds"][f"{company}#{jd_version_id}"] = await audit(factory, jd_version_id)
        out["readiness"][f"{company}#{jd_version_id}"] = await publish_readiness(factory, jd_version_id)

    # Idempotency on the completed Kylinos JD: same bytes must reuse the same task.
    target = targets["KYLINOS_AI_002"]
    service = ManualImportService(factory)
    again = await service.prepare(ManualDocument.from_file(
        file=REPO / target["text_path"], source_url=target["source_url"], source_type="official",
        document_type="job_jd", company=target["company"], job_title=target["job_title"]))
    out["idempotency"] = {"prepare_status": again["status"], "reused": again["reused"],
                          "refresh_task_id": again["refresh_task_id"]}

    await engine.dispose()
    (DATA / "real_run" / "audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for key, row in out["jds"].items():
        print(f"{key:24} req={row['counts']['total']:>2} (R{row['counts']['REQUIRED']}/P{row['counts']['PREFERRED']}) "
              f"offsets_bad={row['counts']['offsets_bad']} literal_bad={row['counts']['literal_bad']} "
              f"quality={row['quality_assessment']} status={row['publication_status']}")
    print("readiness", json.dumps(out["readiness"], ensure_ascii=False))
    print("idempotency", json.dumps(out["idempotency"], ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
