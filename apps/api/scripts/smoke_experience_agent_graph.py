"""Smoke checks for Phase 4 Step 7B experience agent graph."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from sqlalchemy import delete, select

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
    "这是一篇腾讯后端二面面经。面试官先问 Redis 缓存穿透怎么解决，"
    "我回答可以使用布隆过滤器、缓存空值和参数校验。随后又问 Spring Boot 启动流程，"
    "我解释了自动配置、starter、Bean 生命周期以及项目中的排查经验。"
    "整体问题都是后端技术面试题，最后还追问了项目中的限流设计。"
) * 10


class FakeLLM:
    async def chat(self, messages: list[dict], **kwargs) -> str:
        system = messages[0]["content"]
        prompt = messages[-1]["content"]
        if "Reliability Agent" in system:
            return self._reliability(prompt)
        if "target_banks" in prompt:
            return self._routing(prompt)
        return self._extraction(prompt)

    def _extraction(self, prompt: str) -> str:
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
                    "round_name": "二面",
                    "experience_summary": "",
                    "questions": [],
                    "source_quality_note": "invalid by design",
                    "extraction_confidence": 0.8,
                },
                ensure_ascii=False,
            )
        questions = [
            {
                "question": "Redis 缓存穿透怎么解决？",
                "question_type": "TECHNICAL",
                "original_answer": "可以使用布隆过滤器、缓存空值和参数校验。",
                "standard_answer": "常见方案包括布隆过滤器、缓存空值、参数校验和限流。",
                "answer_source": "ORIGINAL",
                "evidence": "面试官先问 Redis 缓存穿透怎么解决",
                "confidence": 0.9,
            },
            {
                "question": "Spring Boot 启动流程是什么？",
                "question_type": "TECHNICAL",
                "original_answer": "我解释了自动配置、starter、Bean 生命周期。",
                "standard_answer": "可从启动入口、环境准备、自动配置和 Bean 生命周期回答。",
                "answer_source": "ORIGINAL",
                "evidence": "随后又问 Spring Boot 启动流程",
                "confidence": 0.88,
            },
        ]
        if "low-reliability" in prompt:
            questions = questions[:1]
        return json.dumps(
            {
                "is_interview_experience": True,
                "company": "腾讯",
                "position": "后端",
                "round_name": "二面",
                "experience_summary": "腾讯后端二面面经，包含 Redis 和 Spring Boot 面试题。",
                "questions": questions,
                "source_quality_note": "source has concrete interview questions",
                "extraction_confidence": 0.9,
            },
            ensure_ascii=False,
        )

    def _routing(self, prompt: str) -> str:
        question_results = [
            {
                "question_index": 0,
                "normalized_question": "Redis 缓存穿透怎么解决？",
                "job_direction": "BACKEND",
                "technical_categories": ["Redis", "Cache"],
                "question_type": "BASIC_KNOWLEDGE",
                "difficulty": "MEDIUM",
                "target_banks": ["backend", "redis"],
                "should_index": True,
                "routing_confidence": 0.82,
            }
        ]
        if "Spring Boot" in prompt:
            question_results.append(
                {
                    "question_index": 1,
                    "normalized_question": "Spring Boot 启动流程是什么？",
                    "job_direction": "BACKEND",
                    "technical_categories": ["Spring Boot"],
                    "question_type": "BASIC_KNOWLEDGE",
                    "difficulty": "MEDIUM",
                    "target_banks": ["backend", "spring"],
                    "should_index": True,
                    "routing_confidence": 0.8,
                }
            )
        if "low-reliability" in prompt:
            question_results[0]["routing_confidence"] = 0.42
        return json.dumps(
            {
                "overall_job_direction": "BACKEND",
                "company": "腾讯",
                "position": "后端",
                "question_results": question_results,
                "suggested_tags": ["后端", "Redis", "Spring Boot"],
                "routing_summary": "backend interview questions",
                "routing_confidence": min(
                    item["routing_confidence"] for item in question_results
                ),
            },
            ensure_ascii=False,
        )

    def _reliability(self, prompt: str) -> str:
        score = 0.82
        source_quality = 0.75
        spam = 0.1
        risks: list[str] = []
        quality = ["concrete_questions"]
        recommendation = "NEEDS_REVIEW"
        if "low-reliability" in prompt:
            score = 0.45
            source_quality = 0.42
            risks = ["low_source_quality"]
            quality = ["needs_manual_check"]
        return json.dumps(
            {
                "is_reliable": score >= 0.6,
                "reliability_score": score,
                "content_quality_score": 0.78,
                "source_quality_score": source_quality,
                "spam_risk_score": spam,
                "ad_or_training_risk": False,
                "outdated_risk": False,
                "hallucination_risk_note": None,
                "risk_flags": risks,
                "quality_flags": quality,
                "publish_recommendation": recommendation,
                "reason": "smoke reliability result",
            },
            ensure_ascii=False,
        )

    async def chat_stream(self, messages: list[dict], **kwargs):
        yield await self.chat(messages, **kwargs)


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


async def add_source(task_id: int, marker: str, name: str) -> ExperienceSourceItem:
    url = f"https://example.test/{marker}/{name}"
    normalized = normalize_url(url) or url
    item = ExperienceSourceItem(
        task_id=task_id,
        source_url=url,
        normalized_url_hash=hash_url(normalized),
        platform=None,
        title=f"{name} 面经",
        snippet="smoke Step 7B snippet",
        raw_text=f"{LONG_TEXT}\nmarker={name}",
        fetch_status="FETCHED",
    )
    return item


async def create_smoke_data() -> dict:
    marker = uuid.uuid4().hex[:12]
    async with async_session_factory() as db:
        admin = User(
            email=f"agent-graph-admin-{marker}@example.test",
            username=None,
            password_hash=hash_password("123456"),
            role="ADMIN",
            is_active=True,
        )
        user = User(
            email=f"agent-graph-user-{marker}@example.test",
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
            platforms_json=[],
            max_results=10,
            review_mode="MANUAL",
            found_url_count=4,
            fetched_count=4,
            status="FETCH_COMPLETED",
            progress=100,
        )
        db.add(task)
        await db.flush()

        sources = [
            await add_source(task.id, marker, "good"),
            await add_source(task.id, marker, "low-reliability"),
            await add_source(task.id, marker, "non-experience"),
            await add_source(task.id, marker, "validation-fail"),
        ]
        db.add_all(sources)
        await db.commit()
        return {
            "marker": marker,
            "admin_id": admin.id,
            "user_id": user.id,
            "task_id": task.id,
            "source_ids": {item.source_url.rsplit("/", 1)[-1]: item.id for item in sources},
        }


async def cleanup(data: dict) -> None:
    async with async_session_factory() as db:
        await db.execute(
            delete(InterviewExperience).where(InterviewExperience.task_id == data["task_id"])
        )
        await db.execute(
            delete(ExperienceCollectionTask).where(ExperienceCollectionTask.id == data["task_id"])
        )
        await db.execute(delete(User).where(User.id.in_([data["admin_id"], data["user_id"]])))
        await db.execute(
            delete(AuditLog).where(AuditLog.request_id.like(f"agent-graph-{data['marker']}%"))
        )
        await db.commit()


def make_client(user_id: int, role: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token(user_id, role)}"},
    )


async def post_extract(client: httpx.AsyncClient, data: dict, source_key: str, *, force: bool = False):
    return await client.post(
        f"/api/v1/admin/experience/sources/{data['source_ids'][source_key]}/extract",
        json={"force": force},
        headers={"X-Request-ID": f"agent-graph-{data['marker']}-{source_key}-{force}"},
        timeout=60,
    )


async def check_permissions(data: dict) -> None:
    async with make_client(data["user_id"], "USER") as client:
        response = await post_extract(client, data, "good")
    assert_true(response.status_code == 403, response.text)
    print_ok("normal user cannot run admin extraction")


async def check_lock(data: dict) -> None:
    source_id = data["source_ids"]["good"]
    lock_key = f"experience:source:{source_id}:extract"
    lock_token = await acquire_lock(lock_key, 30)
    assert_true(lock_token is not None, "failed to pre-acquire extract lock")
    try:
        async with make_client(data["admin_id"], "ADMIN") as client:
            response = await post_extract(client, data, "good")
        assert_true(response.status_code == 409, response.text)
    finally:
        await release_lock(lock_key, lock_token)
    print_ok("Redis lock protects single-source extraction")


async def check_waiting_review(data: dict) -> None:
    async with make_client(data["admin_id"], "ADMIN") as client:
        response = await post_extract(client, data, "good")
        second = await post_extract(client, data, "good")
    assert_true(response.status_code == 200, response.text)
    payload = response.json()["data"]
    assert_true(payload["review_status"] == "WAITING_REVIEW", str(payload))
    assert_true(payload["question_count"] == 2, str(payload))
    assert_true(payload["indexable_question_count"] == 2, str(payload))
    assert_true(payload["reliability_score"] == 0.82, str(payload))
    assert_true(payload["quality_gate_reasons"] == ["ready_for_admin_review"], str(payload))
    assert_true(second.json()["data"]["skipped"] is True, second.text)

    async with async_session_factory() as db:
        experience = (
            await db.execute(
                select(InterviewExperience).where(
                    InterviewExperience.source_item_id == data["source_ids"]["good"]
                )
            )
        ).scalar_one()
        assert_true(experience.routing_json is not None, "routing_json missing")
        assert_true(experience.reliability_json is not None, "reliability_json missing")
        assert_true(experience.quality_gate_json is not None, "quality_gate_json missing")
        questions = (
            await db.execute(
                select(InterviewQuestion).where(InterviewQuestion.experience_id == experience.id)
            )
        ).scalars().all()
        assert_true(len(questions) == 2, "question drafts missing")
        assert_true(all(q.should_index for q in questions), "should_index not persisted")
        assert_true(questions[0].technical_categories_json == ["Redis", "Cache"], "routing categories missing")
        steps = (
            await db.execute(
                select(ExperienceAgentStepRun.step_name)
                .join(ExperienceAgentRun, ExperienceAgentStepRun.agent_run_id == ExperienceAgentRun.id)
                .where(ExperienceAgentRun.source_item_id == data["source_ids"]["good"])
            )
        ).scalars().all()
        for step in [
            "extraction",
            "extraction_validation",
            "routing",
            "reliability",
            "quality_gate",
            "save_result",
        ]:
            assert_true(step in steps, f"missing step trace: {step}")
    print_ok("WAITING_REVIEW path stores routing, reliability, quality gate and step traces")


async def check_manual_and_rejected(data: dict) -> None:
    async with make_client(data["admin_id"], "ADMIN") as client:
        low = await post_extract(client, data, "low-reliability")
        non_exp = await post_extract(client, data, "non-experience")
        invalid = await post_extract(client, data, "validation-fail")

    assert_true(low.status_code == 200, low.text)
    low_payload = low.json()["data"]
    assert_true(low_payload["review_status"] == "NEEDS_MANUAL_CHECK", str(low_payload))
    assert_true("reliability_needs_manual_check" in low_payload["quality_gate_reasons"], str(low_payload))

    assert_true(non_exp.status_code == 200, non_exp.text)
    non_payload = non_exp.json()["data"]
    assert_true(non_payload["extract_status"] == "NOT_EXPERIENCE", str(non_payload))
    assert_true(non_payload["review_status"] == "REJECTED", str(non_payload))

    assert_true(invalid.status_code == 200, invalid.text)
    invalid_payload = invalid.json()["data"]
    assert_true(invalid_payload["extract_status"] == "EXTRACT_FAILED", str(invalid_payload))
    assert_true(invalid_payload["error_message"], str(invalid_payload))
    print_ok("NEEDS_MANUAL_CHECK, NOT_EXPERIENCE and validation failure paths work")


async def check_force_and_audit(data: dict) -> None:
    async with make_client(data["admin_id"], "ADMIN") as client:
        forced = await post_extract(client, data, "good", force=True)
    assert_true(forced.status_code == 200, forced.text)
    assert_true(forced.json()["data"]["skipped"] is False, forced.text)
    async with async_session_factory() as db:
        experiences = (
            await db.execute(
                select(InterviewExperience).where(
                    InterviewExperience.source_item_id == data["source_ids"]["good"]
                )
            )
        ).scalars().all()
        assert_true(len(experiences) == 1, "force should replace previous draft")
        logs = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "experience.source.extract",
                    AuditLog.request_id.like(f"agent-graph-{data['marker']}%"),
                )
            )
        ).scalars().all()
        assert_true(len(logs) >= 5, f"expected audit logs, got {len(logs)}")
        metadata = [log.metadata_json or {} for log in logs if log.status == "SUCCESS"]
        assert_true(
            any("reliability_score" in item for item in metadata),
            "audit metadata missing reliability_score",
        )
    print_ok("force rerun replaces draft and audit metadata includes quality fields")


async def main() -> None:
    await ensure_redis()
    data = await create_smoke_data()
    original_service = experience_router.ExperienceAgentService
    try:
        experience_router.ExperienceAgentService = FakeExperienceAgentService
        await check_permissions(data)
        await check_lock(data)
        await check_waiting_review(data)
        await check_manual_and_rejected(data)
        await check_force_and_audit(data)
        print("[OK] experience agent graph smoke completed")
    finally:
        experience_router.ExperienceAgentService = original_service
        await cleanup(data)


if __name__ == "__main__":
    asyncio.run(main())
