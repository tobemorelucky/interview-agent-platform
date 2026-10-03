"""Only snapshot creation reads mutable public data / current candidate profile."""
from copy import deepcopy
from sqlalchemy import select, or_

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.modules.candidate_profile.service import CandidateProfileService
from interview_api.modules.candidate_profile.models import CandidateProfileVersion, CandidateFact
from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion, JobRequirement
from interview_api.modules.public_career.fingerprints import compute_content_fingerprint, compute_question_fingerprint
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.experience.models import InterviewQuestion, InterviewExperience
from interview_api.modules.memory.snapshot import build_memory_snapshot


APPROVED = ("APPROVED", "AUTO_APPROVED")


async def build_context(db, user_id, request):
    service = CandidateProfileService(db)
    version = (await db.get(CandidateProfileVersion, request.candidate_profile_version_id)
               if request.candidate_profile_version_id else await service.current(user_id))
    if not version:
        raise AppError("PROFILE_REQUIRED", "请先完成候选人画像", 409)
    profile = await service.serialize(user_id, version)
    company, job = await db.get(Company, request.company_id), await db.get(Job, request.job_id)
    if not company or not job or job.company_id != company.id or job.status != "ACTIVE" or company.status != "ACTIVE":
        raise AppError("INVALID_COMPANY_JOB", "岗位必须属于所选公司且可用", 422)
    query = select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job.id,
        JobDescriptionVersion.publication_status == "PUBLISHED")
    if request.job_description_version_id:
        query = query.where(JobDescriptionVersion.id == request.job_description_version_id)
    jd = await db.scalar(query.order_by(JobDescriptionVersion.version_number.desc()).limit(1))
    if not jd:
        raise AppError("PUBLISHED_JD_REQUIRED", "所选岗位缺少已发布 JD；本流程不会触发 Research", 409)
    requirements = (await db.scalars(select(JobRequirement).where(JobRequirement.job_description_version_id == jd.id).order_by(JobRequirement.id)
        .with_for_update(read=True, key_share=True))).all()
    if not requirements:
        raise AppError("REQUIREMENTS_REQUIRED", "所选 JD 缺少岗位要求", 409)
    # Exact public FKs only. A company-level record with unknown Job is permitted;
    # records assigned to another Job/company are never guessed into this pool.
    def scope(table):
        return or_(table.job_id == job.id, (table.job_id.is_(None) & (table.company_id == company.id))) & or_(table.company_id.is_(None), table.company_id == company.id)
    experiences = (await db.scalars(select(InterviewExperience).where(scope(InterviewExperience),
        InterviewExperience.review_status.in_(APPROVED)).order_by(InterviewExperience.id).limit(30)
        .with_for_update(read=True, key_share=True))).all()
    exp_ids = {e.id for e in experiences}
    questions = (await db.scalars(select(InterviewQuestion).where(scope(InterviewQuestion),
        InterviewQuestion.review_status.in_(APPROVED), InterviewQuestion.should_index.is_(True),
        or_(InterviewQuestion.experience_id.is_(None), InterviewQuestion.experience_id.in_(exp_ids)))
        .order_by(InterviewQuestion.id).limit(100).with_for_update(read=True, key_share=True))).all()
    facts = (await db.scalars(select(CandidateFact).where(CandidateFact.version_id == version.id).order_by(CandidateFact.id))).all()
    context = dict(
        candidate_profile_version_id=version.id, company_id=company.id, job_id=job.id,
        job_description_version_id=jd.id,
        company_name=company.canonical_name, job_title=job.canonical_title,
        profile=deepcopy(profile["snapshot"]), normalized=profile["normalized"],
        candidate_facts=[dict(id=f.id, field_path=f.field_path, statement=f.statement,
                              evidence_ids=_fact_keys(profile["snapshot"], f.field_path)) for f in facts],
        candidate_evidence=profile["evidence"],
        profile_version_number=version.version_number, jd_version_number=jd.version_number,
        profile_producer=version.producer,
        jd_fingerprint=jd.content_fingerprint,
        requirements=[dict(id=r.id, statement=r.statement, importance=r.importance,
                           fingerprint=compute_content_fingerprint(r.statement)) for r in requirements],
        public_questions=[dict(id=q.id, question=q.question, expected_points=(q.standard_answer or q.original_answer or "").splitlines(),
                               dimension=q.category or "综合", difficulty=q.difficulty,
                               fingerprint=compute_question_fingerprint(q.question), experience_id=q.experience_id) for q in questions],
        interview_experiences=[dict(id=e.id, summary=(e.summary or "")[:2000],
                                   fingerprint=compute_content_fingerprint(e.content_text or e.summary or "")) for e in experiences],
        coverage_insufficient=len(questions) < request.question_count,
        budgets=dict(question_count=request.question_count, max_follow_ups=settings.interview_max_follow_ups_per_question,
                     max_rounds=settings.interview_max_rounds, max_duration_seconds=settings.interview_max_duration_seconds,
                     max_model_tokens=settings.interview_max_model_tokens),
    )
    # M5: read once at creation, as two explicitly different provenance layers.
    # Agents continue reading only this immutable Snapshot on every round.
    context.update(await build_memory_snapshot(db, user_id))
    return context, version.source_resume_id, digest(context)


