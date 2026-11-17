"""Read-only public projections and deduplicated, non-executing source requests."""
from hashlib import sha256
import json
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from interview_api.core.exceptions import AppError
from interview_api.modules.experience.models import InterviewExperience, InterviewQuestion
from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion, JobRequirement, Source, SourceSnapshot, PublicDataRefreshTask
from interview_api.core.url_safety import canonicalize_public_url, URLSafetyError

APPROVED = ("APPROVED", "AUTO_APPROVED")

# User-facing direction filters for the public job search. These are literal
# keyword sets over title/body, not inferred job families: curated JDs carry no
# structured family field, and guessing one would make the filter unverifiable.
DIRECTIONS = {
    "ai_agent": ("AI", "Agent", "大模型", "LLM", "RAG", "机器学习", "深度学习", "算法"),
    "java": ("Java", "后端", "服务端", "Spring"),
    "python": ("Python",),
    "frontend": ("前端", "Vue", "React", "Web"),
    "test": ("测试", "QA"),
    "data": ("数据", "大数据", "数仓"),
    "embedded": ("嵌入式", "操作系统", "内核", "C++"),
}


def published_jobs():
    latest = select(func.max(JobDescriptionVersion.version_number)).where(
        JobDescriptionVersion.job_id == Job.id,
        JobDescriptionVersion.publication_status == "PUBLISHED").correlate(Job).scalar_subquery()
    return select(Company, Job, JobDescriptionVersion, Source).join(Job, Job.company_id == Company.id).join(
        JobDescriptionVersion, JobDescriptionVersion.job_id == Job.id).outerjoin(
        SourceSnapshot, JobDescriptionVersion.source_snapshot_id == SourceSnapshot.id).outerjoin(
        Source, SourceSnapshot.source_id == Source.id).where(Company.status == "ACTIVE", Job.status == "ACTIVE",
        JobDescriptionVersion.publication_status == "PUBLISHED", JobDescriptionVersion.version_number == latest)


def job_projection(company, job, jd, source):
    declared = (jd.metadata_json or {}).get('web_source') or (jd.metadata_json or {}).get('manual_source') or {}
    return dict(company_id=company.id, company=company.canonical_name, job_id=job.id,
        title=job.canonical_title, jd_version_id=jd.id, version=jd.version_number,
        source_url=source.canonical_url if source else None, source_type=declared.get('source_type') or (source.source_type if source else None),
        updated_at=jd.observed_at, published_at=jd.published_at)


DUTY_HEADINGS = ("岗位职责", "工作职责", "职位描述", "岗位描述", "职责描述", "工作内容")
SKILL_HEADINGS = ("任职要求", "任职资格", "职位要求", "岗位要求", "技能要求", "任职条件", "我们希望你")


def split_jd_sections(text):
    """Keep the source's own duty/qualification split; never invent a heading."""
    buckets = {"responsibilities": [], "qualifications": []}
    current, has_sections = None, False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        bare = line.strip("【】[]#：: ")
        if any(bare.startswith(head) for head in DUTY_HEADINGS):
            current, has_sections = "responsibilities", True
            continue
        if any(bare.startswith(head) for head in SKILL_HEADINGS):
            current, has_sections = "qualifications", True
            continue
        if current:
            buckets[current].append(line)
    return dict(responsibilities=buckets["responsibilities"] if has_sections else [],
                qualifications=buckets["qualifications"] if has_sections else [],
                has_sections=has_sections)


