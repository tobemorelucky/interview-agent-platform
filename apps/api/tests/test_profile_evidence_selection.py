"""Synthetic fixtures for evidence selection, never private resume content."""
import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.context import build_profile_context
from interview_api.modules.candidate_profile.evidence import ProfileEvidenceError, normalize_skill_evidence_states, resolve_and_validate
from interview_api.modules.candidate_profile.models import CandidateFact, CandidateFactEvidence, CandidateProfileVersion, ResumeEvidence
from interview_api.modules.candidate_profile.schemas import CandidateEvidenceSelection, CandidateProfileDelta, CorrectionRequest
from interview_api.modules.candidate_profile.service import CandidateProfileService
from test_m3_candidate_profile import seed, response, model_router
from test_profile_context import catalog


RAW = "项目经历\nDEMO 项目使用 Redis 缓存。\nDEMO 项目使用 Redis 缓存并记录命中日志。\n技能\nKafka\n"


def context():
    return build_profile_context(catalog(RAW), {})


def test_model_schema_has_no_fact_prose_or_derived_analysis():
    schema = CandidateEvidenceSelection.model_json_schema()
    encoded = json.dumps(schema)
    assert '"statement"' not in encoded and '"candidate_summary"' not in encoded and '"strengths"' not in encoded
    assert set(schema["properties"]) == {"skills", "projects", "education", "work_experience", "internship_experience", "unknowns"}
    for definition in schema["$defs"].values():
        assert definition["additionalProperties"] is False


@pytest.mark.parametrize("extra", ["statement", "excerpt", "category"])
def test_model_cannot_supply_stable_fact_statement_or_unrecognized_fields(extra):
    with pytest.raises(ValidationError):
        CandidateEvidenceSelection.model_validate({"projects": [{"label": "DEMO", "evidence_ids": ["E02"], extra: "invented"}]})


@pytest.mark.parametrize("category", ["skills", "projects", "education", "work_experience", "internship_experience"])
def test_deterministic_representative_preserves_every_reference_and_order(category):
    ctx = context()
    label = "Redis" if category == "skills" else "DEMO 项目"
    original = deepcopy(ctx.catalog)
    first = CandidateEvidenceSelection.model_validate({category: [{"label": label, "evidence_ids": ["E03", "E02", "E03"]}]})
    second = CandidateEvidenceSelection.model_validate({category: [{"label": label, "evidence_ids": ["E02", "E03"]}]})
    a, b = ctx.resolve_selection(first), ctx.resolve_selection(second)
    assert a.model_dump() == b.model_dump()
    fact = getattr(a, category)[0]
    assert fact.statement == RAW.splitlines()[2]
    assert fact.evidence_ids == [ctx.alias_to_key["E02"], ctx.alias_to_key["E03"]]
    assert a.candidate_summary is None and a.strengths == []
    resolve_and_validate(a, ctx.catalog, stable_only=True)
    assert ctx.catalog == original and first.model_dump()[category][0]["evidence_ids"] == ["E03", "E02", "E03"]


@pytest.mark.parametrize("category", ["skills", "projects"])
def test_unsupported_label_rejected_without_rewriting_citations(category):
    ctx = context()
    selection = CandidateEvidenceSelection.model_validate({category: [{"label": "Kafka", "evidence_ids": ["E02"]}]})
    with pytest.raises(ProfileEvidenceError) as exc:
        ctx.resolve_selection(selection)
    diagnostic = exc.value.diagnostics
    assert exc.value.code == "UNSUPPORTED_CANDIDATE_FACT"
    assert diagnostic["field_path"] == category + ".0.label"
    assert diagnostic["possible_wrong_evidence_reference"] is True
    assert diagnostic["cited_aliases"] == ["E02"]


@pytest.mark.parametrize("alias", ["E99", "re_fake", "e02"])
def test_unknown_evidence_alias_rejected(alias):
    selection = CandidateEvidenceSelection(skills=[dict(label="Redis", evidence_ids=[alias])])
    with pytest.raises(ProfileEvidenceError, match="UNKNOWN_CANDIDATE_EVIDENCE"):
        context().resolve_selection(selection)


