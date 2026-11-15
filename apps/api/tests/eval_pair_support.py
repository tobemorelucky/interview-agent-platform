"""Replay harness for the *official* Evaluation chain.

Every step below is the product's own path:

``MockInterviewService.create`` → ``SessionController.create`` (frozen snapshot)
``MockInterviewService.start``  → decision contract → ``SessionController.start``
``MockInterviewService.answer`` → ``prepare_answer`` → Evaluation Agent →
``commit_answer`` → ``InterviewEvaluation`` row + state advance.

Nothing inserts an evaluation, a question or an answer directly. The model seam is
Replay: the Main question is *replayed* so the same fixed question reaches both
sessions of a pair, and the Evaluation reply is scripted so the persisted values
can be compared with what the double returned.

``question_count=1`` is deliberate: after the single answer the Controller reaches
COMPLETE, so submitting an answer costs exactly one Evaluation request and never
an extra next-question request.
"""
import json
from uuid import uuid4

# Register every mapper (the snapshot's job_target_id points at user_job_targets);
# a subset import would otherwise fail with NoReferencedTableError at flush time.
import interview_api.modules.models  # noqa: F401
import interview_api.modules.job_target.models  # noqa: F401

from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.topics import mentioned_topics
from v21_support import selection_artifact

MAIN_ROUTE = "interview_main"
EVAL_ROUTE = "answer_evaluation"
DIMENSIONS = ("technical_correctness", "coverage", "depth", "relevance", "communication")


class FixedQuestionReplay(ModelProvider):
    """Replay the dataset question through the real decision contract.

    The question is chosen from the session's own legal candidate space, so
    ``validate_decision`` still checks topic/source/difficulty instead of being
    bypassed. Nothing about the Controller is mocked.
    """
    provider_name, model_name = "replay", "R24_FIXED_QUESTION"

    def __init__(self, question, expected_points, dimension, topic):
        self.question, self.expected_points = question, list(expected_points)
        self.dimension, self.topic = dimension, topic
        self.calls, self.selected = [], None

    def _pair(self, payload):
        adaptive = payload["adaptive"]
        targets, sources = adaptive.get("targets") or {}, adaptive.get("sources") or {}
        wanted = mentioned_topics(self.question)
        if self.topic:
            wanted = [self.topic]
        for candidate in adaptive["candidates"]:
            kid, ref = candidate["target_knowledge_id"], candidate["source"]["ref"]
            target = targets.get(kid)
            if not target or target.get("topic") not in wanted:
                continue
            if "NEXT" not in candidate.get("strategies", []):
                continue
            if sources.get(ref, {}).get("source_type") not in (
                    "JOB_REQUIREMENT", "PUBLIC_QUESTION", "CANDIDATE_PROFILE"):
                continue
            return kid, ref, target
        raise AssertionError(f"no legal candidate for topic {self.topic or wanted}: "
                             f"{[(c['target_knowledge_id'], c['source']['ref']) for c in adaptive['candidates']]}")

    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        self.calls.append(schema["title"])
        assert schema["title"] in ("AdaptiveSelection", "AdaptiveDecision"), schema["title"]
        kid, ref, target = self._pair(payload)
        self.selected = (kid, ref)
        value = dict(decision_version="2.1.0", strategy="NEXT", target_knowledge_id=kid,
                     supporting_refs=[ref], reason_tags=["OPENING"], question_source_ref=ref,
                     proposed_question=dict(question=self.question,
                         expected_points=self.expected_points, dimension=self.dimension,
                         difficulty=payload["desired_difficulty"]))
        if schema["title"] == "AdaptiveSelection":
            value = selection_artifact(value, payload,
                scoped="focus_gap_ids" in schema.get("properties", {}))
        return ModelResponse(json.dumps(value, ensure_ascii=False),
                             dict(input_tokens=0, output_tokens=0, total_tokens=0))


