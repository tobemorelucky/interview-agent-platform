"""DEMO/REPLAY-only draft publication/recovery and route isolation tests."""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.router import (
    build_model_router, build_resume_profile_router, build_interview_router, EXTRACT_ROUTE, QUALITY_ROUTE,
)
from interview_api.modules.audit.models import AuditLog
from interview_api.modules.public_career.models import JobDescriptionVersion, JobRequirement, SourceSnapshot, Job
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.research.evidence import EvidenceCatalog
from interview_api.modules.public_career.research.evidence import EvidenceValidationError
from interview_api.modules.public_career.manual_import import ManualImportService
from interview_api.modules.public_career.review import PublicCareerReviewService
from interview_api.modules.users.models import User
from test_manual_public_career_import import document, scenario, no_external_network
from test_m2b_research_agent import job_response, router


async def prepared(factory):
    doc = document()
    item = await ManualImportService(factory).prepare(doc)
    async with factory() as db:
        snapshot = await db.get(SourceSnapshot, item["source_snapshot_id"])
        ids = list(EvidenceCatalog([snapshot]).spans)
    result = await PublicDataResearchAgent(factory, router([
        job_response(doc.company, ids), {"suitable": True, "risk_flags": []},
    ])).run(item["refresh_task_id"])
    assert result["status"] == "COMPLETED"
    async with factory() as db:
        user = User(email=f"demo-review-{uuid4().hex}@example.com", password_hash="DEMO_FIXTURE_ONLY", role="ADMIN")
        db.add(user)
        await db.commit()
        return result, user.id, item


def test_review_publish_atomic_idempotent_provenance_and_literal_topic(database_url):
    async def action(factory):
        result, reviewer, item = await prepared(factory)
        async with factory() as db:
            jd = await db.get(JobDescriptionVersion, result["jd_version_id"])
            raw, fingerprint = jd.raw_text, jd.content_fingerprint
            requirement = await db.scalar(select(JobRequirement).where(JobRequirement.job_description_version_id == jd.id))
            approved = await PublicCareerReviewService(db).publish_jd(jd.id, reviewer_id=reviewer, reason="DEMO explicit review",
                requirement_topics={str(requirement.id): "Python"})
            assert not approved["reused"]
            await db.rollback()
        async with factory() as db:
            jd = await db.get(JobDescriptionVersion, result["jd_version_id"])
            assert jd.publication_status == "WAITING_REVIEW"
            assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.resource_id == str(jd.id),
                AuditLog.action == "public_career.jd.publish")) == 0
            await PublicCareerReviewService(db).publish_jd(jd.id, reviewer_id=reviewer, reason="DEMO explicit review")
            await db.commit()
            retry = await PublicCareerReviewService(db).publish_jd(jd.id, reviewer_id=reviewer, reason="DEMO retry")
            assert retry["reused"] and jd.raw_text == raw and jd.content_fingerprint == fingerprint
            assert jd.metadata_json["manual_source"]["source_type"] == "community"
            assert (await db.get(Job, jd.job_id)).status == "ACTIVE"
            assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.resource_id == str(jd.id),
                AuditLog.action == "public_career.jd.publish")) == 1
    asyncio.run(scenario(database_url, action))


@pytest.mark.parametrize("failure,code", [
    ("user", "ADMIN_REQUIRED"), ("degree", "JD_CONSTRAINT_UNSUPPORTED"),
    ("topic", "REQUIREMENT_TOPIC_UNSUPPORTED"), ("source", "JD_EVIDENCE_INVALID"),
    ("closed", "CLOSED_JOB"),
])
def test_review_rejects_invalid_changes_without_publication(database_url, failure, code):
    async def action(factory):
        result, reviewer, item = await prepared(factory)
        async with factory() as db:
            jd = await db.get(JobDescriptionVersion, result["jd_version_id"])
            requirement = await db.scalar(select(JobRequirement).where(JobRequirement.job_description_version_id == jd.id))
            kwargs = dict(reviewer_id=reviewer, reason="DEMO review")
            if failure == "user": (await db.get(User, reviewer)).role = "USER"
            if failure == "degree": kwargs["matching_constraints"] = {"education_minimum": "博士"}
            if failure == "topic": kwargs["requirement_topics"] = {str(requirement.id): "Kafka"}
            if failure == "source": (await db.get(SourceSnapshot, item["source_snapshot_id"])).content_text = "DEMO tampered"
            if failure == "closed": (await db.get(Job, jd.job_id)).status = "CLOSED"
            await db.flush()
            with pytest.raises(AppError) as exc:
                await PublicCareerReviewService(db).publish_jd(jd.id, **kwargs)
            assert exc.value.code == code
            assert jd.publication_status == "WAITING_REVIEW"
            await db.rollback()
    asyncio.run(scenario(database_url, action))