@pytest.mark.parametrize("proposed,refs,expected", [
    ("STRONG_EVIDENCE", ["E02"], "DEMONSTRATED"),
    ("STRONG_EVIDENCE", ["E02", "E03"], "STRONG_EVIDENCE"),
    ("MENTIONED", ["E02", "E03"], "MENTIONED"),
    ("STRONG_EVIDENCE", ["E02", "E02"], "DEMONSTRATED"),
])
def test_selected_skill_state_only_converges_downward(proposed, refs, expected):
    ctx = context()
    delta = ctx.resolve_selection(CandidateEvidenceSelection(skills=[dict(
        label="Redis", evidence_ids=refs, proposed_evidence_state=proposed)]))
    normalize_skill_evidence_states(delta, ctx.catalog)
    resolve_and_validate(delta, ctx.catalog, stable_only=True)
    assert delta.skills[0].evidence_state == expected


def test_unknown_information_never_becomes_negative_capability_fact():
    ctx = context()
    delta = ctx.resolve_selection(CandidateEvidenceSelection(unknowns=["候选人不会 Kafka"]))
    with pytest.raises(ValueError, match="UNKNOWN_IS_NOT_A_CAPABILITY_JUDGMENT"):
        resolve_and_validate(delta, ctx.catalog, stable_only=True)


def test_historical_snapshot_schema_still_reads_original_statement():
    original = response(7, aliases=False)
    parsed = CandidateProfileDelta.model_validate(original)
    assert parsed.model_dump() == original
    assert parsed.candidate_summary and parsed.strengths


def test_persistence_all_provenance_links_and_statement_correction_compatibility(database_url):
    async def run():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, rid = await seed(factory)
            body = response(rid)
            body["skills"][0]["evidence_ids"].append("E03")
            router, provider = model_router([body, body])
            agent = ResumeProfileAgent(factory, router)
            generated = await agent.run(rid)
            assert generated["status"] == "COMPLETED", generated
            assert (await agent.run(rid))["reused"] and provider.calls == 1
            async with factory() as db:
                view = await CandidateProfileService(db).serialize(uid)
                assert view["snapshot"]["candidate_summary"] is None and view["snapshot"]["strengths"] == []
                facts = (await db.scalars(select(CandidateFact).where(CandidateFact.version_id == generated["version_id"]))).all()
                for fact in facts:
                    sources = (await db.scalars(select(ResumeEvidence).join(
                        CandidateFactEvidence, CandidateFactEvidence.resume_evidence_id == ResumeEvidence.id
                    ).where(CandidateFactEvidence.fact_id == fact.id))).all()
                    assert fact.statement in [source.excerpt for source in sources]
                    if fact.field_path == "skills.0":
                        assert len(sources) == 2
                old = deepcopy(view["snapshot"])
                fixed = await CandidateProfileService(db).correct(uid, CorrectionRequest(
                    base_version_id=generated["version_id"], field_path="skills.0.statement",
                    new_value="DEMO 用户确认缓存用途", reason="DEMO explicit confirmation"))
                await db.commit()
                historical = await CandidateProfileService(db).serialize(uid, await db.get(CandidateProfileVersion, generated["version_id"]))
                assert historical["snapshot"] == old
                assert fixed["snapshot"]["skills"][0]["statement"] == "DEMO 用户确认缓存用途"
                assert fixed["provenance"]["skills.0"] == "USER_CONFIRMED"
            regenerated = await agent.run(rid, regeneration_token="DEMO user requested regeneration")
            assert regenerated["status"] == "COMPLETED"
            async with factory() as db:
                current = await CandidateProfileService(db).serialize(uid)
                assert current["snapshot"]["skills"][0]["statement"] == "DEMO 用户确认缓存用途"
                assert current["provenance"]["skills.0"] == "USER_CONFIRMED"
        finally:
            await engine.dispose()
    asyncio.run(run())
