"""The official Evaluation chain, end to end, with Replay at the model seam.

Verifies what round 24 could not: that an answer submitted through the product's
own orchestration ends with a *persisted* evaluation row, that both answers of a
pair are evaluated on the same question/rubric in independent sessions, what else
the submission triggers, and that no simulated result reaches real Memory.
"""
import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker

from eval_pair_support import DIMENSIONS, open_fixed_case, seed_case, seed_user

SKILL = "ai_agent_application"
STATEMENTS = [
    "理解 RAG 检索与融合（混合召回、排序融合、RRF）",
    "了解 LangGraph 等有状态 Agent 编排与断点恢复",
]
QUESTION = "两路检索分数尺度不同，怎样做排序融合？请说明 RRF 的计算方式、好处与代价。"
RUBRIC = ["按排名倒数融合 1/(k+rank)，k 常取 60", "不依赖 BM25 与向量分数的原始尺度",
          "代价是丢弃分数大小信息", "融合参数与人工验证"]
GOOD = ("用 RRF 按排名融合：每路取 1/(k+rank) 再累加，k 常取 60，这样不用比较 BM25 与余弦分数的量纲；"
        "融合后按固定 top-k 截断，并用人工标注查询验证提升。")
POOR = ("直接把 BM25 分数和向量余弦相似度相加就行，因为两者都是 0 到 1 的分数，量纲一致，不需要归一化。"
        "RRF 是把两路分数相乘再取对数，适合所有场景，而且不需要调任何参数。")
GOOD_DIMS = dict(technical_correctness=5, coverage=4, depth=4, relevance=5, communication=5)
POOR_DIMS = dict(technical_correctness=1, coverage=1, depth=1, relevance=2, communication=2)


def _case_kwargs(dims, **extra):
    return dict(skill_id=SKILL, question=QUESTION, expected_points=RUBRIC, dimension="检索与 RAG",
                topic="rag_retrieval", dims=dims, **extra)


async def _count(factory, table, where="TRUE", params=None):
    async with factory() as db:
        return await db.scalar(text(f"SELECT count(*) FROM {table} WHERE {where}"), params or {})


