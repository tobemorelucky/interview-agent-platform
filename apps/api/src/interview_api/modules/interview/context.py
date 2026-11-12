"""Only snapshot creation reads mutable public data / current candidate profile."""
from copy import deepcopy
from sqlalchemy import select, or_

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.modules.candidate_profile.service import CandidateProfileService
from interview_api.modules.candidate_profile.models import CandidateProfileVersion, CandidateFact
from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion, JobRequirement, Source, SourceSnapshot
from interview_api.modules.public_career.fingerprints import compute_content_fingerprint, compute_question_fingerprint
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.experience.models import InterviewQuestion, InterviewExperience
from interview_api.modules.memory.snapshot import build_memory_snapshot
from interview_api.modules.interview.topics import coverage, classify, is_hard_condition, validate_topic, mentioned_topics
from interview_api.modules.interview.skill_engine import SkillRegistry, selection_coverage, choose_skill_source, interaction_policy


APPROVED = ("APPROVED", "AUTO_APPROVED")


async def private_target(db, user_id, request):
    """The user's own confirmed JD, or None for the public path.

    Ownership is checked here, not in the router: a target id that belongs to
    someone else must never become interview context.
    """
    target_id = getattr(request, "job_target_id", None)
    if not target_id:
        return None
    from interview_api.modules.job_target.models import CONFIRMED, UserJobTarget
    target = await db.scalar(select(UserJobTarget).where(UserJobTarget.id == target_id,
        UserJobTarget.user_id == user_id).with_for_update(read=True, key_share=True))
    if target is None:
        raise AppError("JOB_TARGET_NOT_FOUND", "目标岗位不存在", 404)
    if target.parse_status != CONFIRMED or not target.requirements_json:
        raise AppError("JOB_TARGET_NOT_CONFIRMED", "请先解析并确认岗位要求，再开始面试", 409)
    return target


def target_requirements(target) -> list[dict]:
    return [dict(id=row["id"], statement=row["statement"], importance=row.get("importance") or "UNKNOWN",
                 topic=row.get("topic"), source=row.get("source") or "USER")
            for row in (target.requirements_json or [])]