def _fact_keys(snapshot, path):
    parts = path.split(".")
    item = snapshot[parts[0]] if len(parts) == 1 else snapshot[parts[0]][int(parts[1])]
    return item["evidence_ids"]


def question_policy(context, questions, action="NEXT"):
    if action in ("FOLLOW_UP", "DEEP_DIVE"):
        return dict(source_type="FOLLOW_UP" if action == "FOLLOW_UP" else "DYNAMIC_GENERATED", source_id=None)
    used_public = {q.source_question_id for q in questions}
    public = next((q for q in context["public_questions"] if q["id"] not in used_public), None)
    if public:
        return dict(source_type="PUBLIC_QUESTION", source_id=public["id"])
    used_requirements = {q.job_requirement_id for q in questions}
    requirement = next((r for r in context["requirements"] if r["id"] not in used_requirements), None)
    if requirement:
        return dict(source_type="JOB_REQUIREMENT", source_id=requirement["id"])
    used_facts = {key for q in questions for key in (q.candidate_refs_json or {}).get("fact_ids", [])}
    ordered_facts = sorted(context["candidate_facts"], key=lambda f: (0 if f["field_path"].startswith("projects.") else 1, f["id"]))
    fact = next((f for f in ordered_facts if f["id"] not in used_facts and
                 f["field_path"].split(".")[0] in ("projects", "skills", "work_experience", "internship_experience")), None)
    return dict(source_type="CANDIDATE_PROFILE", source_id=fact["id"]) if fact else dict(source_type="DYNAMIC_GENERATED", source_id=None)


def validate_question(artifact, context, policy):
    if artifact.source_type != policy["source_type"]:
        raise AppError("QUESTION_SOURCE_POLICY", "模型未遵循题目来源策略", 422)
    public = {q["id"]: q for q in context["public_questions"]}
    requirements = {r["id"]: r for r in context["requirements"]}
    facts = {f["id"]: f for f in context["candidate_facts"]}
    evidence = {e["key"] for e in context["candidate_evidence"]}
    if artifact.source_question_id is not None and artifact.source_question_id not in public:
        raise AppError("UNKNOWN_PUBLIC_QUESTION", "未知公共题目引用", 422)
    if artifact.source_question_id is not None and artifact.source_type != "PUBLIC_QUESTION":
        raise AppError("PUBLIC_SOURCE_MISMATCH", "非公共原题不能冒用公共题目来源", 422)
    if artifact.job_requirement_id is not None and artifact.job_requirement_id not in requirements:
        raise AppError("UNKNOWN_REQUIREMENT", "未知岗位要求引用", 422)
    if set(artifact.candidate_fact_ids) - facts.keys() or set(artifact.candidate_evidence_ids) - evidence:
        raise AppError("UNKNOWN_CANDIDATE_EVIDENCE", "未知候选人事实或证据引用", 422)
    for fact_id in artifact.candidate_fact_ids:
        if not set(facts[fact_id]["evidence_ids"]) & set(artifact.candidate_evidence_ids):
            raise AppError("UNSUPPORTED_CANDIDATE_QUESTION", "候选人问题未绑定对应事实证据", 422)
    if artifact.source_type == "PUBLIC_QUESTION":
        if artifact.source_question_id != policy["source_id"] or artifact.question != public[policy["source_id"]]["question"]:
            raise AppError("PUBLIC_QUESTION_CHANGED", "公共题目必须使用快照原题", 422)
    elif artifact.source_type == "JOB_REQUIREMENT" and artifact.job_requirement_id != policy["source_id"]:
        raise AppError("REQUIREMENT_REQUIRED", "缺少本题岗位要求引用", 422)
    elif artifact.source_type == "CANDIDATE_PROFILE" and (policy["source_id"] not in artifact.candidate_fact_ids or not artifact.candidate_evidence_ids):
        raise AppError("CANDIDATE_EVIDENCE_REQUIRED", "缺少本题候选人证据", 422)