def test_paired_answers_get_one_persisted_evaluation_each_from_the_official_chain(database_url):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            case = await seed_case(factory, STATEMENTS)
            # Two owners over one identical JD: completing the first session would
            # otherwise project capability memory and deprioritise the topic for
            # the second one, which is exactly the contamination a pair must avoid.
            good = await open_fixed_case(factory, case, user_id=await seed_user(factory),
                                         **_case_kwargs(GOOD_DIMS))
            poor = await open_fixed_case(factory, case, user_id=await seed_user(factory),
                                         **_case_kwargs(POOR_DIMS))
            async with factory() as db:
                memory_mark = await db.scalar(text(
                    "SELECT coalesce(max(id), 0) FROM capability_memories"))

            events_good = await good.submit(GOOD)
            events_poor = await poor.submit(POOR)

            # The question and rubric are identical in two independent sessions.
            assert good.question_id != poor.question_id
            for fixed in (good, poor):
                assert fixed.main.selected == good.main.selected
                async with factory() as db:
                    row = (await db.execute(text(
                        "SELECT question, standard_answer, candidate_refs_json FROM interview_session_questions "
                        "WHERE id = :i"), {"i": fixed.question_id})).first()
                assert row[0] == QUESTION
                assert row[1].splitlines() == RUBRIC
            async with factory() as db:
                snapshots = (await db.execute(text(
                    "SELECT context_json FROM interview_context_snapshots WHERE interview_session_id IN (:a, :b)"),
                    {"a": good.session_id, "b": poor.session_id})).fetchall()
            assert len(snapshots) == 2
            contexts = [s[0] if isinstance(s[0], dict) else s[0] for s in snapshots]
            for key in ("requirements", "company_name", "job_title", "adaptive_blueprint"):
                assert contexts[0][key] == contexts[1][key], key

            # One evaluation per answer, persisted by commit_answer, not inserted.
            for fixed, text_answer, dims, events in (
                    (good, GOOD, GOOD_DIMS, events_good), (poor, POOR, POOR_DIMS, events_poor)):
                rows = await _count(factory, "interview_evaluations", "session_id = :s", {"s": fixed.session_id})
                assert rows == 1
                async with factory() as db:
                    stored = (await db.execute(text(
                        "SELECT " + ", ".join(DIMENSIONS) + ", provider, model, answer_id, artifact_json "
                        "FROM interview_evaluations WHERE session_id = :s"), {"s": fixed.session_id})).first()
                assert {key: stored[i] for i, key in enumerate(DIMENSIONS)} == dims
                assert stored[5] == "replay" and stored[6] == "R24_SCRIPTED_EVALUATION"
                answer_id = stored[7]
                async with factory() as db:
                    answer = (await db.execute(text(
                        "SELECT status, error FROM interview_answers WHERE id = :i"), {"i": answer_id})).first()
                    message = await db.scalar(text(
                        "SELECT content FROM interview_messages WHERE id = "
                        "(SELECT message_id FROM interview_answers WHERE id = :i)"), {"i": answer_id})
                assert answer[0] == "COMPLETED" and answer[1] is None
                assert message == text_answer
                assert [name for name, _ in events][0] == "evaluation"
                # One Evaluation request, and the replayed Main question; no Coach call.
                assert fixed.evaluator.calls == ["AdaptiveEvaluation"]
                assert fixed.main.calls == ["AdaptiveSelection"]

            # No simulated result reaches a *real* user's memory, and no Coach run
            # is triggered by submitting an answer. Note the product behaviour this
            # pins down: build_context always freezes a memory_guided_plan, so
            # complete() always reprojects capability memory for the session owner.
            # The harness is therefore only ever pointed at a throwaway user.
            assert await _count(factory, "capability_memories",
                                "id > :m AND user_id NOT IN (:a, :b)",
                                {"m": memory_mark, "a": good.user_id, "b": poor.user_id}) == 0
            assert await _count(factory, "final_interview_reports",
                                "session_id IN (:a, :b)",
                                {"a": good.session_id, "b": poor.session_id}) == 0
            for fixed in (good, poor):
                async with factory() as db:
                    status = await db.scalar(text(
                        "SELECT status FROM interview_sessions WHERE id = :s"), {"s": fixed.session_id})
                assert status == "COMPLETED"
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_evaluation_request_carries_question_answer_and_rubric_only(database_url):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        received = []

        try:
            case = await seed_case(factory, STATEMENTS)
            fixed = await open_fixed_case(factory, case, user_id=await seed_user(factory),
                                         **_case_kwargs(GOOD_DIMS))
            original = fixed.evaluator.generate

            async def spy(messages, *, schema):
                received.append(messages[-1]["content"])
                return await original(messages, schema=schema)

            fixed.evaluator.generate = spy
            await fixed.submit(GOOD)
            payload = received[-1]
            for forbidden in ("quality_label", "GOOD", "POOR", "expected_score", "expected_band",
                              "人工参考", "预期分数"):
                assert forbidden not in payload, forbidden
            import json as _json
            body = _json.loads(payload)["input"]
            assert body["answer"] == GOOD
            assert body["rubric"] == RUBRIC
            assert POOR not in payload
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_duplicate_submission_reuses_one_evaluation(database_url):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            case = await seed_case(factory, STATEMENTS)
            fixed = await open_fixed_case(factory, case, user_id=await seed_user(factory),
                                         **_case_kwargs(GOOD_DIMS))
            first = await fixed.submit(GOOD)
            second = await fixed.submit(GOOD)
            assert second == first
            assert fixed.evaluator.calls == ["AdaptiveEvaluation"]
            assert await _count(factory, "interview_evaluations", "session_id = :s",
                                {"s": fixed.session_id}) == 1
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_failed_evaluation_persists_nothing_and_can_be_retried(database_url):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            case = await seed_case(factory, STATEMENTS)
            fixed = await open_fixed_case(factory, case, user_id=await seed_user(factory),
                                         **_case_kwargs(GOOD_DIMS, fail_once=True))
            with pytest.raises(Exception):
                await fixed.submit(GOOD)
            assert await _count(factory, "interview_evaluations", "session_id = :s",
                                {"s": fixed.session_id}) == 0
            async with factory() as db:
                statuses = (await db.execute(text(
                    "SELECT status FROM interview_answers WHERE session_id = :s"),
                    {"s": fixed.session_id})).fetchall()
                session_status = await db.scalar(text(
                    "SELECT status FROM interview_sessions WHERE id = :s"), {"s": fixed.session_id})
            assert [row[0] for row in statuses] == ["FAILED"]
            assert session_status == "IN_PROGRESS"

            # Retrying the same attempt is the documented repair, and it is not a
            # second evaluation: the failed attempt left no row behind.
            await fixed.submit(GOOD)
            assert await _count(factory, "interview_evaluations", "session_id = :s",
                                {"s": fixed.session_id}) == 1
        finally:
            await engine.dispose()
    asyncio.run(run())
