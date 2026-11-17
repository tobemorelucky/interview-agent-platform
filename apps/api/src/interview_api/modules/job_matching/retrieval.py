"""Bounded SQL retrieval followed by conservative deterministic qualification.

The database materializes only a configured window, not the complete Job library.
Unknown fields are retained; explicitly excluded rows are audited separately.
"""
from sqlalchemy import select, func, exists, or_, and_, cast, case, literal
from sqlalchemy.dialects.postgresql import JSONB
from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion, JobRequirement, JobTag
from interview_api.modules.candidate_profile.models import CandidateProfileVersion, CandidateFact
from interview_api.modules.candidate_profile.service import CandidateProfileService, iter_facts
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.job_matching.policy import norm, eligibility, evidence_matrix, ranking_components, pre_rank_key
from interview_api.modules.job_matching.skills import profile_topics, requirement_topics
from interview_api.modules.public_career.requirement_vocabulary import effective_strength


async def profile_snapshot(db, user_id, version_id=None):
    service = CandidateProfileService(db)
    version = await db.get(CandidateProfileVersion, version_id) if version_id else await service.current(user_id)
    if not version:
        raise AppError("MATCH_PROFILE_REQUIRED", "请先生成候选人画像", 409)
    view = await service.serialize(user_id, version)  # ownership check before any private-data access
    content = dict(iter_facts(view["snapshot"]))
    records = {r["fact_id"]: r for rows in view["normalized"].values() for r in rows}
    facts = []
    for fact in (await db.scalars(select(CandidateFact).where(CandidateFact.version_id == version.id).order_by(CandidateFact.id))).all():
        item = content[fact.field_path]
        record = records.get(fact.id, {})
        facts.append(dict(id=fact.id, category=fact.field_path.split(".")[0], field_path=fact.field_path,
            statement=fact.statement, label=item.get("label"), evidence_ids=item["evidence_ids"], provenance=fact.provenance,
            record_id=record.get("id"), evidence_state=record.get("evidence_state")))
    return dict(id=version.id, version_number=version.version_number, source_resume_id=version.source_resume_id,
        producer=version.producer, summary=version.summary, unknowns=version.unknowns,
        facts=facts, evidence=view["evidence"], fingerprint=digest(view["snapshot"]))


