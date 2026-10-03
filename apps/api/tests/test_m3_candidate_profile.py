import asyncio
from copy import deepcopy
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
import interview_api.modules.models  # noqa: F401
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.exceptions import AppError
from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, RESUME_PROFILE_ROUTE
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.evidence import digest, evidence_rows, ensure_resume_evidence, resolve_and_validate
from interview_api.modules.candidate_profile.models import (
    CandidateProfile, CandidateProfileVersion, CandidateFact, CandidateFactEvidence,
    ResumeEvidence, ProjectExperience, WorkExperience, EducationExperience, UserSkill,
    ProfileCorrection, ProfileGeneration, UserConfirmedEvidence,
)
from interview_api.modules.candidate_profile.schemas import CandidateProfileDelta, CorrectionRequest
from interview_api.modules.candidate_profile.service import CandidateProfileService
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.resume.models import Resume, ResumeReport
from interview_api.modules.resume.parser import ResumeParser
from interview_api.modules.resume.service import ResumeService
from interview_api.modules.users.models import User

RAW = "Demo candidate — local fixture, not real personal data\n技能\nPython Redis\n项目经历\n订单系统：使用 Python 和 Redis 实现缓存。\n教育背景\n示例大学 计算机科学 本科\n工作经历\n示例公司 Python 开发\n实习经历\n示例实验室 Redis 缓存实习\n"


async def seed(factory, raw=RAW):
    async with factory() as db:
        user = User(email=f"m3-{uuid4().hex}@example.com", password_hash="not-a-login-password")
        db.add(user)
        await db.flush()
        resume = Resume(user_id=user.id, filename="demo_fixture.txt", storage_key=f"fixture/{uuid4().hex}.txt", file_type="txt", status="COMPLETED", raw_text=raw)
        db.add(resume)
        await db.flush()
        db.add(ResumeReport(resume_id=resume.id, user_id=user.id, summary_json={"fixture": True, "skills": ["Python", "Redis"]}))
        await db.commit()
        return user.id, resume.id


def response(resume_id, raw=RAW):
    rows = evidence_rows(resume_id, raw)
    def ref(fragment):
        return next(row["evidence_key"] for row in rows if fragment in row["excerpt"])
    def named(label, statement):
        return {"label": label, "statement": statement, "evidence_ids": [ref(statement)]}
    return dict(candidate_summary={"statement": raw.splitlines()[0], "evidence_ids": [rows[0]["evidence_key"]]},
                skills=[dict(**named("Redis", "订单系统：使用 Python 和 Redis 实现缓存。"), evidence_state="DEMONSTRATED")],
                projects=[named("订单系统", "订单系统：使用 Python 和 Redis 实现缓存。")],
                education=[named("示例大学", "示例大学 计算机科学 本科")],
                work_experience=[named("示例公司", "示例公司 Python 开发")],
                internship_experience=[named("示例实验室", "示例实验室 Redis 缓存实习")],
                strengths=[{"statement": "使用 Python 和 Redis 实现缓存", "evidence_ids": [ref("订单系统")]}],
                unknowns=["项目规模"])


def model_router(outputs):
    provider = ReplayModelProvider(outputs)
    return ModelRouter({RESUME_PROFILE_ROUTE: provider}), provider


def test_evidence_stable_parser_offsets():
    raw = ResumeParser().parse(RAW.encode(), "txt")
    first = evidence_rows(7, raw)
    assert first == evidence_rows(7, raw)
    assert first != evidence_rows(8, raw)
    assert all(row["page"] is None and raw[row["start_offset"]:row["end_offset"]] == row["excerpt"] for row in first)
    assert len({row["evidence_key"] for row in first}) == len(first)


@pytest.mark.parametrize("case", ["unknown", "strength", "skill_state", "negative_unknown", "excerpt", "no_evidence"])
def test_deterministic_rejections(case):
    body = response(7)
    rows = evidence_rows(7, RAW)
    evidence = {row["evidence_key"]: dict(key=row["evidence_key"], excerpt=row["excerpt"], section=row["section"]) for row in rows}
    if case == "unknown": body["skills"][0]["evidence_ids"] = ["re_invented"]
    if case == "strength": body["strengths"][0]["statement"] = "精通高并发，具备优秀系统设计能力"
    if case == "skill_state": body["skills"][0]["evidence_state"] = "STRONG_EVIDENCE"
    if case == "negative_unknown": body["unknowns"] = ["候选人不会 Kafka"]
    if case == "excerpt": body["skills"][0]["excerpt"] = "invented excerpt"
    if case == "no_evidence": body["strengths"][0]["evidence_ids"] = []
    with pytest.raises(ValueError):
        resolve_and_validate(CandidateProfileDelta.model_validate(body), evidence)


