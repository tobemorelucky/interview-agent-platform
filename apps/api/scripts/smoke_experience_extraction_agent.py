"""Smoke checks for Phase 4 Step 7A Extraction Agent."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from sqlalchemy import delete, select

from interview_api.core.config import settings
from interview_api.core.locks import acquire_lock, release_lock
from interview_api.core.rate_limit import get_redis_client
from interview_api.core.security import create_access_token, hash_password
from interview_api.infrastructure.db.session import async_session_factory
from interview_api.main import app
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.experience import router as experience_router
from interview_api.modules.experience.agents.service import ExperienceAgentService
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


LONG_TEXT = (
    "这是一篇腾讯后端一面面经。面试官问了 Redis 缓存穿透怎么解决？"
    "我回答可以使用布隆过滤器和空值缓存。还问了 Spring Boot 启动流程。"
    "整体偏项目和八股，最后问了为什么选择后端岗位。"
) * 8
SHORT_TEXT = "太短了"


class FakeLLM:
    async def chat(self, messages: list[dict], **kwargs) -> str:
        system = messages[0]["content"]
        prompt = messages[-1]["content"]
        if "Reliability Agent" in system:
            return json.dumps(
                {
                    "is_reliable": True,
                    "reliability_score": 0.72,
                    "content_quality_score": 0.74,
                    "source_quality_score": 0.7,
                    "spam_risk_score": 0.1,
                    "ad_or_training_risk": False,
                    "outdated_risk": False,
                    "hallucination_risk_note": None,
                    "risk_flags": [],
                    "quality_flags": ["legacy_smoke"],
                    "publish_recommendation": "NEEDS_REVIEW",
                    "reason": "legacy smoke reliability",
                },
                ensure_ascii=False,
            )
        if "target_banks" in prompt:
            return json.dumps(
                {
                    "overall_job_direction": "BACKEND",
                    "company": "腾讯",
                    "position": "后端",
                    "question_results": [
                        {
                            "question_index": 0,
                            "normalized_question": "Redis 缓存穿透怎么解决？",
                            "job_direction": "BACKEND",
                            "technical_categories": ["Redis"],
                            "question_type": "BASIC_KNOWLEDGE",
                            "difficulty": "MEDIUM",
                            "target_banks": ["backend", "redis"],
                            "should_index": True,
                            "routing_confidence": 0.75,
                        }
                    ],
                    "suggested_tags": ["后端", "Redis"],
                    "routing_summary": "legacy smoke routing",
                    "routing_confidence": 0.75,
                },
                ensure_ascii=False,
            )
        if "non-experience" in prompt:
            return json.dumps(
                {
                    "is_interview_experience": False,
                    "company": None,
                    "position": None,
                    "round_name": None,
                    "experience_summary": "",
                    "questions": [],
                    "source_quality_note": "not interview content",
                    "extraction_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        if "validation-fail" in prompt:
            return json.dumps(
                {
                    "is_interview_experience": True,
                    "company": "腾讯",
                    "position": "后端",
                    "round_name": "一面",
                    "experience_summary": "",
                    "questions": [],
                    "source_quality_note": "invalid by design",
                    "extraction_confidence": 0.8,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "is_interview_experience": True,
                "company": "腾讯",
                "position": "后端",
                "round_name": "一面",
                "experience_summary": "腾讯后端一面，主要考察 Redis 和 Spring Boot。",
                "questions": [
                    {
                        "question": "Redis 缓存穿透怎么解决？",
                        "question_type": "TECHNICAL",
                        "original_answer": "可以使用布隆过滤器和空值缓存。",
                        "standard_answer": "常见方案包括布隆过滤器、缓存空值、参数校验和限流。",
                        "answer_source": "HYBRID",
                        "evidence": "面试官问了 Redis 缓存穿透怎么解决？我回答可以使用布隆过滤器和空值缓存。",
                        "confidence": 0.92,
                    }
                ],
                "source_quality_note": "source has concrete interview questions",
                "extraction_confidence": 0.9,
            },
            ensure_ascii=False,
        )


class FakeExperienceAgentService(ExperienceAgentService):
    def __init__(self, db):
        super().__init__(db, llm=FakeLLM())


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def print_ok(message: str) -> None:
    print(f"[OK] {message}")


async def ensure_redis() -> None:
    await get_redis_client().ping()


def token(user_id: int, role: str) -> str:
    return create_access_token(user_id, role)


def source_url(marker: str, name: str) -> str:
    return f"https://example.test/{marker}/{name}"


async def add_source(
    task_id: int,
    marker: str,
    name: str,
    *,
    fetch_status: str = "FETCHED",
    raw_text: str = LONG_TEXT,
) -> ExperienceSourceItem:
    url = source_url(marker, name)
    normalized = normalize_url(url) or url
    return ExperienceSourceItem(
        task_id=task_id,
        source_url=url,
        normalized_url_hash=hash_url(normalized),
        platform=None,
        title=f"{name} 面经",
        snippet="smoke extraction snippet",
        raw_text=raw_text,
        fetch_status=fetch_status,
    )


async def create_smoke_data() -> dict:
    marker = uuid.uuid4().hex[:12]
    async with async_session_factory() as db:
        admin = User(
            email=f"extract-admin-{marker}@example.test",
            username=None,
            password_hash=hash_password("123456"),
            role="ADMIN",
            is_active=True,
        )
        db.add(admin)
        await db.flush()

        task = ExperienceCollectionTask(
            created_by=admin.id,
            search_scope="JOB",
            time_window_hours=24,
            job_keywords_json=["后端"],
            company_keywords_json=[],
            platforms_json=[],
            max_results=10,
            review_mode="MANUAL",
            found_url_count=6,
            fetched_count=5,
            status="FETCH_COMPLETED",
            progress=100,
        )
        db.add(task)
        await db.flush()

        sources = [
            await add_source(task.id, marker, "good"),
            await add_source(task.id, marker, "not-fetched", fetch_status="DISCOVERED"),
            await add_source(task.id, marker, "short", raw_text=SHORT_TEXT),
            await add_source(task.id, marker, "non-experience"),
            await add_source(task.id, marker, "validation-fail"),
            await add_source(task.id, marker, "no-llm"),
        ]
        db.add_all(sources)
        await db.commit()
        return {
            "marker": marker,
            "admin_id": admin.id,
            "task_id": task.id,
            "source_ids": {item.source_url.rsplit("/", 1)[-1]: item.id for item in sources},
        }


async def cleanup(data: dict) -> None:
    marker = data["marker"]
    async with async_session_factory() as db:
        await db.execute(
            delete(InterviewExperience).where(InterviewExperience.task_id == data["task_id"])
        )
        await db.execute(
            delete(ExperienceCollectionTask).where(ExperienceCollectionTask.id == data["task_id"])
        )
        await db.execute(delete(User).where(User.id == data["admin_id"]))
        await db.execute(
            delete(AuditLog).where(AuditLog.request_id.like(f"extract-smoke-{marker}%"))
        )
        await db.commit()


def make_client(data: dict) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token(data['admin_id'], 'ADMIN')}"},
    )


async def post_extract(
    client: httpx.AsyncClient,
    data: dict,
    source_key: str,
    *,
    force: bool = False,
    suffix: str | None = None,
) -> httpx.Response:
    marker = data["marker"]
    return await client.post(
        f"/api/v1/admin/experience/sources/{data['source_ids'][source_key]}/extract",
        json={"force": force},
        headers={"X-Request-ID": f"extract-smoke-{marker}-{suffix or source_key}"},
        timeout=60,
    )


async def check_llm_not_configured(data: dict) -> None:
    old_key = settings.llm_api_key
    old_model = settings.llm_model
    settings.llm_api_key = ""
    settings.llm_model = ""
    try:
        async with make_client(data) as client:
            response = await post_extract(client, data, "no-llm")
        assert_true(response.status_code == 400, response.text)
        assert_true(response.json()["error"]["code"] == "LLM_NOT_CONFIGURED", response.text)
    finally:
        settings.llm_api_key = old_key
        settings.llm_model = old_model
    print_ok("LLM_NOT_CONFIGURED is returned clearly")


async def check_rejections(data: dict) -> None:
    async with make_client(data) as client:
        not_fetched = await post_extract(client, data, "not-fetched")
        short = await post_extract(client, data, "short")
    assert_true(not_fetched.status_code == 400, not_fetched.text)
    assert_true(short.status_code == 400, short.text)
    print_ok("non-FETCHED and short raw_text sources are rejected")


async def check_lock(data: dict) -> None:
    source_id = data["source_ids"]["good"]
    lock_key = f"experience:source:{source_id}:extract"
    lock_token = await acquire_lock(lock_key, 30)
    assert_true(lock_token is not None, "failed to pre-acquire extract lock")
    try:
        async with make_client(data) as client:
            response = await post_extract(client, data, "good", suffix="locked")
        assert_true(response.status_code == 409, response.text)
        assert_true(response.json()["error"]["code"] == "RESOURCE_LOCKED", response.text)
    finally:
        await release_lock(lock_key, lock_token)
    print_ok("Redis lock blocks duplicate extraction")


async def check_success_and_idempotency(data: dict) -> None:
    async with make_client(data) as client:
        response = await post_extract(client, data, "good", suffix="good-first")
        second = await post_extract(client, data, "good", suffix="good-second")
        forced = await post_extract(client, data, "good", force=True, suffix="good-force")

    assert_true(response.status_code == 200, response.text)
    first_data = response.json()["data"]
    assert_true(first_data["is_interview_experience"] is True, str(first_data))
    assert_true(first_data["question_count"] == 1, str(first_data))
    assert_true(first_data["extract_status"] == "EXTRACTED", str(first_data))

    assert_true(second.status_code == 200, second.text)
    second_data = second.json()["data"]
    assert_true(second_data["skipped"] is True, str(second_data))
    assert_true(second_data["agent_run_id"] is None, str(second_data))

    assert_true(forced.status_code == 200, forced.text)
    forced_data = forced.json()["data"]
    assert_true(forced_data["skipped"] is False, str(forced_data))
    assert_true(forced_data["agent_run_id"] is not None, str(forced_data))

    async with async_session_factory() as db:
        experiences = (
            await db.execute(
                select(InterviewExperience).where(
                    InterviewExperience.source_item_id == data["source_ids"]["good"]
                )
            )
        ).scalars().all()
        assert_true(len(experiences) == 1, f"force rerun should replace draft, got {len(experiences)}")
        experience = experiences[0]
        assert_true(
            experience.review_status in {"WAITING_REVIEW", "NEEDS_MANUAL_CHECK"},
            "experience review_status mismatch",
        )
        assert_true(experience.extraction_confidence == 0.9, "extraction_confidence mismatch")
        questions = (
            await db.execute(
                select(InterviewQuestion).where(InterviewQuestion.experience_id == experience.id)
            )
        ).scalars().all()
        assert_true(len(questions) == 1, "question draft not saved")
        question = questions[0]
        assert_true(question.original_answer is not None, "original_answer missing")
        assert_true(question.evidence is not None, "evidence missing")
        assert_true(question.question_type == "BASIC_KNOWLEDGE", "question_type mismatch")
        steps = (
            await db.execute(
                select(ExperienceAgentStepRun).join(
                    ExperienceAgentRun,
                    ExperienceAgentStepRun.agent_run_id == ExperienceAgentRun.id,
                ).where(ExperienceAgentRun.source_item_id == data["source_ids"]["good"])
            )
        ).scalars().all()
        assert_true(len(steps) >= 6, "expected step traces for first and forced runs")
    print_ok("valid extraction saves review draft experience/question and is idempotent")


async def check_non_experience_and_validation_failed(data: dict) -> None:
    async with make_client(data) as client:
        non_exp = await post_extract(client, data, "non-experience")
        invalid = await post_extract(client, data, "validation-fail")

    assert_true(non_exp.status_code == 200, non_exp.text)
    non_data = non_exp.json()["data"]
    assert_true(non_data["is_interview_experience"] is False, str(non_data))
    assert_true(non_data["question_count"] == 0, str(non_data))
    assert_true(non_data["extract_status"] == "NOT_EXPERIENCE", str(non_data))

    assert_true(invalid.status_code == 200, invalid.text)
    invalid_data = invalid.json()["data"]
    assert_true(invalid_data["extract_status"] == "EXTRACT_FAILED", str(invalid_data))
    assert_true(invalid_data["error_message"], str(invalid_data))

    async with async_session_factory() as db:
        non_questions = (
            await db.execute(
                select(InterviewQuestion)
                .join(InterviewExperience, InterviewQuestion.experience_id == InterviewExperience.id)
                .where(InterviewExperience.source_item_id == data["source_ids"]["non-experience"])
            )
        ).scalars().all()
        assert_true(len(non_questions) == 0, "non-experience should not create questions")
        invalid_run = (
            await db.execute(
                select(ExperienceAgentRun)
                .where(ExperienceAgentRun.source_item_id == data["source_ids"]["validation-fail"])
                .order_by(ExperienceAgentRun.id.desc())
                .limit(1)
            )
        ).scalar_one()
        assert_true(invalid_run.status == "VALIDATION_FAILED", "invalid output should mark run VALIDATION_FAILED")
    print_ok("non-experience and validation failed branches behave correctly")


async def check_audit_logs(data: dict) -> None:
    async with async_session_factory() as db:
        logs = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "experience.source.extract",
                    AuditLog.request_id.like(f"extract-smoke-{data['marker']}%"),
                )
            )
        ).scalars().all()
        assert_true(len(logs) >= 8, f"expected extract audit logs, got {len(logs)}")
    print_ok("audit logs record experience.source.extract")


async def main() -> None:
    await ensure_redis()
    data = await create_smoke_data()
    original_service = experience_router.ExperienceAgentService
    try:
        await check_llm_not_configured(data)
        await check_rejections(data)
        experience_router.ExperienceAgentService = FakeExperienceAgentService
        await check_lock(data)
        await check_success_and_idempotency(data)
        await check_non_experience_and_validation_failed(data)
        await check_audit_logs(data)
        print("[OK] experience extraction agent smoke completed")
    finally:
        experience_router.ExperienceAgentService = original_service
        await cleanup(data)


if __name__ == "__main__":
    asyncio.run(main())