def test_failed_manual_jd_explicit_recovery_preserves_attempts_and_completed_is_not_rerun(database_url):
    async def action(factory):
        doc = document()
        service = ManualImportService(factory)
        item = await service.prepare(doc)
        async with factory() as db:
            snapshot = await db.get(SourceSnapshot, item["source_snapshot_id"])
            ids = list(EvidenceCatalog([snapshot]).spans)
        failed = await PublicDataResearchAgent(factory, router([])).run(item["refresh_task_id"])
        assert failed["status"] == "FAILED"
        success = await PublicDataResearchAgent(factory, router([
            job_response(doc.company, ids), {"suitable": True, "risk_flags": []},
        ])).run(item["refresh_task_id"], force=True)
        assert success["status"] == "COMPLETED" and success["agent_run_id"] != failed["agent_run_id"]
        with pytest.raises(ValueError):
            await PublicDataResearchAgent(factory, router([])).run(item["refresh_task_id"], force=True)
    asyncio.run(scenario(database_url, action))


def test_public_research_thinking_policy_does_not_change_other_routes(monkeypatch):
    monkeypatch.setattr(settings, "answer_evaluation_thinking", None)  # isolate the Research override
    monkeypatch.setattr(settings, "interview_main_thinking", None)
    monkeypatch.setattr(settings, "llm_api_key", "DEMO_FIXTURE_NOT_REAL")
    monkeypatch.setattr(settings, "llm_base_url", "https://fixture.invalid")
    monkeypatch.setattr(settings, "llm_model", "DEMO_FIXTURE_MODEL")
    monkeypatch.setattr(settings, "public_research_thinking", "disabled")
    research = build_model_router()
    assert research.primary(EXTRACT_ROUTE).thinking_type == "disabled"
    assert research.primary(QUALITY_ROUTE).thinking_type == "disabled"
    assert build_resume_profile_router().primary("resume_profile").thinking_type == settings.resume_profile_thinking
    assert all(p.thinking_type is None for p in build_interview_router().routes.values())


def test_identity_formatting_whitespace_is_not_semantic_matching():
    snapshot = SourceSnapshot(id=100, source_id=100, content_text="中国移动研究院 AI 算法工程师岗")
    catalog = EvidenceCatalog([snapshot])
    PublicDataResearchAgent._validate_identity(catalog, list(catalog.spans), "中国移动研究院", "AI算法工程师")
    with pytest.raises(EvidenceValidationError):
        PublicDataResearchAgent._validate_identity(catalog, list(catalog.spans), "中国移动浙江嘉兴分公司")


def test_identity_does_not_join_unrelated_evidence_into_fabricated_name():
    snapshot = SourceSnapshot(id=100, source_id=100, content_text="中国某数据移动研究院")
    catalog = EvidenceCatalog([snapshot], span_chars=2)
    ids = [s.evidence_id for s in catalog.spans.values() if s.start in (0, 6, 8)]
    with pytest.raises(EvidenceValidationError):
        PublicDataResearchAgent._validate_identity(catalog, ids, "中国移动研究院")


def test_identity_can_join_adjacent_real_spans():
    snapshot = SourceSnapshot(id=100, source_id=100, content_text="中国移动研究院")
    catalog = EvidenceCatalog([snapshot], span_chars=2)
    PublicDataResearchAgent._validate_identity(catalog, list(catalog.spans), "中国移动研究院")


def test_manual_extractive_prompt_is_versioned_without_changing_live_collection_prompt():
    from interview_api.modules.public_career.research.prompts import PromptRegistry, ManualSourcePromptRegistry
    old = PromptRegistry().get("interview_extract")
    current = ManualSourcePromptRegistry().get("interview_extract")
    assert old.version == "1.0.0" and current.version == "1.0.1"
    assert "at most 20" in current.system and "Do not generate guidance" in current.system
    assert ManualSourcePromptRegistry().get("interview_extract", "1.0.0").system == old.system
    assert ManualSourcePromptRegistry().get("job_jd").version == "1.0.1"
    assert "BOTH" in ManualSourcePromptRegistry().get("job_jd").system
    assert PromptRegistry().get("job_jd").version == "1.0.0"