async def successful_scenario(url):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        user_id, resume_id = await seed(factory)
        router, provider = model_router([response(resume_id)])
        result = await ResumeProfileAgent(factory, router).run(resume_id)
        assert result["status"] == "COMPLETED", result
        repeat = await ResumeProfileAgent(factory, router).run(resume_id)
        assert repeat["version_id"] == result["version_id"] and repeat["reused"] and provider.calls == 1
        async with factory() as db:
            service = CandidateProfileService(db)
            profile = await service.serialize(user_id)
            assert profile["producer"] == "REPLAY_FIXTURE" and profile["version_number"] == 1
            assert all(profile["normalized"][key] for key in ("projects", "education", "work_experience", "skills"))
            assert len(profile["normalized"]["work_experience"]) == 2
            skill = await db.scalar(select(UserSkill).where(UserSkill.version_id == result["version_id"]))
            links = list((await db.scalars(select(CandidateFactEvidence).where(CandidateFactEvidence.fact_id == skill.fact_id))).all())
            assert links and links[0].resume_evidence_id and not links[0].user_evidence_id
            before = await db.scalar(select(func.count(ResumeEvidence.id)).where(ResumeEvidence.resume_id == resume_id))
            await ensure_resume_evidence(db, resume_id, RAW)
            await db.commit()
            assert before == await db.scalar(select(func.count(ResumeEvidence.id)).where(ResumeEvidence.resume_id == resume_id))
            run = await db.get(ExperienceAgentRun, (await db.get(CandidateProfileVersion, result["version_id"])).agent_run_id)
            step = await db.scalar(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == run.id))
            assert run.agent_role == "ResumeProfileAgent" and run.refresh_task_id is None and run.status == "SUCCEEDED"
            assert step.provider_name == "replay" and step.prompt_name == "resume_profile" and step.token_usage_json["total_tokens"] == 0
        return result
    finally:
        await engine.dispose()


def test_agent_normalized_and_idempotent(database_url):
    asyncio.run(successful_scenario(database_url))


async def correction_scenario(url):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        uid, rid = await seed(factory)
        router, provider = model_router([response(rid), response(rid)])
        initial = await ResumeProfileAgent(factory, router).run(rid)
        async with factory() as db:
            old = deepcopy((await db.get(CandidateProfileVersion, initial["version_id"])).snapshot_json)
            fixed = await CandidateProfileService(db).correct(uid, CorrectionRequest(base_version_id=initial["version_id"],
                field_path="skills.0.label", new_value="Redis 缓存", reason="补充准确技能名称"))
            await db.commit()
            assert fixed["version_number"] == 2 and fixed["base_version_id"] == initial["version_id"]
            assert fixed["snapshot"]["projects"] == old["projects"]
            assert fixed["snapshot"]["education"] == old["education"]
            assert fixed["snapshot"]["skills"][0]["label"] == "Redis 缓存"
            assert fixed["snapshot"]["skills"][0]["evidence_state"] == "MENTIONED"
            assert fixed["provenance"]["skills.0"] == "USER_CONFIRMED"
            assert (await db.get(CandidateProfileVersion, initial["version_id"])).snapshot_json == old
            correction = await db.scalar(select(ProfileCorrection).where(ProfileCorrection.new_version_id == fixed["id"]))
            assert correction.changed_by == "user" and correction.user_id == uid and correction.old_value == "Redis" and correction.new_value == "Redis 缓存"
            confirmed = await db.scalar(select(UserConfirmedEvidence).where(UserConfirmedEvidence.user_id == uid))
            assert confirmed.statement == "Redis 缓存"  # Does not attest to unchanged project details.
            with pytest.raises(AppError) as err:
                await CandidateProfileService(db).correct(uid, CorrectionRequest(base_version_id=initial["version_id"], field_path="skills.0.label", new_value="Kafka", reason="stale"))
            assert err.value.status_code == 409
            await db.rollback()
        # Automatic retry after a correction must NOT reset current_version to N.
        repeat = await ResumeProfileAgent(factory, router).run(rid)
        assert repeat["version_id"] == initial["version_id"] and provider.calls == 1
        async with factory() as db:
            assert (await CandidateProfileService(db).current(uid)).id == fixed["id"]
        regenerated = await ResumeProfileAgent(factory, router).run(rid, regeneration_token="explicit-user-request")
        assert regenerated["status"] == "COMPLETED", regenerated
        async with factory() as db:
            current = await CandidateProfileService(db).serialize(uid)
            assert current["version_number"] == 3
            assert any(item["label"] == "Redis 缓存" for item in current["snapshot"]["skills"])
            assert not any(item["label"] == "Redis" for item in current["snapshot"]["skills"])
        repeat = await ResumeProfileAgent(factory, router).run(rid, regeneration_token="explicit-user-request")
        assert repeat["reused"] and provider.calls == 2
    finally:
        await engine.dispose()


