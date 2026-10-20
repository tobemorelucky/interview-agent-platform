"""Synthetic PDF-like reading order fixtures; never real resume content."""
import asyncio
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, RESUME_PROFILE_ROUTE
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.context import build_profile_context
from interview_api.modules.candidate_profile.evidence import (
    evidence_rows, ensure_resume_evidence, normalize_skill_evidence_states, resolve_and_validate,
)
from interview_api.modules.candidate_profile.models import CandidateProfileVersion, ResumeEvidence, UserSkill
from interview_api.modules.candidate_profile.schemas import CandidateProfileDelta, CorrectionRequest
from interview_api.modules.candidate_profile.service import CandidateProfileService, evidence_catalog
from interview_api.modules.experience.models import ExperienceAgentRun
from interview_api.modules.resume.models import ResumeReport
from test_m3_candidate_profile import seed


RAW = "\n".join([
    "DEMO 示例大学 计算机科学 本科 2026",
    "DEMO RAG 检索项目 2026",
    "使用 RAG 构建检索服务，使用 Redis 缓存检索结果。",
    "技术栈：Python Redis RAG",
    "不明用途的孤立字段",
    "使用 Kafka 异步处理检索事件并实现失败重试。",
    "DEMO 订单项目 2026",
    "使用 Redis 缓存订单查询，记录缓存命中及失败情况。",
    "Python 和 SQL 技能说明",
    "教育背景", "项目经历", "专业技能",
    "DEMO候选人", "demo@example.com 13800000000", "未确认字段",
])
REPORT = {
    "basic_info": {"name": "DEMO候选人", "email": "demo@example.com", "phone": "13800000000"},
    "education": [{"school": "DEMO 示例大学", "major": "计算机科学", "degree": "本科"}],
    "projects": [
        {"name": "DEMO RAG 检索项目", "description": "使用 RAG 构建检索服务，使用 Redis 缓存检索结果。",
         "tech_stack": ["Python", "Redis", "RAG"], "key_contributions": ["使用 Kafka 异步处理检索事件并实现失败重试。"]},
        {"name": "DEMO 订单项目", "description": "使用 Redis 缓存订单查询，记录缓存命中及失败情况。"},
    ],
    "skills": {"languages": ["Python", "SQL"], "tools": ["Kafka", "Redis", "RAG"]},
    "risk_points": [{"description": "MODEL_DERIVED_RISK_MUST_NOT_ENTER_CONTEXT"}],
    "legacy_only": "LEGACY_ONLY_MUST_NOT_ENTER_CONTEXT", "internships": [],
}


def catalog(raw=RAW, resume_id=1):
    return {row["evidence_key"]: dict(row, key=row["evidence_key"], id=i, kind="RESUME")
            for i, row in enumerate(evidence_rows(resume_id, raw), 1)}


def test_pdf_like_reordered_text_grouped_without_changing_atomic_evidence():
    original = catalog()
    before = deepcopy(original)
    context = build_profile_context(original, REPORT)
    assert original == before
    assert len(context.catalog) == len(original)
    for key, item in context.catalog.items():
        assert item["excerpt"] == original[key]["excerpt"]
        assert item["evidence_key"] == key and item["key"] == key
        assert item["start_offset"] == original[key]["start_offset"]
        assert item["end_offset"] == original[key]["end_offset"]
        assert item["content_hash"] == original[key]["content_hash"]
    for technology in ("Kafka", "Redis", "RAG"):
        assert any(technology in item["excerpt"] and item["section"] == "projects"
                   for item in context.catalog.values())
    assert all(item["section"] == "general" for item in context.catalog.values()
               if item["excerpt"] in ("项目经历", "教育背景", "专业技能", "DEMO候选人", "未确认字段")
               or "@" in item["excerpt"])
    # Being between project anchors does NOT license inventing a classification.
    assert next(item for item in context.catalog.values() if item["excerpt"] == "不明用途的孤立字段")["section"] == "general"
    assert next(item for item in context.catalog.values() if "示例大学" in item["excerpt"])["section"] == "education"


