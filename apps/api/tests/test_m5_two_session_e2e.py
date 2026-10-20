"""Two real persisted interviews, explicitly offline Replay/fixture/demo models."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

from sqlalchemy import func, select

from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_COACH_ROUTE
from interview_api.modules.interview.models import InterviewContextSnapshot, InterviewSession
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview_report.models import FinalInterviewReport, InterviewPerformanceObservation
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.modules.memory.models import UserMemoryEvent, UserMemoryItem, UserSkillProfile
from interview_api.modules.memory.proposal_models import MemoryProposal
from interview_api.modules.memory.proposal_service import MemoryProposalService
from interview_api.modules.memory.service import MemoryService
from interview_api.modules.memory.policies import MemoryActor
from test_m4_interview import (
    queue_start, queue_answer, full_replay, run_with_database,
)


class CapturingReplayModelProvider(ReplayModelProvider):
    def __init__(self, responses):
        super().__init__(responses)
        self.requests = []

    async def generate(self, messages, *, schema):
        self.requests.append(dict(messages=deepcopy(messages), schema=deepcopy(schema)))
        return await super().generate(messages, schema=schema)


def coach_fixture(evaluations):
    """IDs come from actual persisted evaluations, never fabricated question refs."""
    refs = [e["evidence_ref"] for e in evaluations]
    finding = lambda text, ids: dict(statement=text, supporting_evaluation_ids=ids)
    return dict(
        overall_summary="Replay / fixture / demo：本场模拟面试的回答表现与练习建议，非长期能力结论。",
        strengths=[finding("本场项目主题回答中对技术取舍的说明较完整。", [refs[3]])],
        weaknesses=[finding("本场末尾回答对并发更新边界的覆盖有限。", [refs[-1]])],
        recurring_issues=[finding("本场多轮回答尚未完整说明并发更新边界。", refs[:2])],
        job_alignment=[finding("本场回答为岗位要求中的缓存主题提供了部分支持。", [refs[2]])],
        recommended_actions=["结合本场回答，练习解释缓存失效与并发更新边界。"],
        practice_plan=["先复盘有支持引用的回答，再练习技术原理、实现与边界说明。"],
        memory_proposals=[
            dict(proposal_type="PREPARATION_FOCUS", content="我希望下一场重点练习并发更新边界。", supporting_evaluation_ids=refs[:2]),
            dict(proposal_type="PREFERENCE", content="我希望下一场针对遗漏点进行补问。", supporting_evaluation_ids=[refs[0]]),
            dict(proposal_type="SKILL_OBSERVATION", content="本场模拟面试末尾回答的覆盖有限，仅作本场表现观察。", supporting_evaluation_ids=[refs[-1]]),
        ],
    )


async def two_session_replay(factory):
    first = await full_replay(factory)
    uid, sid = first["user_id"], first["session_id"]
    coach = ReplayModelProvider([])
    reports = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: coach}))
    before = await reports.session_detail(sid, uid)
    coach.responses.append(coach_fixture(before["evaluations"]))
    report = await reports.generate(sid, uid)
    assert report["generation_status"] == "COMPLETED" and report["is_current"]
    assert report["statistics"]["overall_average"] == 3.1667
    assert report["statistics"]["deep_dive_count"] == 1
    assert report["provider"] == "replay"
    assert (await reports.generate(sid, uid))["id"] == report["id"] and coach.calls == 1
    async with factory() as db:
        assert not await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == uid))
        proposals = list((await db.scalars(select(MemoryProposal).where(MemoryProposal.report_id == report["id"]).order_by(MemoryProposal.id))).all())
        assert len(proposals) == 3 and all(p.status == "PENDING" for p in proposals)
        service = MemoryProposalService(db)
        approved = await service.decide(uid, proposals[0].id, "APPROVED", reason="Replay user approval")
        corrected_text = "我希望下一场先提示，再围绕遗漏点进行补问。"
        corrected = await service.decide(uid, proposals[1].id, "CORRECTED", content=corrected_text, reason="Replay user correction")
        rejected = await service.decide(uid, proposals[2].id, "REJECTED", reason="Replay user rejection")
        await db.commit()
        assert approved["memory_item_id"] and corrected["memory_item_id"] and not rejected["memory_item_id"]
        assert corrected["original_content"] != corrected["corrected_content"] == corrected_text
        assert corrected["changed_by"] == "user" and corrected["corrected_at"]
    async with factory() as db:
        assert await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id == uid)) == 2
        assert await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == uid, UserMemoryEvent.actor_type == "USER")) == 2
        assert not await db.scalar(select(func.count(UserSkillProfile.id)).where(UserSkillProfile.user_id == uid))
        source = await db.get(InterviewContextSnapshot, report["context_snapshot_id"])
        old_snapshot = deepcopy(source.context_json)
    # Reuse the same user's real, pinned Profile/JD rather than create new demo facts.
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.infrastructure.models.router import INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
    main, evaluator = CapturingReplayModelProvider([]), CapturingReplayModelProvider([])
    core = MockInterviewService(factory, ModelRouter({INTERVIEW_MAIN_ROUTE: main, ANSWER_EVALUATION_ROUTE: evaluator}))
    second = await core.create(uid, CreateSessionRequest(company_id=old_snapshot["company_id"], job_id=old_snapshot["job_id"],
        candidate_profile_version_id=old_snapshot["candidate_profile_version_id"],
        job_description_version_id=old_snapshot["job_description_version_id"], question_count=3))
    pinned = deepcopy(second["context_snapshot"])
    memories = pinned["approved_memory_context"]["items"]
    assert {m["id"] for m in memories} == {approved["memory_item_id"], corrected["memory_item_id"]}
    assert next(m["content"] for m in memories if m["id"] == corrected["memory_item_id"]) == corrected_text
    assert rejected["id"] not in {m["provenance"].get("proposal_id") for m in memories}
    observations = pinned["recent_performance_observations"]
    assert observations["items"] and not observations["is_stable_fact"]
    assert all(not o["user_confirmed"] and not o["is_stable_fact"] for o in observations["items"])
    assert pinned["approved_memory_context"]["fingerprint"] != observations["fingerprint"]
    async with factory() as db:
        count_before = await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == uid))
    # Modifying approved Memory and creating new confirmed Memory cannot drift this session.
    async with factory() as db:
        await MemoryService(db).update_memory(uid, approved["memory_item_id"], {"content": "Replay user: 后续改为练习项目边界。"}, MemoryActor("USER", uid))
        await MemoryService(db).create_memory(uid, {"memory_type": "PREFERENCE", "content": "Replay user: 本场创建后才确认的新偏好。"}, MemoryActor("USER", uid))
    assert (await core.detail(second["id"], uid))["context_snapshot"] == pinned
    await queue_start(core, main, uid, second["id"])
    for action in ("FOLLOW_UP", "NEXT", "COMPLETE"):
        request = await queue_answer(core, main, evaluator, uid, second["id"], action)
        await core.answer(second["id"], uid, request.question_id, request.idempotency_key, request.content)
        assert (await core.detail(second["id"], uid))["context_snapshot"] == pinned
        # Verify actual Agent payload used the pinned two-layer memory, not current DB.
        for provider in (main, evaluator):
            for call in provider.requests:
                payload = json.loads(call["messages"][-1]["content"])["input"]
                for name, layer in (("approved_memory_context", pinned["approved_memory_context"]),
                                    ("recent_performance_observations", observations)):
                    actual = payload["snapshot"][name]
                    assert [(m['id'], m['content']) for m in actual['items']] == [(m['id'], m['content']) for m in layer['items']]
                    assert 'NOT_SKILL_PROOF' in actual['semantics'] or 'NOT_STABLE_FACTS' in actual['semantics']
    final = await core.detail(second["id"], uid)
    assert final["status"] == "COMPLETED" and all(q["status"] == "COMPLETED" for q in final["questions"])
    async with factory() as db:
        assert (await db.get(InterviewSession, sid)).status == "COMPLETED"
        assert (await db.get(FinalInterviewReport, report["id"])).is_current
        assert await db.scalar(select(func.count(InterviewPerformanceObservation.id)).where(InterviewPerformanceObservation.report_id == report["id"])) >= 3
        assert (await db.get(InterviewContextSnapshot, report["context_snapshot_id"])).context_json == old_snapshot
        count_after = await db.scalar(select(func.count(UserMemoryEvent.id)).where(UserMemoryEvent.user_id == uid))
        assert count_after == count_before + 2  # Only the two explicit user writes, never interview reads.
    return dict(mode="replay / fixture / demo", first_session_id=sid, second_session_id=second["id"], user_id=uid,
        report_id=report["id"], report_status=report["generation_status"], statistics=report["statistics"],
        proposal_decisions=[approved["status"], corrected["status"], rejected["status"]],
        approved_memory_ids=[m["id"] for m in memories], observation_ids=[o["id"] for o in observations["items"]],
        second_status=final["status"], snapshot_fingerprint=second["snapshot_fingerprint"], coach_calls=coach.calls)


def test_replay_two_sessions_report_controlled_memory_snapshot(database_url):
    with patch("httpx.AsyncClient.request", side_effect=AssertionError("Replay must never access external network")):
        result = asyncio.run(run_with_database(database_url, two_session_replay))
    assert result["second_status"] == "COMPLETED" and result["coach_calls"] == 1
