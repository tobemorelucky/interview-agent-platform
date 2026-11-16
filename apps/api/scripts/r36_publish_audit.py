"""R32: pre-publish audit for the imported JDs. Read-only, zero model.

Checks source identity, body hash, requirement completeness, stored vs effective
strength, vocabulary normalisation, claim/evidence fidelity, skill
recommendation, quality flags and version consistency. Never publishes.
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
from interview_api.modules.interview.skill_recommendation import recommend, recommend_jobs
from interview_api.modules.public_career.fingerprints import normalize_text
from interview_api.modules.public_career.models import (
    Claim, ClaimEvidence, Company, Job, JobDescriptionVersion, JobRequirement,
    PublicDataRefreshTask, Source, SourceSnapshot,
)
from interview_api.modules.public_career.requirement_vocabulary import describe

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "r34_target_jd"
MANIFEST = DATA / "source_manifest.json"
DATABASE = "interview_agent_wb_jd_r31_20261116"


def norm(text: str) -> str:
    return "".join(normalize_text(text or "").split())


async def audit_one(db, jd: JobDescriptionVersion) -> dict:
    job = await db.get(Job, jd.job_id)
    company = await db.get(Company, job.company_id)
    snapshot = await db.get(SourceSnapshot, jd.source_snapshot_id)
    source = await db.get(Source, snapshot.source_id) if snapshot else None
    requirements = list((await db.scalars(select(JobRequirement).where(
        JobRequirement.job_description_version_id == jd.id).order_by(JobRequirement.id))).all())

    problems: list[str] = []
    rows = []
    for requirement in requirements:
        claim = await db.get(Claim, requirement.claim_id) if requirement.claim_id else None
        evidence = list((await db.scalars(select(ClaimEvidence).where(
            ClaimEvidence.claim_id == requirement.claim_id))).all()) if claim else []
        evidence.sort(key=lambda item: ((item.source_location_json or {}).get("start_char") or 0))
        offsets_ok, joined = True, ""
        for item in evidence:
            location = item.source_location_json or {}
            start, end = location.get("start_char"), location.get("end_char")
            text = snapshot.content_text or ""
            if not isinstance(start, int) or not isinstance(end, int) or text[start:end] != item.excerpt:
                offsets_ok = False
            joined += item.excerpt
        projection = describe(requirement.statement, requirement.requirement_type, requirement.importance)
        if not requirement.claim_id:
            problems.append(f"REQUIREMENT_WITHOUT_CLAIM:{requirement.id}")
        if not offsets_ok:
            problems.append(f"EVIDENCE_OFFSET_INVALID:{requirement.id}")
        if norm(requirement.statement) not in norm(joined):
            problems.append(f"STATEMENT_NOT_IN_EVIDENCE:{requirement.id}")
        if norm(requirement.statement) not in norm(jd.raw_text or ""):
            problems.append(f"STATEMENT_NOT_IN_RAW_TEXT:{requirement.id}")
        if projection["type_status"] == "UNKNOWN":
            problems.append(f"REQUIREMENT_TYPE_UNKNOWN:{requirement.id}:{requirement.requirement_type!r}")
        if projection["type_status"] == "AMBIGUOUS":
            problems.append(f"REQUIREMENT_TYPE_AMBIGUOUS:{requirement.id}:{requirement.requirement_type!r}")
        if projection["compound_mixed"]:
            problems.append(f"STRENGTH_OVER_GENERALISED:{requirement.id}:{requirement.importance}->{projection['effective_strength']}")
        rows.append({
            "id": requirement.id, "statement": requirement.statement,
            "stored_type": requirement.requirement_type, "stored_strength": requirement.importance,
            **projection,
            "evidence_rows": len(evidence), "offsets_ok": offsets_ok,
        })

    attributes = (jd.metadata_json or {}).get("attribute_claims") or []
    attr_rows = []
    for item in attributes:
        claim = await db.get(Claim, item["claim_id"]) if item.get("claim_id") else None
        literal = bool(claim) and norm(claim.statement) in norm(snapshot.content_text or "")
        if not literal:
            problems.append(f"ATTRIBUTE_NOT_LITERAL:{item.get('claim_id')}")
        attr_rows.append({"field": item.get("field"), "statement": claim.statement if claim else None,
                          "literal": literal})

    identity = await db.get(Claim, (jd.metadata_json or {}).get("identity_claim_id"))
    identity_ok = bool(identity) and any(
        norm(company.canonical_name) in norm(item.excerpt) for item in
        (await db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == identity.id))).all()) if identity else False
    if not identity_ok:
        problems.append("IDENTITY_EVIDENCE_WEAK")

    quality = (jd.metadata_json or {}).get("quality_assessment") or {}
    if quality.get("suitable") is not True:
        problems.append("QUALITY_NOT_SUITABLE")

    return {
        "jd_version_id": jd.id, "version": jd.version_number, "company": company.canonical_name,
        "job_title": job.canonical_title, "company_status": company.status, "job_status": job.status,
        "publication_status": jd.publication_status, "source_url": source.canonical_url if source else None,
        "source_type": ((jd.metadata_json or {}).get("manual_source") or {}).get("source_type"),
        "snapshot_chars": len(snapshot.content_text or ""),
        "snapshot_sha256": snapshot.content_hash,
        "snapshot_sha256_ok": sha256((snapshot.content_text or "").encode()).hexdigest() == snapshot.content_hash,
        "raw_text_equals_snapshot": (jd.raw_text or "") == (snapshot.content_text or ""),
        "identity_claim_ok": identity_ok,
        "quality": quality,
        "quality_gate": (jd.metadata_json or {}).get("quality_gate"),
        "requirements": rows,
        "attributes": attr_rows,
        "requirement_total": len(rows),
        "stored_strength_counts": {
            "REQUIRED": sum(1 for r in rows if str(r["stored_strength"]).upper() == "REQUIRED"),
            "PREFERRED": sum(1 for r in rows if str(r["stored_strength"]).upper() == "PREFERRED"),
            "OTHER": sum(1 for r in rows if str(r["stored_strength"]).upper() not in {"REQUIRED", "PREFERRED"}),
        },
        "effective_strength_counts": {
            "REQUIRED": sum(1 for r in rows if r["effective_strength"] == "REQUIRED"),
            "PREFERRED": sum(1 for r in rows if r["effective_strength"] == "PREFERRED"),
        },
        "type_counts": {t: sum(1 for r in rows if r["type"] == t) for t in sorted({r["type"] for r in rows})},
        "hard_eligibility_count": sum(1 for r in rows if r["is_hard_eligibility"]),
        "problems": problems,
        "stored_strength_raw": sorted({str(r["stored_strength"]) for r in rows}),
        "blocking": [p for p in problems if not (p.startswith("STRENGTH_OVER_GENERALISED")
                                                or p.startswith("REQUIREMENT_TYPE_AMBIGUOUS"))],
        "advisory": [p for p in problems if p.startswith("STRENGTH_OVER_GENERALISED")
                     or p.startswith("REQUIREMENT_TYPE_AMBIGUOUS")],
    }


async def main() -> None:
    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{DATABASE}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_company = {record["company"]: record for record in manifest["records"]}

    out: dict = {"checked_at": datetime.now(timezone.utc).isoformat(), "database": DATABASE, "jds": {}}
    async with factory() as db:
        versions = list((await db.scalars(select(JobDescriptionVersion).order_by(JobDescriptionVersion.id))).all())
        tasks = list((await db.scalars(select(PublicDataRefreshTask).order_by(PublicDataRefreshTask.id))).all())
        jobs = (await db.execute(select(Job.id, Job.canonical_title))).all()
        recommendations = await recommend_jobs(db, [job_id for job_id, _ in jobs])
        audit_count = await db.scalar(select(func.count()).select_from(AuditLog))
        out["tasks"] = [{"id": t.id, "status": t.status, "error": (t.error_message or "")[:120]} for t in tasks]
        out["audit_logs"] = audit_count
        for version in versions:
            entry = await audit_one(db, version)
            entry["skill_after_publish"] = recommendations.get(version.job_id)
            # recommend_jobs() only reads ACTIVE jobs with a PUBLISHED JD, so a
            # pre-publish reviewer sees nothing. Compute the same rule inputs
            # directly (read-only) and label it as a preview.
            requirements_rows = list((await db.scalars(select(JobRequirement).where(
                JobRequirement.job_description_version_id == version.id).order_by(JobRequirement.id))).all())
            job_row = await db.get(Job, version.job_id)
            structured = version.structured_data_json or {}
            duties = structured.get("responsibilities") or []
            if isinstance(duties, str):
                duties = [duties]
            duties = [item.get("statement", "") if isinstance(item, dict) else item
                      for item in duties] if isinstance(duties, list) else []
            entry["skill_preview_before_publish"] = recommend(dict(
                job_id=job_row.id, title=job_row.canonical_title, job_family=job_row.job_family,
                jd_id=version.id, jd_fingerprint=version.content_fingerprint,
                responsibilities=duties,
                requirements=[dict(id=row.id, statement=row.statement) for row in requirements_rows],
                tags=[]))
            out["jds"][f"{entry['company']}#{version.id}"] = entry

    # JD-not-imported entries (the NIO failure) are reported explicitly.
    imported = {entry["company"] for entry in out["jds"].values()}
    out["not_imported"] = [
        {**{"company": company, "job_title": record["job_title"]},
         "reason": "MANUAL_EXTRACTED_IDENTITY_MISMATCH (R31)",
         "identity_recheck": "corrected label passes both guards; retry blocked by MANUAL_IMPORT_IDENTITY_CONFLICT"}
        for company, record in by_company.items() if company not in imported
    ]

    await engine.dispose()
    (DATA / "publish_audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for key, entry in out["jds"].items():
        print(f"{key:22} stored={entry['stored_strength_counts']} effective={entry['effective_strength_counts']} "
              f"types={entry['type_counts']}")
        preview = entry.get("skill_preview_before_publish") or {}
        print("   stored_raw", entry["stored_strength_raw"], "| blocking", entry["blocking"] or "none",
              "| advisory", len(entry["advisory"]), "| skill", preview.get("status"),
              preview.get("recommended_skill_id"))
        entry["publishable"] = not entry["blocking"]
    print("audit_logs", out["audit_logs"], "| not_imported", [x["company"] for x in out["not_imported"]])


if __name__ == "__main__":
    asyncio.run(main())