def test_deterministic_alias_order_and_minimal_report_context():
    source = catalog()
    a = build_profile_context(source, REPORT)
    b = build_profile_context(dict(reversed(list(source.items()))), deepcopy(REPORT))
    assert a.payload == b.payload and a.alias_to_key == b.alias_to_key
    assert list(a.alias_to_key) == [f"E{i:02}" for i in range(1, len(source)+1)]
    serialized = json.dumps(a.payload)
    assert "re_" not in serialized and "resume_report" not in a.payload
    assert "risk_points" not in serialized and "MODEL_DERIVED_RISK" not in serialized
    assert "legacy_only" not in serialized
    assert {row["excerpt"] for row in a.payload["resume_evidence"]} == {row["excerpt"] for row in source.values()}
    assert {alias for group in a.payload["evidence_groups"] for alias in group["evidence_ids"]} == set(a.alias_to_key)


def test_report_hint_alone_cannot_create_concrete_use_evidence():
    source = catalog("Redis\nKafka\n不明字段")
    hints = {"projects": [{"name": "虚构项目", "description": "使用 Redis 完成不存在的项目实现", "tech_stack": ["Redis", "Kafka"]}]}
    context = build_profile_context(source, hints)
    assert all(item["section"] == "general" for item in context.catalog.values())
    assert not any("虚构" in row["excerpt"] for row in context.payload["resume_evidence"])


def test_ambiguous_exact_anchors_stay_general():
    text = "使用 Redis 缓存查询并记录缓存命中率。"
    hints = {"projects": [{"name": "项目甲", "description": text}, {"name": "项目乙", "description": text}]}
    item = next(iter(build_profile_context(catalog(text), hints).catalog.values()))
    assert item["section"] == "general" and item["grouping_basis"] == ["ambiguous_exact_anchors"]


@pytest.mark.parametrize("bad", ["E99", "re_invented", "e01", "E1", "P01"])
def test_unknown_alias_rejected_exactly(bad):
    context = build_profile_context(catalog(), REPORT)
    delta = CandidateProfileDelta.model_validate({"skills": [dict(label="Redis", statement="Redis", evidence_ids=[bad])]})
    with pytest.raises(ValueError, match="UNKNOWN_CANDIDATE_EVIDENCE") as exc:
        context.resolve_aliases(delta)
    assert exc.value.diagnostics["validation_rule"] == "CALL_LOCAL_EVIDENCE_ALIAS"
    assert delta.skills[0].evidence_ids == [bad]


def test_real_key_not_accepted_as_model_alias_and_current_profile_does_not_leak_keys():
    source = catalog()
    real = next(iter(source))
    current = {"candidate_summary": dict(statement="old", evidence_ids=[real, "uc_old_key"]),
               "skills": [dict(label="Redis", statement="Redis", evidence_ids=[real, "uc_old_key"])]}
    context = build_profile_context(source, REPORT, current)
    assert context.payload["current_profile"]["candidate_summary"] is None
    assert context.payload["current_profile"]["skills"][0]["evidence_ids"] == ["E01"]
    assert current["candidate_summary"]["evidence_ids"] == [real, "uc_old_key"]
    with pytest.raises(ValueError, match="UNKNOWN_CANDIDATE_EVIDENCE"):
        context.resolve_aliases(CandidateProfileDelta.model_validate({"candidate_summary": dict(statement="old", evidence_ids=[real])}))


def test_alias_restore_downward_normalization_and_validator_unchanged():
    context = build_profile_context(catalog(), REPORT)
    alias = next(alias for alias, key in context.alias_to_key.items() if "使用 Kafka" in context.catalog[key]["excerpt"])
    delta = CandidateProfileDelta.model_validate({"skills": [dict(label="Kafka", statement="Kafka", evidence_ids=[alias, alias], proposed_evidence_state="STRONG_EVIDENCE")]})
    resolved = context.resolve_aliases(delta)
    assert resolved.skills[0].evidence_ids == [context.alias_to_key[alias]] * 2
    assert delta.skills[0].evidence_ids == [alias, alias]
    diagnostics = normalize_skill_evidence_states(resolved, context.catalog)
    resolve_and_validate(resolved, context.catalog)
    assert resolved.skills[0].evidence_state == "DEMONSTRATED"
    assert diagnostics[0]["maximum_supported_state"] == "DEMONSTRATED"
    invalid = CandidateProfileDelta.model_validate({"strengths": [dict(statement="精通 Kafka", evidence_ids=[alias])]})
    with pytest.raises(ValueError, match="UNSUPPORTED_STRENGTH"):
        resolve_and_validate(context.resolve_aliases(invalid), context.catalog)