class ScriptedEvaluation(ModelProvider):
    """Reply with one explicit evaluation artifact; record what was asked."""
    provider_name, model_name = "replay", "R24_SCRIPTED_EVALUATION"

    def __init__(self, dims, *, evaluation="Replay 评价", covered=None, missing=None,
                 issues=None, next_action="NEXT", fail_once=False):
        self.dims = dict(dims)
        self.evaluation = evaluation
        self.covered, self.missing, self.issues = covered or [], missing or [], issues or []
        self.next_action, self.fail_once = next_action, fail_once
        self.calls, self.failed = [], False

    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        self.calls.append(schema["title"])
        assert schema["title"] == "AdaptiveEvaluation", schema["title"]
        if self.fail_once and not self.failed:
            self.failed = True
            from interview_api.infrastructure.models.provider import ModelProviderError
            raise ModelProviderError("model_request_failed", "DEMO one-shot evaluation failure")
        kid = payload["adaptive"]["active_knowledge_id"]
        assert payload["answer"], "the evaluation payload must carry the real answer text"
        value = dict(self.dims, evaluation=self.evaluation, covered_points=self.covered,
                     missing_points=self.missing, factual_or_reasoning_issues=self.issues,
                     follow_up_focus=[], next_action_recommendation=self.next_action,
                     reference_answer=None,
                     knowledge_observations=[dict(knowledge_id=kid,
                         state="DEMONSTRATED_IN_SESSION" if sum(self.dims.values()) >= 15 else "INSUFFICIENT_EVIDENCE",
                         answer_quotes=[payload["answer"][:80]] if sum(self.dims.values()) >= 15 else [],
                         missing_mechanisms=[] if sum(self.dims.values()) >= 15 else ["未说明关键机制"],
                         resolved_gap_ids=[])])
        return ModelResponse(json.dumps(value, ensure_ascii=False),
                             dict(input_tokens=0, output_tokens=0, total_tokens=0))


async def seed_case(factory, statements):
    """One published JD both sessions of a pair can use.

    Deliberately *without* a user: completing a session projects capability memory
    for its owner, and that memory deprioritises the just-verified topic on the
    owner's next blueprint. A pair therefore needs two owners over one identical
    JD, not two sessions under one owner.
    """
    import interview_api.modules.models  # noqa: F401  (register every mapper)
    from interview_api.modules.experience.models import InterviewExperience, InterviewQuestion
    from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion, JobRequirement
    async with factory() as db:
        company = Company(canonical_name="R24 评测夹具 " + uuid4().hex)
        db.add(company)
        await db.flush()
        job = Job(company_id=company.id, canonical_title="AI Agent 应用开发工程师")
        db.add(job)
        await db.flush()
        jd = JobDescriptionVersion(job_id=job.id, version_number=1,
            raw_text="R24 评测夹具 JD：" + "；".join(statements), publication_status="PUBLISHED")
        db.add(jd)
        await db.flush()
        for statement in statements:
            db.add(JobRequirement(job_description_version_id=jd.id, statement=statement,
                                  requirement_type="SKILL"))
        experience = InterviewExperience(company_id=company.id, job_id=job.id,
            review_status="APPROVED", summary="R24 fixture", content_text="R24 fixture content")
        db.add(experience)
        await db.flush()
        db.add(InterviewQuestion(company_id=company.id, job_id=job.id, experience_id=experience.id,
            question="R24 公共题占位：请说明一个技术决策的取舍",
            standard_answer="取舍\n边界", review_status="APPROVED", category="缓存", difficulty="MEDIUM"))
        await db.commit()
        return dict(company_id=company.id, job_id=job.id)


async def seed_user(factory):
    """A throwaway candidate with a real profile, so a session has candidate facts."""
    from test_m3_candidate_profile import seed as seed_resume, response, model_router
    from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
    user_id, resume_id = await seed_resume(factory)
    router, _ = model_router([response(resume_id)])
    profile = await ResumeProfileAgent(factory, router).run(resume_id)
    assert profile["status"] == "COMPLETED", profile
    return user_id


class FixedCase:
    """A legal one-question session whose question and rubric are fixed."""

    def __init__(self, core, session_id, user_id, question_id, main, evaluator):
        self.core, self.session_id, self.user_id = core, session_id, user_id
        self.question_id, self.main, self.evaluator = question_id, main, evaluator
        self.answer_key = uuid4().hex

    async def submit(self, text, *, key=None, allow_unknown_retry=False):
        return await self.core.answer(self.session_id, self.user_id, self.question_id,
            key or self.answer_key, text, allow_unknown_retry=allow_unknown_retry)


async def open_fixed_case(factory, case, *, user_id, skill_id, question, expected_points, dimension,
                          topic, dims, evaluation="Replay 评价", covered=None, missing=None,
                          issues=None, fail_once=False):
    main = FixedQuestionReplay(question, expected_points, dimension, topic)
    evaluator = ScriptedEvaluation(dims, evaluation=evaluation, covered=covered, missing=missing,
                                   issues=issues, fail_once=fail_once)
    core = MockInterviewService(factory, ModelRouter({MAIN_ROUTE: main, EVAL_ROUTE: evaluator}))
    session = await core.create(user_id, CreateSessionRequest(
        company_id=case["company_id"], job_id=case["job_id"], question_count=1,
        interviewer_skill_id=skill_id, adaptive_interview=True))
    created = await core.start(session["id"], user_id)
    assert created["question"] == question, created["question"]
    return FixedCase(core, session["id"], user_id, created["question_id"], main, evaluator)
