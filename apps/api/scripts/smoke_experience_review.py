"""Smoke checks for Phase 4 Step 8 admin experience review APIs."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select

from interview_api.core.rate_limit import get_redis_client
from interview_api.core.security import create_access_token, hash_password
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.main import app
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.experience.models import (
    ExperienceAgentRun,
    ExperienceAgentStepRun,
    ExperienceCollectionTask,
    ExperienceSourceItem,
    InterviewExperience,
    InterviewQuestion,
)
from interview_api.modules.experience.search.url_utils import hash_url, normalize_url
from interview_api.modules.users.models import User


RAW_TEXT = (
    "腾讯后端二面面经。面试官问了 Redis 缓存穿透、Spring Boot 启动流程、"
    "限流设计和项目排障。回答中包含布隆过滤器、缓存空值、自动配置、Bean 生命周期等内容。"
) * 40


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def print_ok(message: str) -> None:
    print(f"[OK] {message}")


def token(user_id: int, role: str) -> str:
    return create_access_token(user_id, role)


async def ensure_redis() -> None:
    await get_redis_client().ping()


async def create_source(task_id: int, marker: str, name: str) -> ExperienceSourceItem:
    url = f"https://example.test/{marker}/{name}"
    normalized = normalize_url(url) or url
    return ExperienceSourceItem(
        task_id=task_id,
        source_url=url,
        normalized_url_hash=hash_url(normalized),
        platform="牛客",
        title=f"{name} 腾讯后端面经",
        snippet="腾讯后端二面面经摘要",
        raw_text=RAW_TEXT,
        fetch_status="FETCHED",
        extract_status="EXTRACTED",
    )


async def create_experience(task_id: int, source: ExperienceSourceItem, status: str) -> InterviewExperience:
    experience = InterviewExperience(
        source_item_id=source.id,
        task_id=task_id,
        source_url=source.source_url,
        platform=source.platform,
        company="腾讯",
        position="后端",
        job_direction="BACKEND",
        interview_round="二面",
        summary="腾讯后端二面，重点考 Redis、Spring Boot 和限流设计。",
        content_text=RAW_TEXT,
        tags_json=["后端", "Redis"],
        reliability_score=0.82,
        extraction_confidence=0.9,
        extraction_output_json={"is_interview_experience": True, "questions": [1, 2]},
        routing_json={"overall_job_direction": "BACKEND"},
        reliability_json={
            "reliability_score": 0.82,
            "risk_flags": ["low_source_quality"],
            "quality_flags": ["concrete_questions"],
        },
        quality_gate_json={"review_status": status, "reasons": ["ready_for_admin_review"]},
        quality_flags_json=["concrete_questions"],
        review_status=status,
    )
    return experience


async def add_questions(experience: InterviewExperience) -> list[InterviewQuestion]:
    questions = [
        InterviewQuestion(
            experience_id=experience.id,
            question="Redis 缓存穿透怎么解决？",
            original_answer="使用布隆过滤器和缓存空值。",
            standard_answer="可从布隆过滤器、缓存空值、参数校验和限流回答。",
            answer_source="HYBRID",
            evidence="面试官问了 Redis 缓存穿透",
            question_type="BASIC_KNOWLEDGE",
            category="Redis",
            difficulty="MEDIUM",
            company=experience.company,
            position=experience.position,
            source_url=experience.source_url,
            reliability_score=experience.reliability_score,
            confidence=0.9,
            technical_categories_json=["Redis"],
            should_index=True,
            tags_json=["backend", "redis"],
            routing_json={"question_index": 0},
            review_status=experience.review_status,
            index_status="NOT_INDEXED",
        ),
        InterviewQuestion(
            experience_id=experience.id,
            question="Spring Boot 启动流程是什么？",
            standard_answer="可从启动入口、环境准备、自动配置和 Bean 生命周期回答。",
            answer_source="LLM_GENERATED",
            evidence="随后又问 Spring Boot 启动流程",
            question_type="BASIC_KNOWLEDGE",
            category="Spring Boot",
            difficulty="MEDIUM",
            company=experience.company,
            position=experience.position,
            source_url=experience.source_url,
            reliability_score=experience.reliability_score,
            confidence=0.86,
            technical_categories_json=["Spring Boot"],
            should_index=False,
            tags_json=["backend", "spring"],
            routing_json={"question_index": 1},
            review_status=experience.review_status,
            index_status="NOT_INDEXED",
        ),
    ]
    return questions


async def create_smoke_data() -> dict:
    marker = uuid.uuid4().hex[:12]
    async with async_session_factory() as db:
        admin = User(
            email=f"review-admin-{marker}@example.test",
            username=None,
            password_hash=hash_password("123456"),
            role="ADMIN",
            is_active=True,
        )
        user = User(
            email=f"review-user-{marker}@example.test",
            username=None,
            password_hash=hash_password("123456"),
            role="USER",
            is_active=True,
        )
        db.add_all([admin, user])
        await db.flush()

        task = ExperienceCollectionTask(
            created_by=admin.id,
            search_scope="JOB",
            time_window_hours=24,
            job_keywords_json=["后端"],
            company_keywords_json=[],
            platforms_json=["牛客"],
            max_results=10,
            review_mode="MANUAL",
            found_url_count=3,
            fetched_count=3,
            extracted_count=3,
            question_count=6,
            status="WAITING_REVIEW",
            progress=100,
        )
        db.add(task)
        await db.flush()

        sources = [
            await create_source(task.id, marker, "approve"),
            await create_source(task.id, marker, "reject"),
            await create_source(task.id, marker, "rerun"),
        ]
        db.add_all(sources)
        await db.flush()

        experiences = [
            await create_experience(task.id, sources[0], "WAITING_REVIEW"),
            await create_experience(task.id, sources[1], "NEEDS_MANUAL_CHECK"),
            await create_experience(task.id, sources[2], "WAITING_REVIEW"),
        ]
        db.add_all(experiences)
        await db.flush()

        for experience in experiences:
            db.add_all(await add_questions(experience))

        run = ExperienceAgentRun(
            source_item_id=sources[0].id,
            task_id=task.id,
            graph_name="experience_extraction_graph",
            graph_version="v1",
            status="SUCCEEDED",
            input_hash="review-smoke",
            output_json={"quality_gate_result": {"review_status": "WAITING_REVIEW"}},
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        db.add(run)
        await db.flush()
        db.add_all(
            [
                ExperienceAgentStepRun(
                    agent_run_id=run.id,
                    step_name="extraction",
                    status="SUCCEEDED",
                    output_json={"question_count": 2},
                    latency_ms=10,
                ),
                ExperienceAgentStepRun(
                    agent_run_id=run.id,
                    step_name="quality_gate",
                    status="SUCCEEDED",
                    output_json={"review_status": "WAITING_REVIEW"},
                    latency_ms=5,
                ),
            ]
        )
        await db.commit()
        return {
            "marker": marker,
            "admin_id": admin.id,
            "user_id": user.id,
            "task_id": task.id,
            "source_ids": [source.id for source in sources],
            "experience_ids": [experience.id for experience in experiences],
        }


async def cleanup(data: dict) -> None:
    async with async_session_factory() as db:
        await db.execute(
            delete(InterviewExperience).where(InterviewExperience.id.in_(data["experience_ids"]))
        )
        await db.execute(
            delete(ExperienceCollectionTask).where(ExperienceCollectionTask.id == data["task_id"])
        )
        await db.execute(delete(User).where(User.id.in_([data["admin_id"], data["user_id"]])))
        await db.execute(
            delete(AuditLog).where(AuditLog.request_id.like(f"review-smoke-{data['marker']}%"))
        )
        await db.commit()


def make_client(user_id: int, role: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token(user_id, role)}"},
    )


async def check_permission(data: dict) -> None:
    async with make_client(data["user_id"], "USER") as client:
        response = await client.get(
            "/api/v1/admin/experience/review/items",
            headers={"X-Request-ID": f"review-smoke-{data['marker']}-user-list"},
        )
    assert_true(response.status_code == 403, response.text)
    print_ok("normal user cannot access review APIs")


async def check_list_and_detail(data: dict) -> None:
    async with make_client(data["admin_id"], "ADMIN") as client:
        response = await client.get(
            "/api/v1/admin/experience/review/items",
            params={"review_status": "WAITING_REVIEW", "keyword": "腾讯"},
            headers={"X-Request-ID": f"review-smoke-{data['marker']}-list"},
        )
        detail = await client.get(
            f"/api/v1/admin/experience/review/items/{data['experience_ids'][0]}",
            headers={"X-Request-ID": f"review-smoke-{data['marker']}-detail"},
        )
    assert_true(response.status_code == 200, response.text)
    payload = response.json()["data"]
    assert_true(payload["total"] >= 2, str(payload))
    assert_true(any(item["experience_id"] == data["experience_ids"][0] for item in payload["items"]), str(payload))
    assert_true(detail.status_code == 200, detail.text)
    detail_data = detail.json()["data"]
    assert_true(len(detail_data["questions"]) == 2, str(detail_data))
    assert_true(detail_data["experience"]["reliability_json"]["reliability_score"] == 0.82, str(detail_data))
    assert_true(detail_data["experience"]["quality_gate_json"]["review_status"] == "WAITING_REVIEW", str(detail_data))
    assert_true(len(detail_data["agent"]["step_runs"]) == 2, str(detail_data))
    assert_true(len(detail_data["experience"]["raw_text_preview"]) <= 2000, "raw_text_preview too long")
    print_ok("review list and detail return questions, agent outputs and bounded preview")


async def check_patch_experience_and_question(data: dict) -> int:
    async with make_client(data["admin_id"], "ADMIN") as client:
        update_exp = await client.patch(
            f"/api/v1/admin/experience/review/items/{data['experience_ids'][0]}",
            json={
                "company": "腾讯云",
                "position": "后端开发",
                "round_name": "技术二面",
                "summary": "人工修订后的摘要",
                "review_note": "已人工校对",
            },
            headers={"X-Request-ID": f"review-smoke-{data['marker']}-exp-update"},
        )
        detail = update_exp.json()["data"]
        question_id = detail["questions"][1]["question_id"]
        update_question = await client.patch(
            f"/api/v1/admin/experience/review/questions/{question_id}",
            json={
                "question": "请说明 Spring Boot 启动流程。",
                "standard_answer": "人工修订后的标准答案",
                "technical_categories": ["Spring Boot", "Java"],
                "difficulty": "HARD",
                "should_index": True,
                "review_status": "WAITING_REVIEW",
            },
            headers={"X-Request-ID": f"review-smoke-{data['marker']}-question-update"},
        )
    assert_true(update_exp.status_code == 200, update_exp.text)
    updated_exp = update_exp.json()["data"]["experience"]
    assert_true(updated_exp["company"] == "腾讯云", str(updated_exp))
    assert_true(updated_exp["review_note"] == "已人工校对", str(updated_exp))
    assert_true(update_question.status_code == 200, update_question.text)
    updated_question = update_question.json()["data"]
    assert_true(updated_question["difficulty"] == "HARD", str(updated_question))
    assert_true(updated_question["technical_categories"] == ["Spring Boot", "Java"], str(updated_question))
    print_ok("experience and question patch APIs update editable fields")
    return question_id


async def check_approve_reject_rerun(data: dict) -> None:
    async with make_client(data["admin_id"], "ADMIN") as client:
        approve = await client.post(
            f"/api/v1/admin/experience/review/items/{data['experience_ids'][0]}/approve",
            headers={"X-Request-ID": f"review-smoke-{data['marker']}-approve"},
        )
        reject = await client.post(
            f"/api/v1/admin/experience/review/items/{data['experience_ids'][1]}/reject",
            json={"reason": "内容可信度不足"},
            headers={"X-Request-ID": f"review-smoke-{data['marker']}-reject"},
        )
        rerun = await client.post(
            f"/api/v1/admin/experience/review/items/{data['experience_ids'][2]}/request-rerun",
            json={"reason": "抽取结果不完整", "force": True},
            headers={"X-Request-ID": f"review-smoke-{data['marker']}-rerun"},
        )
    assert_true(approve.status_code == 200, approve.text)
    assert_true(reject.status_code == 200, reject.text)
    assert_true(rerun.status_code == 200, rerun.text)

    async with async_session_factory() as db:
        approved_exp = await db.get(InterviewExperience, data["experience_ids"][0])
        rejected_exp = await db.get(InterviewExperience, data["experience_ids"][1])
        rerun_exp = await db.get(InterviewExperience, data["experience_ids"][2])
        assert_true(approved_exp.review_status == "APPROVED", "experience not approved")
        approved_questions = (
            await db.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.experience_id == approved_exp.id,
                    InterviewQuestion.should_index.is_(True),
                )
            )
        ).scalars().all()
        assert_true(all(question.review_status == "APPROVED" for question in approved_questions), "indexable questions not approved")
        assert_true(rejected_exp.review_status == "REJECTED", "experience not rejected")
        assert_true(rejected_exp.reject_reason == "内容可信度不足", "reject reason missing")
        rejected_questions = (
            await db.execute(
                select(InterviewQuestion).where(InterviewQuestion.experience_id == rejected_exp.id)
            )
        ).scalars().all()
        assert_true(all(question.review_status == "REJECTED" for question in rejected_questions), "questions not rejected")
        assert_true(rerun_exp.review_status == "NEEDS_RERUN", "experience not marked rerun")
        source = await db.get(ExperienceSourceItem, rerun_exp.source_item_id)
        assert_true(source.extract_status == "NEEDS_RERUN", "source not marked rerun")
    print_ok("approve, reject and request-rerun transitions work")


async def check_audit_logs(data: dict) -> None:
    async with async_session_factory() as db:
        logs = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.request_id.like(f"review-smoke-{data['marker']}%"),
                    AuditLog.action.in_(
                        [
                            "experience.review.update",
                            "experience.review.question_update",
                            "experience.review.approve",
                            "experience.review.reject",
                            "experience.review.request_rerun",
                        ]
                    ),
                )
            )
        ).scalars().all()
        actions = {log.action for log in logs}
    assert_true("experience.review.update" in actions, f"missing update audit: {actions}")
    assert_true("experience.review.question_update" in actions, f"missing question audit: {actions}")
    assert_true("experience.review.approve" in actions, f"missing approve audit: {actions}")
    assert_true("experience.review.reject" in actions, f"missing reject audit: {actions}")
    assert_true("experience.review.request_rerun" in actions, f"missing rerun audit: {actions}")
    print_ok("audit logs record review actions")


async def main() -> None:
    await ensure_redis()
    data = await create_smoke_data()
    try:
        await check_permission(data)
        await check_list_and_detail(data)
        await check_patch_experience_and_question(data)
        await check_approve_reject_rerun(data)
        await check_audit_logs(data)
        print("[OK] experience review smoke completed")
    finally:
        await cleanup(data)


if __name__ == "__main__":
    asyncio.run(main())
