"""R132 acceptance: deterministically link a reviewed experience to an exact job, then approve."""
import argparse, asyncio, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
if not os.environ.get("R132_DATABASE_URL"):
    raise SystemExit("set R132_DATABASE_URL to the isolated acceptance database")
os.environ["DATABASE_URL"] = os.environ["R132_DATABASE_URL"]

from sqlalchemy import select, update
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.experience.models import InterviewExperience, InterviewQuestion
from interview_api.modules.experience.review_service import ExperienceReviewService
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.service import link_interview_experience


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--experience", type=int, required=True)
    p.add_argument("--reviewer", type=int, required=True)
    p.add_argument("--approve", action="store_true")
    args = p.parse_args()
    async with async_session_factory() as db:
        experience = await db.get(InterviewExperience, args.experience)
        if not experience:
            raise SystemExit("experience not found")
        agent = PublicDataResearchAgent(async_session_factory)
        company_id, job_id = await agent._match_company_job(db, experience.company, experience.position)
        if job_id is None:
            print(json.dumps(dict(linked=False, company_id=company_id, job_id=None,
                position=experience.position, reason="NO_UNAMBIGUOUS_EXACT_OR_BASE_TITLE_MATCH"),
                ensure_ascii=False))
            raise SystemExit(0)
        await link_interview_experience(db, experience, company_id=company_id, job_id=job_id)
        await db.execute(update(InterviewQuestion).where(InterviewQuestion.experience_id == experience.id)
                         .values(company_id=company_id, job_id=job_id))
        experience.review_note = "R132 reviewer confirmed exact company/job from source text"
        db.add(AuditLog(actor_user_id=args.reviewer, actor_role="ADMIN",
            action="experience.review.confirm_job_link", resource_type="InterviewExperience",
            resource_id=str(experience.id),
            metadata_json=dict(company_id=company_id, job_id=job_id, position=experience.position)))
        await db.commit()
        print(json.dumps(dict(linked=True, experience_id=experience.id, company_id=company_id, job_id=job_id),
                         ensure_ascii=False))
    if args.approve:
        async with async_session_factory() as db:
            result = await ExperienceReviewService(db).approve(args.experience, args.reviewer)
            print(json.dumps(dict(approved=True, status=result["item"]["review_status"]), ensure_ascii=False))

asyncio.run(main())