def test_complete_correction_snapshot_and_provenance(database_url):
    asyncio.run(correction_scenario(database_url))


@pytest.mark.parametrize("failure", [False, True])
def test_structured_repair_or_hard_fail(database_url, failure):
    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory)
            body = response(rid)
            # Valid facts, JSON syntax only broken; constrained repair retains all facts.
            import json
            damaged = json.dumps(body, ensure_ascii=False)[:-1]
            router, provider = model_router([damaged, "not json" if failure else body])
            result = await ResumeProfileAgent(factory, router).run(rid)
            assert provider.calls == 2
            assert result["status"] == ("FAILED" if failure else "COMPLETED"), result
            async with factory() as db:
                generation = await db.scalar(select(ProfileGeneration).where(ProfileGeneration.resume_id == rid))
                step = await db.scalar(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id == generation.agent_run_id))
                assert step.structured_retry == 1
                assert (await db.get(Resume, rid)).status == "COMPLETED"
                assert await db.scalar(select(ResumeReport.id).where(ResumeReport.resume_id == rid))
                if failure:
                    assert generation.status == "FAILED" and await CandidateProfileService(db).current(uid) is None
        finally:
            await engine.dispose()
    asyncio.run(scenario())


def test_concurrent_retries_and_report_content_change(database_url):
    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory)
            router, provider = model_router([response(rid), response(rid), response(rid)])
            results = await asyncio.gather(ResumeProfileAgent(factory, router).run(rid), ResumeProfileAgent(factory, router).run(rid))
            assert all(result["status"] == "COMPLETED" for result in results), results
            assert len({result["version_id"] for result in results}) == 1
            async with factory() as db:
                profile = await db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == uid))
                assert await db.scalar(select(func.count(CandidateProfileVersion.id)).where(CandidateProfileVersion.candidate_profile_id == profile.id)) == 1
                report = await db.scalar(select(ResumeReport).where(ResumeReport.resume_id == rid))
                report.summary_json = {**report.summary_json, "actual_content_change": True}
                await db.commit()
            changed = await ResumeProfileAgent(factory, router).run(rid)
            assert changed["status"] == "COMPLETED", changed
            async with factory() as db:
                assert (await CandidateProfileService(db).current(uid)).version_number == 2
            _, new_rid = await seed(factory)
            async with factory() as db:
                new_resume = await db.get(Resume, new_rid)
                new_report = await db.scalar(select(ResumeReport).where(ResumeReport.resume_id == new_rid))
                new_resume.user_id, new_report.user_id = uid, uid
                await db.commit()
            new_router, _ = model_router([response(new_rid)])
            new_result = await ResumeProfileAgent(factory, new_router).run(new_rid)
            assert new_result["status"] == "COMPLETED", new_result
            async with factory() as db:
                assert (await CandidateProfileService(db).current(uid)).version_number == 3
        finally: await engine.dispose()
    asyncio.run(scenario())


def test_evidence_only_resume_delete_protection(database_url, monkeypatch):
    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
        deleted = AsyncMock()
        monkeypatch.setattr(MinioObjectStorageProvider, "delete", deleted)
        try:
            uid, rid = await seed(factory)
            async with factory() as db:
                await ensure_resume_evidence(db, rid, RAW)
                await db.commit()
                assert await CandidateProfileService(db).current(uid) is None
                with pytest.raises(AppError) as err: await ResumeService(db).delete_resume(rid, uid)
                assert err.value.status_code == 409
                deleted.assert_not_called()
                await db.rollback()
                with pytest.raises(IntegrityError): await db.execute(delete(Resume).where(Resume.id == rid))
                await db.rollback()
            # Unreferenced old resumes retain their existing deletion behavior.
            plain_uid, plain_rid = await seed(factory)
            async with factory() as db:
                await ResumeService(db).delete_resume(plain_rid, plain_uid)
                await db.commit()
                assert await db.get(Resume, plain_rid) is None
                deleted.assert_awaited_once()
        finally: await engine.dispose()
    asyncio.run(scenario())


def test_unknown_evidence_agent_fails_without_profile(database_url):
    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory)
            body = response(rid)
            body["skills"][0]["evidence_ids"] = ["re_foreign_or_invented"]
            router, _ = model_router([body])
            result = await ResumeProfileAgent(factory, router).run(rid)
            assert result["status"] == "FAILED" and result["error"] == "UNKNOWN_CANDIDATE_EVIDENCE"
            async with factory() as db:
                assert await CandidateProfileService(db).current(uid) is None
                assert (await db.get(Resume, rid)).status == "COMPLETED"
        finally: await engine.dispose()
    asyncio.run(scenario())


