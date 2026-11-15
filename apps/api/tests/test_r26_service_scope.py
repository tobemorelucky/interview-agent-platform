"""Round 26 P0: the service, not the provider wrapper, owns the attribution.

The previous live run used an evaluation wrapper that did not expose `governed`, so
the gateway fell back to an ambient scope and two answers of a pair were billed to
one owner. The service now binds owner/route/database around every agent call, so a
wrapper cannot lose it — this test drives the official `answer` path with a governed
stub and checks the admission it produces.
"""
import asyncio
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from eval_pair_support import ScriptedEvaluation, seed_case, seed_user
from interview_api.infrastructure.models.governance import governed_call
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.infrastructure.models.scope import current_scope
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest

SKILL = "ai_agent_application"
QUESTION = "两路检索分数尺度不同，怎样做排序融合？请说明 RRF 的计算方式、好处与代价。"
RUBRIC = ["按排名倒数融合 1/(k+rank)，k 常取 60", "不依赖 BM25 与向量分数的原始尺度",
          "代价是丢弃分数大小信息", "融合参数与人工验证"]
ANSWER = ("用 RRF 按排名融合：每路取 1/(k+rank) 再累加，k 常取 60，这样不用比较 BM25 与余弦分数的量纲；"
          "融合后按固定 top-k 截断，并用人工标注查询验证提升。")
DIMS = dict(technical_correctness=4, coverage=4, depth=4, relevance=4, communication=4)


class GovernedEvaluation(ScriptedEvaluation):
    """A scripted evaluation that still travels through the admission gateway."""

    provider_name = "modelscope"
    model_name = "stub-evaluation"
    timeout = 30.0
    max_output_tokens = 3000

    async def generate(self, messages, *, schema):
        async def operation():
            return await ScriptedEvaluation.generate(self, messages, schema=schema)

        return await governed_call(self, messages, operation)


async def open_case(factory, case, *, user_id, evaluator):
    """Same shape as eval_pair_support.open_fixed_case, with an injected evaluator."""
    from eval_pair_support import FixedQuestionReplay
    main = FixedQuestionReplay(QUESTION, RUBRIC, "检索与 RAG", "rag_retrieval")
    core = MockInterviewService(factory, ModelRouter({"interview_main": main, "answer_evaluation": evaluator}))
    session = await core.create(user_id, CreateSessionRequest(
        company_id=case["company_id"], job_id=case["job_id"], question_count=1,
        interviewer_skill_id=SKILL, adaptive_interview=True))
    created = await core.start(session["id"], user_id)
    assert created["question"] == QUESTION
    return core, session["id"], created["question_id"]


def test_two_answers_of_two_users_are_booked_under_their_own_owner(database_url):
    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            case = await seed_case(factory, [
                "理解 RAG 检索与融合（混合召回、排序融合、RRF）",
                "能说明召回与排序的参数取舍及验证方式"])
            owners = []
            for _ in range(2):
                uid = await seed_user(factory)
                evaluator = GovernedEvaluation(DIMS, evaluation="stub 评价", covered=[], missing=[], issues=[])
                core, session_id, question_id = await open_case(factory, case, user_id=uid, evaluator=evaluator)
                await core.answer(session_id, uid, question_id, uuid4().hex, ANSWER)
                assert evaluator.calls == ["AdaptiveEvaluation"]
                async with factory() as db:
                    rows = (await db.execute(text(
                        "SELECT owner_key, route, status, model FROM model_admissions "
                        "WHERE owner_key = :o"), {"o": f"user:{uid}"})).fetchall()
                    evaluations = await db.scalar(text(
                        "SELECT count(*) FROM interview_evaluations WHERE session_id = :s"), {"s": session_id})
                assert len(rows) == 1, rows
                assert rows[0][1] == "answer_evaluation" and rows[0][2] == "SETTLED"
                assert rows[0][3] == "stub-evaluation"
                assert evaluations == 1
                owners.append(rows[0][0])
            assert len(set(owners)) == 2
            assert current_scope() is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
