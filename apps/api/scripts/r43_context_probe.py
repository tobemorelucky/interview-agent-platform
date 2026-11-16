"""Round 34: prove Interview Context can read a published JD end to end.

`build_context()` resolves the candidate profile before it touches the JD, so a
published job cannot be exercised without a profile. Round 34 explicitly allows a
clearly-labelled synthetic candidate for this engineering test, so this script
creates one and nothing else:

  user      synthetic.r34@example.com        (NORMAL, marked in username)
  resume    filename SYNTHETIC_... .txt, raw_text states it is not a real person
  profile   producer = SYNTHETIC_ENGINEERING_FIXTURE_R34, no facts, no evidence

No model call, no Memory write, no real candidate data. The context is built and
then discarded; only a projection summary is kept.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

import interview_api.modules.models  # noqa: F401,E402
from interview_api.core.security import hash_password  # noqa: E402
from interview_api.modules.candidate_profile.models import CandidateProfile, CandidateProfileVersion  # noqa: E402
from interview_api.modules.interview.context import build_context  # noqa: E402
from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion  # noqa: E402
from interview_api.modules.resume.models import Resume  # noqa: E402
from interview_api.modules.users.models import User  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

SANDBOX = "interview_agent_wb_jd_r31_20261116"
EMAIL = "synthetic.r34@example.com"
OUT = REPO / "data/r40_batch2/context_probe.json"

SNAPSHOT = {
    "schema": "synthetic-fixture",
    "notice": "SYNTHETIC ENGINEERING FIXTURE — NOT A REAL CANDIDATE",
    "summary": "空的合成画像，仅用于验证 Interview Context 能读取已发布 JD 与要求。",
    "education": [], "work_experience": [], "projects": [], "skills": [],
}


def request_for(company_id: int, job_id: int, jd_id: int, question_count: int = 3):
    """The real request schema, so a missing field cannot hide behind a stub."""
    from interview_api.modules.interview.schemas import CreateSessionRequest
    return CreateSessionRequest(company_id=company_id, job_id=job_id,
                                job_description_version_id=jd_id,
                                question_count=question_count, interviewer_skill_id=None)


async def ensure_synthetic_candidate(db) -> dict:
    user = await db.scalar(select(User).where(User.email == EMAIL))
    created_user = False
    if user is None:
        user = User(email=EMAIL, username="synthetic_r34_fixture",
                    password_hash=hash_password("Synthetic-Fixture-R34"),
                    role="USER", is_active=True)
        db.add(user)
        await db.flush()
        created_user = True

    resume = await db.scalar(select(Resume).where(Resume.user_id == user.id,
                                                Resume.filename.like("SYNTHETIC_%")))
    created_resume = False
    if resume is None:
        resume = Resume(user_id=user.id, filename="SYNTHETIC_FIXTURE_NOT_A_REAL_RESUME.txt",
                        storage_key=f"synthetic/r34/{user.id}.txt", file_type="txt", file_size=0,
                        status="COMPLETED",
                        raw_text="SYNTHETIC ENGINEERING FIXTURE — NOT A REAL CANDIDATE. "
                                 "Contains no personal data and is used only to exercise "
                                 "Interview Context projection.")
        db.add(resume)
        await db.flush()
        created_resume = True

    profile = await db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    created_profile = False
    if profile is None:
        profile = CandidateProfile(user_id=user.id)
        db.add(profile)
        await db.flush()
        created_profile = True

    version = await db.scalar(select(CandidateProfileVersion).where(
        CandidateProfileVersion.candidate_profile_id == profile.id))
    created_version = False
    if version is None:
        version = CandidateProfileVersion(
            candidate_profile_id=profile.id, version_number=1, source_resume_id=resume.id,
            generation_key="synthetic-r34-fixture-v1", unknowns=["ALL_FIELDS_SYNTHETIC"],
            snapshot_json=SNAPSHOT, producer="SYNTHETIC_ENGINEERING_FIXTURE_R34",
            provider="none", model="none", prompt_version="none", summary=SNAPSHOT["summary"])
        db.add(version)
        await db.flush()
        profile.current_version_id = version.id
        created_version = True

    return {"user_id": user.id, "resume_id": resume.id, "profile_id": profile.id,
            "profile_version_id": version.id,
            "created": {"user": created_user, "resume": created_resume,
                        "profile": created_profile, "version": created_version}}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=SANDBOX)
    parser.add_argument("--job-id", type=int, default=5)
    args = parser.parse_args()

    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{args.database}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "model_http_calls": 0,
                    "synthetic_candidate": True, "not_a_real_candidate": True}
    try:
        async with factory() as db:
            async with db.begin():
                identities = await ensure_synthetic_candidate(db)
        report["fixture"] = identities

        async with factory() as db:
            job = await db.get(Job, args.job_id)
            if job is None:
                raise SystemExit(f"job {args.job_id} not found")
            jd = await db.scalar(select(JobDescriptionVersion).where(
                JobDescriptionVersion.job_id == job.id,
                JobDescriptionVersion.publication_status == "PUBLISHED"
            ).order_by(JobDescriptionVersion.version_number.desc()).limit(1))
            if jd is None:
                raise SystemExit(f"job {args.job_id} has no published JD")
            company = await db.get(Company, job.company_id)
            request = request_for(company.id, job.id, jd.id)
            try:
                context, source_resume_id, context_digest = await build_context(
                    db, identities["user_id"], request)
            except Exception as exc:  # recorded, not hidden
                report["build_context"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            else:
                report["build_context"] = {
                    "ok": True,
                    "company_name": context["company_name"], "job_title": context["job_title"],
                    "jd_version_number": context["jd_version_number"],
                    "jd_fingerprint_matches_row": context["jd_fingerprint"] == jd.content_fingerprint,
                    "requirements": len(context["requirements"]),
                    "requirement_strength": {
                        "REQUIRED": sum(1 for r in context["requirements"] if r["importance"] == "REQUIRED"),
                        "PREFERRED": sum(1 for r in context["requirements"] if r["importance"] == "PREFERRED"),
                        "UNKNOWN": sum(1 for r in context["requirements"] if r["importance"] == "UNKNOWN"),
                    },
                    "fingerprints_unique": len({r["fingerprint"] for r in context["requirements"]})
                    == len(context["requirements"]),
                    "first_requirement": context["requirements"][0]["statement"][:120] if context["requirements"] else None,
                    "public_questions": len(context["public_questions"]),
                    "budgets": context["budgets"],
                    "profile_is_synthetic": context["profile"].get("schema") == "synthetic-fixture",
                    "context_digest": context_digest[:16],
                    "source_resume_matches_fixture": source_resume_id == identities["resume_id"],
                }
            report["job"] = {"job_id": job.id, "company": company.canonical_name,
                             "title": job.canonical_title, "jd_id": jd.id,
                             "jd_version": jd.version_number, "status": jd.publication_status}
    finally:
        await engine.dispose()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    print("written", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