def test_db_immutable_history_and_delete_protection(database_url, monkeypatch):
    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory)
            router, _ = model_router([response(rid)])
            result = await ResumeProfileAgent(factory, router).run(rid)
            from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
            delete_object = AsyncMock()
            monkeypatch.setattr(MinioObjectStorageProvider, "delete", delete_object)
            async with factory() as db:
                with pytest.raises(AppError) as err:
                    await ResumeService(db).delete_resume(rid, uid)
                assert err.value.status_code == 409
                delete_object.assert_not_called()
                await db.rollback()
                with pytest.raises(IntegrityError):
                    await db.execute(delete(Resume).where(Resume.id == rid))
                await db.rollback()
                with pytest.raises(IntegrityError):
                    db.add(CandidateFact(version_id=result["version_id"], field_path="skills.999", statement="late injection", provenance="RESUME_EXTRACTED"))
                    await db.flush()
                await db.rollback()
                for table in (CandidateProfileVersion, CandidateFact, ProjectExperience, WorkExperience, EducationExperience, UserSkill, ResumeEvidence):
                    row = await db.scalar(select(table).where(table.id > 0).limit(1))
                    col = "statement" if table == CandidateFact else "summary" if table == CandidateProfileVersion else "excerpt" if table == ResumeEvidence else "name" if table in (ProjectExperience, UserSkill) else "description"
                    with pytest.raises(IntegrityError):
                        await db.execute(update(table).where(table.id == row.id).values(**{col: "overwritten"}))
                    await db.rollback()
        finally: await engine.dispose()
    asyncio.run(scenario())


def test_atomic_failure_and_unique_profile(database_url):
    async def scenario():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory)
            router, _ = model_router([response(rid)])
            initial = await ResumeProfileAgent(factory, router).run(rid)
            async with factory() as db:
                old = await db.get(CandidateProfileVersion, initial["version_id"])
                profile_id = old.candidate_profile_id
                real_flush = db.flush
                count = 0
                async def failing_flush(*args, **kwargs):
                    nonlocal count
                    count += 1
                    if count == 3:
                        raise RuntimeError("injected_normalized_persistence_failure")
                    await real_flush(*args, **kwargs)
                db.flush = failing_flush
                with pytest.raises(RuntimeError):
                    await CandidateProfileService(db).persist(user_id=uid, resume_id=rid, snapshot=old.snapshot_json,
                        generation_key=digest(["failure", uuid4().hex]), base_version_id=old.id, producer="REPLAY_FIXTURE")
                await db.rollback()
                db.flush = real_flush
                assert (await CandidateProfileService(db).current(uid)).id == initial["version_id"]
                assert await db.scalar(select(func.count(CandidateProfileVersion.id)).where(CandidateProfileVersion.candidate_profile_id == profile_id)) == 1
                db.add(CandidateProfile(user_id=uid))
                with pytest.raises(IntegrityError): await db.flush()
                await db.rollback()
        finally: await engine.dispose()
    asyncio.run(scenario())


def test_api_ownership_and_correction(database_url):
    async def scenario():
        from interview_api.main import app
        from interview_api.api.deps import get_current_user
        from interview_api.infrastructure.db.session import get_db
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        uid, rid = await seed(factory)
        other_id, _ = await seed(factory)
        router, _ = model_router([response(rid)])
        result = await ResumeProfileAgent(factory, router).run(rid)
        actor = uid
        async def db_override():
            async with factory() as db:
                try:
                    yield db
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
        async def user_override():
            async with factory() as db: return await db.get(User, actor)
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_current_user] = user_override
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                current = await client.get("/api/v1/candidate-profile")
                assert current.status_code == 200 and current.json()["data"]["current"]["normalized"]["skills"]
                fixed = await client.post("/api/v1/candidate-profile/corrections", json={"base_version_id": result["version_id"], "field_path": "skills.0.label", "new_value": "Redis 缓存", "reason": "本人确认"})
                assert fixed.status_code == 201 and fixed.json()["data"]["version_number"] == 2
                denied_delete = await client.delete(f"/api/v1/resumes/{rid}")
                assert denied_delete.status_code == 409
                actor = other_id
                assert (await client.get(f"/api/v1/candidate-profile/versions/{result['version_id']}")).status_code == 404
                denied = await client.post("/api/v1/candidate-profile/corrections", json={"base_version_id": fixed.json()["data"]["id"], "field_path": "skills.0.label", "new_value": "Kafka", "reason": "foreign"})
                assert denied.status_code == 409
                assert (await client.get("/api/v1/candidate-profile")).json()["data"]["current"] is None
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()
    asyncio.run(scenario())