def test_work_and_internship_exact_group_hints():
    raw = "DEMO 示例公司 后端开发\n使用 Kafka 处理异步订单并记录重试结果。\nDEMO 实验室 实习\n使用 Redis 缓存实验查询并记录命中情况。"
    hints = {
        "work_experience": [{"company": "DEMO 示例公司", "responsibilities": [raw.splitlines()[1]]}],
        "internships": [{"company": "DEMO 实验室", "responsibilities": [raw.splitlines()[3]]}],
    }
    assert [item["section"] for item in build_profile_context(catalog(raw), hints).catalog.values()] == ["work", "work", "internship", "internship"]


class FixtureProvider(ReplayModelProvider):
    """Respond only to synthetic fixture, using the supplied call-local IDs."""
    async def generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        assert "resume_report" not in payload
        rows = payload["resume_evidence"]
        assert any("DEMO RAG" in row["excerpt"] for row in rows)
        def ref(fragment):
            return next(row["evidence_id"] for row in rows if fragment in row["excerpt"])
        self.responses.append({
            "skills": [dict(label=t, evidence_ids=[ref(fragment)], proposed_evidence_state="STRONG_EVIDENCE")
                       for t, fragment in (("Kafka", "使用 Kafka"), ("Redis", "使用 RAG"))],
            "projects": [dict(label="DEMO RAG 检索项目",
                              evidence_ids=[ref("DEMO RAG"), ref("使用 RAG")])],
            "education": [dict(label="DEMO 示例大学", evidence_ids=[ref("示例大学")])],
        })
        return await super().generate(messages, schema=schema)


def test_grouped_agent_persistence_idempotency_and_frozen_correction(database_url):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory, raw=RAW)
            async with factory() as db:
                report = await db.scalar(select(ResumeReport).where(ResumeReport.resume_id == rid))
                report.summary_json = deepcopy(REPORT)
                await ensure_resume_evidence(db, rid, RAW)
                await db.commit()
                source = deepcopy(await evidence_catalog(db, uid, resume_id=rid))
            provider = FixtureProvider([])
            agent = ResumeProfileAgent(factory, ModelRouter({RESUME_PROFILE_ROUTE: provider}))
            result = await agent.run(rid)
            assert result["status"] == "COMPLETED", result
            retry = await agent.run(rid)
            assert retry["reused"] and retry["version_id"] == result["version_id"] and provider.calls == 1
            async with factory() as db:
                version = await db.get(CandidateProfileVersion, result["version_id"])
                old = deepcopy(version.snapshot_json)
                assert all(key.startswith("re_") for fact in old["skills"] for key in fact["evidence_ids"])
                assert all(fact["evidence_state"] == "DEMONSTRATED" for fact in old["skills"])
                audit = await db.get(ExperienceAgentRun, version.agent_run_id)
                assert audit.output_json["profile_context"]["section_map"]
                assert await evidence_catalog(db, uid, resume_id=rid) == source
                assert (await CandidateProfileService(db).current(uid)).id == version.id
                # Changing legacy hints cannot invalidate the already published
                # immutable version or rewrite its grouping on user correction.
                report = await db.scalar(select(ResumeReport).where(ResumeReport.resume_id == rid))
                report.summary_json = {}
                await db.commit()
                fixed = await CandidateProfileService(db).correct(uid, CorrectionRequest(
                    base_version_id=version.id, field_path="projects.0.statement",
                    new_value="本人确认的 DEMO 项目描述", reason="fixture correction"))
                await db.commit()
                assert fixed["snapshot"]["skills"] == old["skills"]
                second = await CandidateProfileService(db).correct(uid, CorrectionRequest(
                    base_version_id=fixed["id"], field_path="skills.0.label", new_value="Kafka 消息队列", reason="fixture rename"))
                await db.commit()
                assert second["snapshot"]["skills"][1] == old["skills"][1]
                assert (await db.get(CandidateProfileVersion, version.id)).snapshot_json == old
                assert await evidence_catalog(db, uid, resume_id=rid) == source
                assert await db.scalar(select(UserSkill.id).where(UserSkill.version_id == second["id"]))
                assert (await CandidateProfileService(db).current(uid)).id == second["id"]
        finally:
            await engine.dispose()
    asyncio.run(run())