class PublicCareerLibrary:
    def __init__(self, db):
        self.db = db

    async def jobs(self, limit=20, offset=0):
        rows = (await self.db.execute(published_jobs().order_by(Job.id).limit(limit).offset(offset))).all()
        return [job_projection(*row) for row in rows]

    async def search(self, limit=20, offset=0, q=None, company_id=None, direction=None):
        """Search every published job, not just the current page.

        SQL LIKE over company/title/body is deliberate: the library is small and
        bounded, and this keeps the result explainable (no semantic guess).
        """
        from sqlalchemy import or_
        query = published_jobs()
        conditions = []
        text = (q or "").strip()
        if text:
            like = f"%{text}%"
            conditions.append(or_(Company.canonical_name.ilike(like), Job.canonical_title.ilike(like),
                                  JobDescriptionVersion.raw_text.ilike(like)))
        if company_id:
            conditions.append(Company.id == company_id)
        keywords = DIRECTIONS.get(direction or "", ())
        if keywords:
            conditions.append(or_(*[or_(Job.canonical_title.ilike(f"%{word}%"),
                                         JobDescriptionVersion.raw_text.ilike(f"%{word}%"))
                                    for word in keywords]))
        if conditions:
            query = query.where(*conditions)
        total = (await self.db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        rows = (await self.db.execute(query.order_by(Company.canonical_name, Job.id)
                                      .limit(limit).offset(offset))).all()
        return dict(items=[job_projection(*row) for row in rows], total=total, limit=limit, offset=offset)

    async def companies(self):
        rows = (await self.db.execute(published_jobs().with_only_columns(
            Company.id, Company.canonical_name).order_by(Company.canonical_name))).all()
        counts = {}
        for company_id, name in rows:
            entry = counts.setdefault(company_id, dict(company_id=company_id, name=name, job_count=0))
            entry["job_count"] += 1
        return list(counts.values())

    async def job(self, job_id):
        row = (await self.db.execute(published_jobs().where(Job.id == job_id))).first()
        if row is None:
            raise AppError("PUBLIC_JOB_NOT_FOUND", "岗位未发布或当前不可用", 404)
        result = job_projection(*row)
        jd = row[2]
        requirements = (await self.db.scalars(select(JobRequirement).where(
            JobRequirement.job_description_version_id == jd.id).order_by(JobRequirement.id))).all()
        # The full acquired page is an admin/evidence artifact, not a public
        # republication endpoint. Users follow the canonical source for context.
        from interview_api.modules.public_career.requirement_vocabulary import describe
        # Public projection reports the strength consumers actually use, so a
        # compound sentence ending in 优先 is not shown as wholly optional.
        result.update(description=jd.raw_text[:500], description_truncated=len(jd.raw_text) > 500, requirements=[dict(id=r.id, text=r.statement,
            type=describe(r.statement, r.requirement_type, r.importance)["type"],
            importance=describe(r.statement, r.requirement_type, r.importance)["effective_strength"]) for r in requirements])
        # Curated JDs keep the source's own 【岗位职责】/【任职要求】 headings; show
        # them separately instead of one undifferentiated blob.
        sections = split_jd_sections(jd.raw_text)
        result.update(responsibilities=sections["responsibilities"],
                      qualifications=sections["qualifications"],
                      has_sections=sections["has_sections"])
        return result

    async def experiences(self, limit=20, offset=0, experience_id=None):
        query = select(InterviewExperience, Source, Company.canonical_name, Job.canonical_title).outerjoin(Company,
            InterviewExperience.company_id == Company.id).outerjoin(Job, InterviewExperience.job_id == Job.id).outerjoin(SourceSnapshot,
            InterviewExperience.source_snapshot_id == SourceSnapshot.id).outerjoin(Source,
            SourceSnapshot.source_id == Source.id).where(InterviewExperience.review_status.in_(APPROVED))
        if experience_id is not None:
            query = query.where(InterviewExperience.id == experience_id)
        rows = (await self.db.execute(query.order_by(InterviewExperience.id.desc()).limit(limit).offset(offset))).all()
        result = [dict(id=e.id, company_id=e.company_id, job_id=e.job_id, company=e.company or company,
            title=e.position or title, summary=e.summary, source_url=e.source_url or (s.canonical_url if s else None), updated_at=e.updated_at,
            source_type=((e.reliability_json or {}).get('web_source') or (e.reliability_json or {}).get('manual_source') or {}).get('source_type') or (s.source_type if s else None), interview_round=e.interview_round) for e, s, company, title in rows]
        if experience_id is not None:
            if not result:
                raise AppError("PUBLIC_EXPERIENCE_NOT_FOUND", "面经未发布或已撤回", 404)
            questions = (await self.db.scalars(select(InterviewQuestion).where(
                InterviewQuestion.experience_id == experience_id, InterviewQuestion.review_status.in_(APPROVED),
                InterviewQuestion.should_index.is_(True)).order_by(InterviewQuestion.id))).all()
            result[0]["questions"] = [dict(question=q.question, category=q.category) for q in questions]
            return result[0]
        return result

    async def request_source(self, url, document_type):
        try:
            canonical = canonicalize_public_url(url)
        except URLSafetyError as exc:
            raise AppError("SOURCE_URL_INVALID", "只允许不含密钥的公共 HTTP/HTTPS 来源", 422) from exc
        key = "source-request:" + sha256((canonical + "\n" + document_type).encode()).hexdigest()
        await self.db.execute(insert(PublicDataRefreshTask).values(target_type="SOURCE_REQUEST",
            target_ref=None, reason=json.dumps(dict(source_url=canonical, document_type=document_type,
                acquisition_mode="USER_REQUEST_ONLY", execution="ADMIN_REQUIRED")), status="PENDING",
            idempotency_key=key).on_conflict_do_nothing(constraint="uq_public_data_refresh_tasks_idempotency_key"))
        task = await self.db.scalar(select(PublicDataRefreshTask).where(PublicDataRefreshTask.idempotency_key == key))
        await self.db.commit()
        return dict(task_id=task.id, status=task.status, source_url=canonical,
            message="来源申请已合并排队，需管理员核实和采集；未触发网络或模型调用")