async def retrieve(db, profile, filters):
    if not 1 <= settings.job_matching_top_n <= 50 or not settings.job_matching_top_n <= settings.job_matching_retrieval_limit <= 2000:
        raise AppError("MATCH_POLICY_CONFIG", "岗位召回预算配置不合法", 503)
    latest = select(JobDescriptionVersion.id).where(JobDescriptionVersion.job_id == Job.id,
        JobDescriptionVersion.publication_status == "PUBLISHED").order_by(JobDescriptionVersion.version_number.desc(), JobDescriptionVersion.id.desc()).limit(1).correlate(Job).scalar_subquery()
    topics = {norm(f["label"]) for f in profile["facts"] if f["category"] == "skills" and f["label"]}
    topics |= {norm(v) for v in filters["tags"]}
    overlap = select(func.count(JobTag.id)).where(JobTag.job_id == Job.id, JobTag.normalized_tag.in_(topics)).correlate(Job).scalar_subquery()
    base = select(Job, Company, JobDescriptionVersion, overlap.label("overlap")).join(Company, Company.id == Job.company_id).join(
        JobDescriptionVersion, JobDescriptionVersion.id == latest).where(Job.status == "ACTIVE", Company.status == "ACTIVE",
        exists(select(JobRequirement.id).where(JobRequirement.job_description_version_id == JobDescriptionVersion.id)))
    # SQL screens mature structured fields. Missing values deliberately survive.
    admitted = []
    for field, choices in ((Job.campus_type, filters["campus_types"]), (Job.job_family, filters["job_families"])):
        if choices:
            admitted.append(or_(field.is_(None), func.lower(func.trim(field)).in_([norm(v) for v in choices])))
    if filters["exclude_job_ids"]: admitted.append(Job.id.not_in(filters["exclude_job_ids"]))
    if filters["exclude_company_ids"]: admitted.append(Job.company_id.not_in(filters["exclude_company_ids"]))
    if filters["exclude_tags"]:
        admitted.append(~exists(select(JobTag.id).where(JobTag.job_id == Job.id, JobTag.normalized_tag.in_([norm(v) for v in filters["exclude_tags"]]))))
    if filters["tags"]:
        admitted.append(or_(~exists(select(JobTag.id).where(JobTag.job_id == Job.id)),
            exists(select(JobTag.id).where(JobTag.job_id == Job.id, JobTag.normalized_tag.in_([norm(v) for v in filters["tags"]])))))
    if filters["locations"]:
        locations = cast(JobDescriptionVersion.structured_data_json, JSONB)["matching_constraints"]["locations"]
        is_array = func.jsonb_typeof(locations) == "array"
        safe_array = case((is_array, locations), else_=cast(literal("[]"), JSONB))
        elements = func.jsonb_array_elements(safe_array).table_valued("value").alias("location_values")
        invalid = exists(select(1).select_from(elements).where(func.jsonb_typeof(elements.c.value) != "string"))
        texts = func.jsonb_array_elements_text(safe_array).table_valued("value").alias("location_texts")
        intersects = exists(select(1).select_from(texts).where(func.lower(func.trim(texts.c.value)).in_([norm(v) for v in filters["locations"]])))
        reliable = and_(is_array, func.jsonb_array_length(safe_array) > 0, ~invalid)
        admitted.append(or_(~func.coalesce(reliable, False), intersects))  # unknown/malformed survives SQL screen
    window = settings.job_matching_retrieval_limit
    condition = and_(*admitted) if admitted else True
    eligible_query = base.where(condition).order_by(overlap.desc(), Job.id).limit(window)
    rows = list((await db.execute(eligible_query)).all())
    # Bounded excluded audit, never part of the model candidate set.
    if admitted:
        # Prioritize hard mismatches in otherwise requested companies over the
        # potentially very large explicit-exclusion set in this bounded audit.
        explicit_exclusion = or_(Job.company_id.in_(filters["exclude_company_ids"]), Job.id.in_(filters["exclude_job_ids"]))
        rows += list((await db.execute(base.where(~condition).order_by(case((explicit_exclusion, 1), else_=0), Job.id)
            .limit(settings.job_matching_top_n))).all())
    results = []
    candidate_terms = profile_topics(profile["facts"])
    for job, company, jd, count in rows:
        requirements = (await db.scalars(select(JobRequirement).where(JobRequirement.job_description_version_id == jd.id).order_by(JobRequirement.id))).all()
        structured = jd.structured_data_json if isinstance(jd.structured_data_json, dict) else {}
        tags = list((await db.scalars(select(JobTag.tag).where(JobTag.job_id == job.id).order_by(JobTag.id))).all())
        item = dict(job_id=job.id, company_id=company.id, company_name=company.canonical_name, title=job.canonical_title,
            job_family=job.job_family, campus_type=job.campus_type, status=job.status,
            jd_id=jd.id, jd_version_number=jd.version_number, jd_fingerprint=jd.content_fingerprint,
            matching_constraints=structured.get("matching_constraints", {}), tags=tags,
            requirements=[dict(id=r.id, requirement_type=r.requirement_type, normalized_value=r.normalized_value,
                statement=r.statement,
                importance=effective_strength(r.statement, r.importance).value,
                claim_id=r.claim_id) for r in requirements],
            demo=bool((jd.metadata_json or {}).get("fixture") or (jd.metadata_json or {}).get("demo") or (jd.metadata_json or {}).get("mode") == "replay"),
            sql_tag_overlap=count)
        # JobTag rows are empty for curated JDs, so a tag-only overlap would let
        # the database id decide the order. Count the literal technology overlap
        # between this JD's requirements and the candidate's own material.
        job_terms = set()
        for requirement in item["requirements"]:
            job_terms |= requirement_topics(requirement)
        item["topic_overlap"] = len(candidate_terms & job_terms)
        item["job_terms"] = sorted(job_terms)
        item["input_fingerprint"] = digest(item)
        item.update(eligibility(item, filters))
        item["evidence_matrix"] = evidence_matrix(item, profile)
        item["ranking_components"] = ranking_components(item)
        results.append(item)
    normal = sorted([r for r in results if r["eligibility_status"] != "NOT_SATISFIED"], key=pre_rank_key)
    selected = normal[:settings.job_matching_top_n]
    for rank, item in enumerate(selected, 1): item["pre_rank"] = rank
    excluded = [r for r in results if r["eligibility_status"] == "NOT_SATISFIED"][:settings.job_matching_top_n]
    basis = [item["ranking_components"]["evidence_basis"] for item in selected]
    confidence = "EVIDENCE" if any(value != "NONE" for value in basis) else "NONE"
    coverage = dict(status="SUFFICIENT" if len(selected) >= settings.job_matching_top_n else "LIMITED" if selected else "INSUFFICIENT",
        candidate_count=len(selected), excluded_count=len(excluded), top_n=settings.job_matching_top_n,
        retrieval_window=window, window_may_be_truncated=len(rows) >= window,
        omitted_eligible_count=max(0, len(normal) - len(selected)),
        ranking_confidence=confidence,
        message="只使用已发布公共数据；岗位库覆盖不足时不联网补库。" if confidence == "EVIDENCE"
            else "材料里没有能和岗位要求对照的技术证据，这里的顺序不代表匹配程度，请先补全简历或技能。")
    return selected, excluded, coverage
