"""Maintainer publication boundary for evidence-backed draft JD versions.

No model, acquisition or final-entity creation. Caller owns the transaction.
Published historical content is never rewritten to satisfy a matching run.
"""
from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import select

from interview_api.core.exceptions import AppError
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.public_career.fingerprints import normalize_text
from interview_api.modules.public_career.models import (
    Claim, ClaimEvidence, Company, Job, JobDescriptionVersion, JobRequirement, SourceSnapshot,
)
from interview_api.modules.users.models import User


class PublicCareerReviewService:
    def __init__(self, db):
        self.db = db

    async def publish_jd(self, jd_id, *, reviewer_id, reason, matching_constraints=None, requirement_topics=None):
        reviewer = await self.db.get(User, reviewer_id)
        if not reviewer or reviewer.role != "ADMIN" or not reviewer.is_active:
            raise AppError("ADMIN_REQUIRED", "只有管理员可以审核公共岗位", 403)
        if not reason or not reason.strip():
            raise AppError("REVIEW_REASON_REQUIRED", "请记录人工审核依据", 422)
        jd = await self.db.scalar(select(JobDescriptionVersion).where(JobDescriptionVersion.id == jd_id).with_for_update())
        if not jd:
            raise AppError("JD_NOT_FOUND", "JD版本不存在", 404)
        if jd.publication_status == "PUBLISHED":
            # Retry reuses the published version and its original audit.
            if matching_constraints is not None and matching_constraints != (jd.structured_data_json or {}).get("matching_constraints"):
                raise AppError("PUBLISHED_JD_IMMUTABLE", "已发布条件不能原地改写", 409)
            return dict(jd_version_id=jd.id, job_id=jd.job_id, publication_status="PUBLISHED", reused=True)
        if jd.publication_status not in {"WAITING_REVIEW", "APPROVED"}:
            raise AppError("JD_REVIEW_STATE", "该JD不在可审核发布状态", 409)
        job = await self.db.scalar(select(Job).where(Job.id == jd.job_id).with_for_update())
        company = await self.db.scalar(select(Company).where(Company.id == job.company_id).with_for_update())
        if job.status not in {"ACTIVE", "WAITING_REVIEW"} or company.status not in {"ACTIVE", "WAITING_REVIEW"}:
            raise AppError("CLOSED_JOB", "不能通过发布JD重新激活已关闭岗位", 409)
        requirements = list((await self.db.scalars(select(JobRequirement).where(
            JobRequirement.job_description_version_id == jd.id).order_by(JobRequirement.id))).all())
        claim_ids = [r.claim_id for r in requirements] + [(jd.metadata_json or {}).get("identity_claim_id")]
        claim_ids += [item["claim_id"] for item in (jd.metadata_json or {}).get("attribute_claims", [])]
        if not requirements or any(c is None for c in claim_ids):
            raise AppError("JD_EVIDENCE_REQUIRED", "岗位身份和所有要求都必须有Claim/Evidence", 422)
        evidence_text = {}
        for claim_id in set(claim_ids):
            claim = await self.db.get(Claim, claim_id)
            evidence = list((await self.db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == claim_id))).all())
            if not claim or not evidence:
                raise AppError("JD_EVIDENCE_REQUIRED", "缺少岗位证据", 422)
            for item in evidence:
                snapshot = await self.db.get(SourceSnapshot, item.source_snapshot_id)
                loc = item.source_location_json or {}
                start, end = loc.get("start_char"), loc.get("end_char")
                if (not snapshot or not isinstance(start, int) or not isinstance(end, int)
                    or not 0 <= start < end <= len(snapshot.content_text or "")
                    or snapshot.content_text[start:end] != item.excerpt
                    or sha256(snapshot.content_text.encode()).hexdigest() != snapshot.content_hash):
                    raise AppError("JD_EVIDENCE_INVALID", "来源正文或精确证据位置不一致", 422)
            evidence_text[claim_id] = normalize_text("\n".join(e.excerpt for e in evidence))
        constraints = matching_constraints or {}
        if not isinstance(constraints, dict) or set(constraints) - {"education_minimum", "majors", "graduation_years", "locations"}:
            raise AppError("JD_CONSTRAINT_INVALID", "只允许审核原文支持的结构化条件", 422)
        raw = normalize_text(jd.raw_text)
        for field, value in constraints.items():
            items = value if isinstance(value, list) else [value]
            if not items or any(not normalize_text(str(v)) or normalize_text(str(v)) not in raw for v in items):
                raise AppError("JD_CONSTRAINT_UNSUPPORTED", "条件必须来自真实JD原文；缺失保持未知", 422)
            if field != "education_minimum" and not isinstance(value, list):
                raise AppError("JD_CONSTRAINT_INVALID", "地区、专业、毕业年份必须为列表", 422)
        topics = requirement_topics or {}
        by_id = {str(r.id): r for r in requirements}
        if not isinstance(topics, dict) or set(topics) - by_id.keys():
            raise AppError("UNKNOWN_REQUIREMENT", "未知岗位要求引用", 422)
        # Text-alignment topic is not an assertion that the whole requirement
        # is satisfied. Exact source overlap only; no skill aliases/inference.
        for key, topic in topics.items():
            requirement = by_id[key]
            if (not isinstance(topic, str) or not topic.strip() or len(topic) > 255
                or normalize_text(topic) not in normalize_text(requirement.statement)
                or normalize_text(topic) not in evidence_text[requirement.claim_id]):
                raise AppError("REQUIREMENT_TOPIC_UNSUPPORTED", "技术主题必须同时出现在要求和引用证据中", 422)
        before = dict(publication_status=jd.publication_status, job_status=job.status, company_status=company.status)
        for key, topic in topics.items():
            by_id[key].normalized_value = normalize_text(topic)
        if constraints:
            jd.structured_data_json = {**(jd.structured_data_json or {}), "matching_constraints": constraints}
        now = datetime.now(timezone.utc)
        review = dict(reviewer_id=reviewer_id, reviewed_at=now.isoformat(), reason=reason.strip(),
                      matching_constraints=constraints, literal_alignment_topics=topics)
        jd.metadata_json = {**(jd.metadata_json or {}), "manual_review": review}
        jd.publication_status, jd.published_at = "PUBLISHED", now
        job.status, company.status = "ACTIVE", "ACTIVE"
        self.db.add(AuditLog(actor_user_id=reviewer_id, actor_role="ADMIN", action="public_career.jd.publish",
            resource_type="JobDescriptionVersion", resource_id=str(jd.id), before_json=before,
            after_json={"publication_status": "PUBLISHED"}, metadata_json=review, status="SUCCESS"))
        await self.db.flush()
        return dict(jd_version_id=jd.id, job_id=job.id, publication_status="PUBLISHED", reused=False)