async def build_context(db, user_id, request):
    # Resolve the job-source reference first. A job_target_id is an untrusted
    # cross-user reference: resolving ownership before the profile lookup means
    # "missing" and "someone else's" always take the same 404 path, and no
    # company name, JD text, requirement or candidate evidence can leak through
    # a different error code. Both session creation and the training workbench
    # call this function, so the order is enforced for both.
    if getattr(request, "job_target_id", None):
        if request.company_id or request.job_id or request.job_description_version_id:
            raise AppError("JOB_SOURCE_CONFLICT", "公共岗位与私人目标岗位只能二选一", 422)
    elif not request.company_id or not request.job_id:
        raise AppError("JOB_SOURCE_REQUIRED", "请选择已发布岗位，或先创建并确认自己的目标岗位", 422)
    target = await private_target(db, user_id, request)
    service = CandidateProfileService(db)
    version = (await db.get(CandidateProfileVersion, request.candidate_profile_version_id)
               if request.candidate_profile_version_id else await service.current(user_id))
    if not version:
        raise AppError("PROFILE_REQUIRED", "请先完成候选人画像", 409)
    profile = await service.serialize(user_id, version)
    if target is not None:
        # A user-supplied JD is its own source. No public company/job/JD row is
        # read, created or imitated, and its public question pool is empty.
        company = job = jd = None
        requirements = target_requirements(target)
        experiences, questions = [], []
    else:
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
        candidate_profile_version_id=version.id, company_id=company.id if company else None,
        job_id=job.id if job else None,
        job_description_version_id=jd.id if jd else None,
        company_name=target.company_name if target is not None else company.canonical_name,
        job_title=target.job_title if target is not None else job.canonical_title,
        job_target=dict(id=target.id, version=target.version, content_fingerprint=target.content_fingerprint,
                        source_type=target.source_type, parse_mode=target.parse_mode) if target is not None else None,
        profile=deepcopy(profile["snapshot"]), normalized=profile["normalized"],
        candidate_facts=[dict(id=f.id, field_path=f.field_path, statement=f.statement,
                              evidence_ids=_fact_keys(profile["snapshot"], f.field_path)) for f in facts],
        candidate_evidence=profile["evidence"],
        profile_version_number=version.version_number,
        jd_version_number=jd.version_number if jd else None,
        profile_producer=version.producer,
        jd_fingerprint=jd.content_fingerprint if jd else target.content_fingerprint,
        requirements=[dict(id=r["id"], statement=r["statement"], importance=r["importance"],
                           fingerprint=compute_content_fingerprint(r["statement"]),
                           topic=r.get("topic"), source=r.get("source")) if isinstance(r, dict)
                      else dict(id=r.id, statement=r.statement, importance=r.importance,
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
    if request.interviewer_skill_id:
        jd_source = None
        if target is not None:
            # Provenance stays explicit: user-supplied, never company-confirmed.
            jd_source = dict(source_type="USER_SUPPLIED", job_target_id=target.id,
                             version=target.version, content_hash=target.content_fingerprint,
                             jd_version_id=None)
        elif jd.source_snapshot_id:
            source_snapshot = await db.get(SourceSnapshot, jd.source_snapshot_id)
            source = await db.get(Source, source_snapshot.source_id) if source_snapshot else None
            if source:
                jd_source = dict(source_id=source.id, snapshot_id=source_snapshot.id,
                    url=source.canonical_url, source_type=source.source_type,
                    content_hash=source_snapshot.content_hash, jd_version_id=jd.id)
        context["interviewer_config"] = SkillRegistry().freeze(request.interviewer_skill_id,
            request.interview_round, request.interviewer_style, requirements=context["requirements"], jd_source=jd_source)
        if target is not None:
            from interview_api.modules.interview.skill_recommendation import recommend_for_target
            recommendation = recommend_for_target(target)
        else:
            from interview_api.modules.interview.skill_recommendation import recommend_jobs
            recommendation = (await recommend_jobs(db, [job.id])).get(job.id)
        automatic = request.interviewer_selection_source == "AUTO_RECOMMENDED"
        if automatic and (not recommendation or recommendation["recommended_skill_id"] != request.interviewer_skill_id):
            raise AppError("SKILL_RECOMMENDATION_CHANGED", "岗位或推荐已变化，请重新确认专业 Skill", 409)
        context["interviewer_selection"] = dict(source=request.interviewer_selection_source,
            skill_id=request.interviewer_skill_id, recommendation=recommendation)
    elif request.interview_round != "technical_screen" or request.interviewer_style != "standard":
        raise AppError("INTERVIEWER_SKILL_REQUIRED", "请先选择岗位专业 Skill，再设置轮次或风格", 422)
    if request.design_interview:
        from interview_api.modules.interview.system_design import SCENARIO
        if not request.interviewer_skill_id:
            raise AppError("INTERVIEWER_SKILL_REQUIRED", "系统设计面试须选择专业 Skill", 422)
        context["system_design"] = deepcopy(SCENARIO)
    if request.main_harness and not request.adaptive_interview:
        raise AppError("MAIN_HARNESS_REQUIRES_ADAPTIVE", "Main 工具模式仅支持自适应面试", 422)
    if request.adaptive_interview:
        if request.design_interview or not request.interviewer_skill_id:
            raise AppError("ADAPTIVE_MODE_INVALID", "自适应面试须选择专业 Skill，且不能与系统设计工具模式同时启用", 422)
        from interview_api.modules.interview.adaptive import GAP_SCOPE_CONTRACT, build_blueprint
        from interview_api.modules.interview.adaptive_prompts import freeze_prompts
        # Company-level/ambiguous roles are not company-job interview evidence.
        # V1 keeps its historical selection; V2 requires exact job association.
        if job is not None:
            related = {e.id for e in experiences if e.job_id == job.id}
            selected = [q for q in questions if q.job_id == job.id and (q.experience_id is None or q.experience_id in related)
                and ((q.routing_json or {}).get("source_anchor") or {}).get("kind") != "REPORTED_TOPIC"]
            ids = {q.id for q in selected}
            context["public_questions"] = [dict(row, source_anchor=deepcopy(next(q for q in selected if q.id == row["id"]).routing_json or {}).get("source_anchor"))
                for row in context["public_questions"] if row["id"] in ids]
            context["interview_experiences"] = [dict(row, reported_topics=deepcopy((next(e for e in experiences if e.id == row["id"]).extraction_output_json or {}).get("source_anchors", {}).get("reported_topics", []))[:12])
                for row in context["interview_experiences"] if row["id"] in related]
        else:
            # A private target has no reviewed public question or experience pool.
            # The Skill still supplies the knowledge targets; nothing is borrowed.
            context["public_questions"], context["interview_experiences"] = [], []
        context["adaptive_policy"] = dict(version="2.1.0", decision_contract="TARGET_SOURCE_PAIR_V1",
            context_projection="ROLE_CONTEXT_V1",
            evaluation_context="FOLLOWUP_SCORING_V1", gap_scope_contract=GAP_SCOPE_CONTRACT,
            prompts=freeze_prompts(contract="TARGET_SOURCE_PAIR_V1", memory_guided=True, context_projection=True,
                evaluation_context=True, gap_scope=True), invalid_decision="FAIL_SAFE_RETRY_NO_EXTRA_CALL")
        if request.main_harness:
            from interview_api.modules.interview.main_harness import VERSION as HARNESS_VERSION
            from interview_api.modules.interview.adaptive_prompts import MAIN_HARNESS_CONTEXT_HINTS as prompt
            context["adaptive_policy"]["main_harness_version"] = HARNESS_VERSION
            context["adaptive_policy"]["prompts"]["interview_main"] = dict(
                version=prompt.version, system=prompt.system, fingerprint=prompt.content_hash)
        from interview_api.modules.memory.capability_service import CapabilityMemoryService
        memory = CapabilityMemoryService(db)
        states = await memory.refresh(user_id)
        fresh = {r["topic"] for r in states if r["state"] == "VERIFIED" and not r["revoked"]}
        # V2.2 has one inferred planning layer. Legacy report observations remain
        # in the historical Snapshot/UI but must not bypass capability revocation.
        context["adaptive_blueprint"] = build_blueprint({**context, "recent_performance_observations": dict(items=[])},
            deprioritized_topics=fresh)
        context["memory_guided_plan"] = await memory.plan(user_id, context["adaptive_blueprint"], states=states)
        if request.training_mode:
            from interview_api.modules.interview.training import freeze_training
            # Find legitimate Skill goals BEFORE the ordinary question-count cap.
            full = build_blueprint({**context, 'budgets': {**context['budgets'], 'question_count': 8},
                'recent_performance_observations': dict(items=[])}, deprioritized_topics=fresh)
            freeze_training(context, request, states, full)
    elif request.training_mode:
        raise AppError('TRAINING_REQUIRES_ADAPTIVE', '训练须启用自适应面试并选择专业 Skill', 422)
    return context, version.source_resume_id, digest(context)


def _fact_keys(snapshot, path):
    parts = path.split(".")
    item = snapshot[parts[0]] if len(parts) == 1 else snapshot[parts[0]][int(parts[1])]
    return item["evidence_ids"]


def model_context(context, policy=None):
    """Project the pinned Snapshot, without sending each fact three times.

    Full provenance stays in the immutable DB Snapshot and the Controller. All
    fact IDs, evidence keys/excerpts, requirements and public sources remain.
    This is a DTO, not a new context/version boundary or a mutable profile read.
    """
    result = deepcopy({k: v for k, v in context.items() if k not in ("profile", "normalized", "interviewer_selection")})
    for fact in result["candidate_facts"]:
        # The exact excerpt is already in candidate_evidence. Keep identity,
        # classification and support refs, not the same paragraph twice.
        fact.pop("statement", None)
        parts = fact["field_path"].split(".")
        values = context.get("profile", {}).get(parts[0])
        if len(parts) == 2 and isinstance(values, list) and parts[1].isdigit():
            index = int(parts[1])
            if index < len(values) and isinstance(values[index], dict):
                fact["label"] = values[index].get("label")
                if "evidence_state" in values[index]:
                    fact["evidence_state"] = values[index]["evidence_state"]
    result["candidate_evidence"] = [
        {k: item[k] for k in ("key", "kind", "section", "excerpt") if k in item}
        for item in context["candidate_evidence"]
    ]
    result.pop("interview_experiences", None)  # Question intent already pinned.
    for name in ("approved_memory_context", "recent_performance_observations"):
        layer = context.get(name, {})
        result[name] = {"semantics": "USER_CONFIRMED_CONTEXT_NOT_SKILL_PROOF" if name == "approved_memory_context"
                       else "PRIOR_PRACTICE_OBSERVATIONS_NOT_STABLE_FACTS",
            "items": [{k: v for k, v in item.items() if k in ("id", "content", "text", "memory_type", "observation_type")}
                      for item in layer.get("items", [])]}
    if policy is not None:
        result["public_questions"] = [q for q in result["public_questions"] if
            policy.get("source_type") == "PUBLIC_QUESTION" and q["id"] == policy.get("source_id")]
    return result


def question_policy(context, questions, action="NEXT"):
    pacing = selection_coverage(context, coverage(questions, context["budgets"]["question_count"]))
    if action in ("FOLLOW_UP", "DEEP_DIVE"):
        return interaction_policy(context, dict(source_type="FOLLOW_UP" if action == "FOLLOW_UP" else "DYNAMIC_GENERATED", source_id=None,
                    target_topic=pacing["recent_topic"], target_category="TECHNICAL", topic_coverage=pacing))
    used_public = {q.source_question_id for q in questions}
    used_requirements = {q.job_requirement_id for q in questions if q.job_requirement_id is not None}
    # Private targets keep their requirement refs in the question refs, because
    # job_requirement_id must never point at an unrelated public row.
    used_requirements |= {(q.candidate_refs_json or {}).get("job_target_requirement") for q in questions
                          if (q.candidate_refs_json or {}).get("job_target_requirement") is not None}
    used_facts = {key for q in questions for key in (q.candidate_refs_json or {}).get("fact_ids", [])}
    pool = []
    seen_intents = set()
    def add(source_type, source_id, text, category=None):
        subject = classify(text)
        # Known degree data is never turned into an oral eligibility exam.
        if subject["topic"] == "education":
            return
        if subject["topic"] in pacing["blocked_topics"]:
            return
        if subject["category"] == "BACKGROUND" and not pacing["background_allowed"]:
            return
        if source_type == "CANDIDATE_PROFILE":
            topics = [t for t in mentioned_topics(text) if t not in pacing["blocked_topics"]]
            if topics:
                priorities = pacing.get("skill_priority_topics", [])
                subject["topic"] = min(topics, key=lambda t: (t not in priorities if priorities else False,
                    pacing["topic_counts"].get(t, 0), priorities.index(t) if t in priorities else topics.index(t)))
        intent = compute_content_fingerprint(text.casefold())
        if intent in seen_intents:
            return
        seen_intents.add(intent)
        pool.append(dict(source_type=source_type, source_id=source_id,
            target_topic=subject["topic"], target_category=("BACKGROUND" if subject["category"] == "BACKGROUND" else category or subject["category"])))
    for q in context["public_questions"]:
        if q["id"] not in used_public:
            add("PUBLIC_QUESTION", q["id"], q["question"])
    for r in context["requirements"]:
        # A qualification is not an interview skill test. Do not ask the LLM to
        # decide degree/major eligibility by interviewing the user.
        if r["id"] not in used_requirements and not is_hard_condition(r["statement"]):
            add("JOB_REQUIREMENT", r["id"], r["statement"], "JOB_TECHNICAL")
    for f in sorted(context["candidate_facts"], key=lambda f: (not f["field_path"].startswith("projects."), f["id"])):
        section = f["field_path"].split(".")[0]
        if f["id"] not in used_facts and section in ("projects", "skills", "work_experience", "internship_experience"):
            add("CANDIDATE_PROFILE", f["id"], f["statement"], "PROJECT" if section != "skills" else "FOUNDATION")
    technical = [p for p in pool if p["target_category"] != "BACKGROUND"]
    # Public opening intent is allowed; never choose a background entry merely
    # because it has the smallest DB ID. Thereafter favor uncovered technical
    # subjects and weighted coverage, not mechanical quota filling.
    main_count = pacing["main_question_count"]
    fresh_projects = [p for p in technical if p["source_type"] == "CANDIDATE_PROFILE" and p["target_category"] == "PROJECT"]
    configured = choose_skill_source(context["interviewer_config"], technical, pacing) if context.get("interviewer_config") else None
    if configured:
        selected = configured
    elif not questions and technical:
        selected = next((p for p in technical if p["source_type"] == "PUBLIC_QUESTION"), technical[0])
    elif len(questions) >= 3 and main_count == 1 and len([f for f in context["candidate_facts"] if f["field_path"].startswith("projects.")]) > 1 and fresh_projects:
        # After one opening + its follow-up chain, prefer another real project
        # before testing further details of the same RAG project through a JD.
        selected = min(fresh_projects, key=lambda p: (pacing["topic_counts"].get(p["target_topic"], 0), p["source_id"]))
    elif technical:
        def priority(p):
            category = p["target_category"]
            deficit = pacing["category_weights"].get(category, .1) * (main_count + 1) - pacing["category_counts"].get(category, 0)
            preferred = "JOB_REQUIREMENT" if main_count % 3 == 1 else "CANDIDATE_PROFILE"
            return (p["target_topic"] == pacing["recent_topic"] and p["target_topic"] != "unknown",
                    p["source_type"] != preferred if main_count < 3 else -deficit,
                    category != "PROJECT" if main_count == 2 else pacing["topic_counts"].get(p["target_topic"], 0),
                    p["source_id"])
        selected = min(technical, key=priority)
    elif pool:
        selected = pool[0]
    else:
        selected = dict(source_type="DYNAMIC_GENERATED", source_id=None,
                        target_topic="uncovered_technical", target_category="SCENARIO")
    return interaction_policy(context, {**selected, "topic_coverage": pacing})


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
        if artifact.source_question_id != policy["source_id"]:
            raise AppError("PUBLIC_QUESTION_CHANGED", "必须引用本场固定的公共题目来源", 422)
    elif artifact.source_type == "JOB_REQUIREMENT" and artifact.job_requirement_id != policy["source_id"]:
        raise AppError("REQUIREMENT_REQUIRED", "缺少本题岗位要求引用", 422)
    elif artifact.source_type == "CANDIDATE_PROFILE" and (policy["source_id"] not in artifact.candidate_fact_ids or not artifact.candidate_evidence_ids):
        raise AppError("CANDIDATE_EVIDENCE_REQUIRED", "缺少本题候选人证据", 422)
    validate_topic(artifact, policy)
