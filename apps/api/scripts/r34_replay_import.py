"""R31: zero-HTTP legal-import rehearsal for the three target real JDs.

Runs the real `PublicDataResearchAgent` state machine, the real Evidence
validation and the real review/publication service. The only substitution is the
model response, served by `ReplayModelProvider` from a rule-derived fixture that
cites exact spans of the stored snapshot. No network, no model, no production
write.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.core.config import settings
from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, EXTRACT_ROUTE, QUALITY_ROUTE
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.public_career.manual_import import ManualDocument, ManualImportService
from interview_api.modules.public_career.models import (
    Claim, ClaimEvidence, Company, Job, JobDescriptionVersion, JobRequirement,
    PublicDataRefreshTask, Source, SourceSnapshot,
)
from interview_api.modules.public_career.refresh_service import RefreshTaskStatus
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.research.evidence import EvidenceCatalog
from interview_api.modules.public_career.research.schemas import JobJDExtraction, ResearchQuality
from interview_api.modules.public_career.review import PublicCareerReviewService
from interview_api.modules.users.models import User

REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "data" / "r34_target_jd" / "manifest.json"

HEADING_LINES = {"职位描述", "职位要求", "工作职责", "任职资格", "岗位职责", "任职要求", "加分项", "你将负责", "我们希望你具备"}
EDUCATION_HINTS = ("学历", "专业", "毕业生", "博士", "硕士", "本科")


def norm(text: str) -> str:
    return re.sub(r"[\s\u3000]", "", text or "")


def classify(line: str) -> str:
    return "education" if any(hint in line for hint in EDUCATION_HINTS) else "skill"


def spans_covering(catalog: EvidenceCatalog, start: int, end: int) -> list[str]:
    """Evidence IDs whose union covers [start, end) with no gap."""
    spans = sorted(catalog.spans.values(), key=lambda s: s.start)
    hits = [s for s in spans if s.start < end and s.end > start]
    if not hits:
        raise SystemExit(f"no evidence span covers {start}-{end}")
    if hits[0].start > start or hits[-1].end < end:
        raise SystemExit(f"evidence does not cover {start}-{end}")
    for left, right in zip(hits, hits[1:]):
        if left.end != right.start:
            raise SystemExit(f"evidence gap between {left.end} and {right.start}")
    return [s.evidence_id for s in hits]


def literal_range(text: str, statement: str) -> tuple[int, int]:
    """Exact first; otherwise whitespace-insensitive (same rule as validate_identity)."""
    start = text.find(statement)
    if start >= 0:
        return start, start + len(statement)
    normalized, origin = [], []
    for index, char in enumerate(text):
        if not char.isspace() and char != "\u3000":
            normalized.append(char)
            origin.append(index)
    target = norm(statement)
    position = "".join(normalized).find(target)
    if position < 0:
        raise SystemExit(f"statement not literal in snapshot: {statement[:40]}")
    return origin[position], origin[position + len(target) - 1] + 1


def build_fixture(catalog: EvidenceCatalog, text: str, target: dict) -> tuple[dict, dict]:
    """Rule-derived REPLAY fixture: literal lines only, every one citing real spans."""
    company, title = target["company"], target["job_title"]
    identity_ids: list[str] = []
    for value in (company, title):
        start, end = literal_range(text, value)
        identity_ids += spans_covering(catalog, start, end)
    identity_ids = sorted(set(identity_ids))
    jd_ids = sorted(catalog.spans)

    sections = target["sections"]
    requirements = []
    for importance, lines in (("REQUIRED", sections["requirements"]), ("PREFERRED", sections["bonuses"])):
        for line in lines:
            statement = line.strip()
            if not statement or statement in HEADING_LINES:
                continue
            start, end = literal_range(text, statement)
            requirements.append({"statement": statement,
                                 "evidence_ids": spans_covering(catalog, start, end),
                                 "requirement_type": classify(statement),
                                 "importance": importance})
    if not requirements:
        raise SystemExit("no requirement lines extracted")

    attributes = []
    for line in sections["responsibilities"]:
        statement = line.strip()
        if not statement or statement in HEADING_LINES:
            continue
        start, end = literal_range(text, statement)
        attributes.append({"field": "RESPONSIBILITY", "statement": statement,
                           "evidence_ids": spans_covering(catalog, start, end)})
    location = next((line.strip() for line in text.splitlines()
                     if re.fullmatch(r"[^0-9A-Za-z]{0,4}(省|市)[^\n]*", line.strip()) and len(line.strip()) < 60), None)
    if location:
        start, end = literal_range(text, location)
        attributes.append({"field": "LOCATION", "statement": location,
                           "evidence_ids": spans_covering(catalog, start, end)})

    extraction = {"company_name": company, "job_title": title,
                  "identity_evidence_ids": identity_ids, "jd_evidence_ids": jd_ids,
                  "requirements": requirements, "attributes": attributes}
    JobJDExtraction.model_validate(extraction)  # fail fast on schema drift
    quality = {"suitable": True, "risk_flags": []}
    return extraction, quality


def replay_router(extraction: dict, quality: dict) -> ModelRouter:
    return ModelRouter({EXTRACT_ROUTE: ReplayModelProvider([extraction]),
                        QUALITY_ROUTE: ReplayModelProvider([quality])})


async def snapshot_of(factory, snapshot_id: int) -> SourceSnapshot:
    async with factory() as db:
        return await db.get(SourceSnapshot, snapshot_id)


def catalog_for(snapshot: SourceSnapshot) -> EvidenceCatalog:
    return EvidenceCatalog([snapshot], max_chars=settings.public_research_max_evidence_chars)


async def admin_user(factory) -> int:
    async with factory() as db:
        user = await db.scalar(select(User).where(User.email == "r31-reviewer@local.invalid"))
        if user is None:
            user = User(email="r31-reviewer@local.invalid", username="r31-reviewer",
                        password_hash="!replay-no-login", role="ADMIN", is_active=True)
            db.add(user)
            await db.commit()
        return user.id


async def assertions_evidence(factory, jd_id: int) -> list[dict]:
    """Every requirement must resolve to ClaimEvidence that matches the snapshot bytes."""
    out = []
    async with factory() as db:
        requirements = list((await db.scalars(select(JobRequirement).where(
            JobRequirement.job_description_version_id == jd_id).order_by(JobRequirement.id))).all())
        for requirement in requirements:
            claim = await db.get(Claim, requirement.claim_id) if requirement.claim_id else None
            evidence = list((await db.scalars(select(ClaimEvidence).where(
                ClaimEvidence.claim_id == requirement.claim_id))).all()) if claim else []
            exact = True
            joined = ""
            for item in evidence:
                snapshot = await db.get(SourceSnapshot, item.source_snapshot_id)
                location = item.source_location_json or {}
                start, end = location.get("start_char"), location.get("end_char")
                if (snapshot is None or start is None or end is None
                        or snapshot.content_text[start:end] != item.excerpt
                        or sha256(snapshot.content_text.encode()).hexdigest() != snapshot.content_hash):
                    exact = False
                joined += item.excerpt
            out.append({"requirement_id": requirement.id, "importance": requirement.importance,
                        "type": requirement.requirement_type,
                        "statement": requirement.statement[:40],
                        "claim": bool(claim), "evidence_rows": len(evidence),
                        "offset_exact": exact,
                        "literal_in_evidence": norm(requirement.statement) in norm(joined)})
    return out


async def targets_projection(factory) -> list[dict]:
    """Same query as GET /interviews/targets."""
    from interview_api.modules.interview.skill_recommendation import recommend_jobs
    async with factory() as db:
        rows = (await db.execute(select(Company, Job).join(Job, Job.company_id == Company.id)
            .where(Company.status == "ACTIVE", Job.status == "ACTIVE",
                   Job.id.in_(select(JobDescriptionVersion.job_id).where(
                       JobDescriptionVersion.publication_status == "PUBLISHED")))
            .order_by(Company.canonical_name, Job.canonical_title))).all()
        recommendations = await recommend_jobs(db, [job.id for _, job in rows])
        return [{"company": company.canonical_name, "job": job.canonical_title, "job_id": job.id,
                 "skill": (recommendations.get(job.id) or {}).get("recommended_skill_id"),
                 "status": (recommendations.get(job.id) or {}).get("status"),
                 "reason": (recommendations.get(job.id) or {}).get("reason"),
                 "ranking_points": (recommendations.get(job.id) or {}).get("ranking_points")}
                for company, job in rows]


async def entity_counts(factory) -> dict:
    async with factory() as db:
        return {
            "companies": await db.scalar(select(func.count()).select_from(Company)),
            "jobs": await db.scalar(select(func.count()).select_from(Job)),
            "jd_versions": await db.scalar(select(func.count()).select_from(JobDescriptionVersion)),
            "published": await db.scalar(select(func.count()).select_from(JobDescriptionVersion).where(
                JobDescriptionVersion.publication_status == "PUBLISHED")),
            "requirements": await db.scalar(select(func.count()).select_from(JobRequirement)),
            "claims": await db.scalar(select(func.count()).select_from(Claim)),
            "claim_evidence": await db.scalar(select(func.count()).select_from(ClaimEvidence)),
            "tasks": await db.scalar(select(func.count()).select_from(PublicDataRefreshTask)),
            "audit_logs": await db.scalar(select(func.count()).select_from(AuditLog)),
        }


async def run_agent(factory, task_id: int, extraction: dict, quality: dict, *, force: bool = False):
    agent = PublicDataResearchAgent(factory, replay_router(extraction, quality))
    return await agent.run(task_id, force=force)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--empty-official-domains", action="store_true", default=True)
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    targets = {record["jd_key"]: record for record in manifest["records"]}
    order = ["KYLINOS_AI_002", "NIO_AIA_025", "MOONTON_AGENT_005"]

    settings.public_research_official_domains = {}
    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{args.database}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ManualImportService(factory)
    report: dict = {"database": args.database, "model_http_calls": 0,
                    "official_domains_configured": settings.public_research_official_domains,
                    "started_at": datetime.now(timezone.utc).isoformat(), "jds": {}}

    reviewer_id = await admin_user(factory)
    report["reviewer"] = {"user_id": reviewer_id, "role": "ADMIN", "note": "sandbox-only account, no login password"}

    prepared_map: dict[str, dict] = {}
    for key in order:
        target = targets[key]
        document = ManualDocument.from_file(
            file=REPO / target["text_path"], source_url=target["source_url"],
            source_type="official", document_type="job_jd",
            company=target["company"], job_title=target["job_title"])
        prepared = await service.prepare(document)
        if prepared["status"] != "READY_FOR_EXTRACTION":
            raise SystemExit(f"REPLAY_DB_NOT_FRESH: {key} task {prepared['refresh_task_id']} "
                             f"is {prepared['status']}; use a fresh rehearsal database")
        prepared_map[key] = prepared
        snapshot = await snapshot_of(factory, prepared["source_snapshot_id"])
        catalog = catalog_for(snapshot)
        extraction, quality = build_fixture(catalog, snapshot.content_text or "", target)
        entry = {"prepared": {k: prepared[k] for k in ("refresh_task_id", "source_id", "source_snapshot_id",
                                                       "status", "reused", "content_sha256")},
                 "snapshot_chars": len(snapshot.content_text or ""),
                 "evidence_spans": len(catalog.spans),
                 "fixture": {"requirements": len(extraction["requirements"]),
                             "required": sum(1 for r in extraction["requirements"] if r["importance"] == "REQUIRED"),
                             "preferred": sum(1 for r in extraction["requirements"] if r["importance"] == "PREFERRED"),
                             "attributes": len(extraction["attributes"])}}

        if key == "KYLINOS_AI_002":
            # Failure injection: break the publisher mid-transaction, after the
            # company/job insert, to prove no partial published entity survives.
            original_claim = PublicDataResearchAgent._claim

            async def exploding_claim(self, *a, **kw):
                raise RuntimeError("R31_INJECTED_CLAIM_FAILURE")

            PublicDataResearchAgent._claim = exploding_claim
            try:
                failed = await run_agent(factory, prepared["refresh_task_id"], extraction, quality)
            finally:
                PublicDataResearchAgent._claim = original_claim
            async with factory() as db:
                task = await db.get(PublicDataRefreshTask, prepared["refresh_task_id"])
                status_after_failure = task.status
                error_after_failure = (task.error_message or "")[:120]
            entry["failure_injection"] = {
                "result_status": failed.get("status"),
                "task_status": status_after_failure,
                "error": error_after_failure,
                "companies_after_failure": (await entity_counts(factory))["companies"],
                "jd_versions_after_failure": (await entity_counts(factory))["jd_versions"],
                "published_after_failure": (await entity_counts(factory))["published"],
            }
            # Retry the same task (manual + FAILED allows explicit rerun).
            reprepared = await service.prepare(document)
            entry["retry_prepare"] = {"refresh_task_id": reprepared["refresh_task_id"],
                                      "reused": reprepared["reused"], "status": reprepared["status"]}
            result = await run_agent(factory, prepared["refresh_task_id"], extraction, quality, force=True)
        else:
            result = await run_agent(factory, prepared["refresh_task_id"], extraction, quality)

        entry["agent_result"] = {k: result.get(k) for k in (
            "status", "company_id", "job_id", "jd_version_id", "publication_status",
            "manual_publication_decision", "manual_source_type", "error")}
        report["jds"][key] = entry

    # ---------- Publication (Owner boundary, zero model) ----------
    for key in order:
        jd_id = report["jds"][key]["agent_result"]["jd_version_id"]
        async with factory() as db:
            published = await PublicCareerReviewService(db).publish_jd(
                jd_id, reviewer_id=reviewer_id,
                reason="R31 rehearsal: official page captured, evidence offsets verified, requirements literal")
            await db.commit()
        report["jds"][key]["publish"] = published

    # ---------- Evidence assertions ----------
    for key in order:
        jd_id = report["jds"][key]["agent_result"]["jd_version_id"]
        rows = await assertions_evidence(factory, jd_id)
        report["jds"][key]["evidence"] = {
            "total": len(rows), "offset_exact": sum(1 for r in rows if r["offset_exact"]),
            "literal": sum(1 for r in rows if r["literal_in_evidence"]),
            "without_claim": sum(1 for r in rows if not r["claim"]),
            "samples": rows[:3],
        }

    # ---------- Idempotency ----------
    key = "NIO_AIA_025"
    target = targets[key]
    document = ManualDocument.from_file(file=REPO / target["text_path"], source_url=target["source_url"],
                                        source_type="official", document_type="job_jd",
                                        company=target["company"], job_title=target["job_title"])
    again = await service.prepare(document)
    rerun_error = None
    try:
        await run_agent(factory, again["refresh_task_id"], {"company_name": "x"}, {"suitable": True, "risk_flags": []})
    except Exception as exc:  # expected: task already COMPLETED
        rerun_error = type(exc).__name__ + ": " + str(exc)[:80]
    async with factory() as db:
        republish = await PublicCareerReviewService(db).publish_jd(
            report["jds"][key]["agent_result"]["jd_version_id"], reviewer_id=reviewer_id, reason="repeat")
        await db.commit()
    report["idempotency"] = {"prepare_reused": again["reused"],
                             "same_task_id": again["refresh_task_id"] == report["jds"][key]["prepared"]["refresh_task_id"],
                             "agent_rerun_refused": rerun_error,
                             "republish_reused": republish.get("reused")}

    # ---------- Isolation ----------
    async with factory() as db:
        rows = (await db.execute(select(Company.canonical_name, Job.canonical_title, Job.id,
                                        JobDescriptionVersion.version_number, JobDescriptionVersion.publication_status)
                .join(Job, Job.company_id == Company.id)
                .join(JobDescriptionVersion, JobDescriptionVersion.job_id == Job.id))).all()
    report["isolation"] = [{"company": r[0], "job": r[1], "job_id": r[2], "version": r[3], "status": r[4]} for r in rows]

    # ---------- Frontend selection + skill ----------
    report["targets_before_publish"] = None
    report["targets_after_publish"] = await targets_projection(factory)
    report["counts"] = await entity_counts(factory)
    await engine.dispose()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    for key in order:
        entry = report["jds"][key]
        print(f"{key:20} status={entry['agent_result']['status']:10} "
              f"jd={entry['agent_result']['jd_version_id']} pub={entry['agent_result']['publication_status']} "
              f"evidence={entry['evidence']['offset_exact']}/{entry['evidence']['total']}")
    print("counts", json.dumps(report["counts"], ensure_ascii=False))
    print("targets", json.dumps(report["targets_after_publish"], ensure_ascii=False))
    print("idempotency", json.dumps(report["idempotency"], ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"FATAL {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
