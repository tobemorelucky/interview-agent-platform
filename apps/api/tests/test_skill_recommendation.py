"""V1.1 deterministic discovery/recommendation: no network/model invocation."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from interview_api.modules.interview.skill_registry import ROOT, discover
from interview_api.modules.interview.skill_engine import SkillRegistry
from interview_api.modules.interview.skill_recommendation import recommend, attach_to_match


def job(title, requirements, **extra):
    return dict(job_id=1, jd_id=2, jd_fingerprint="frozen-jd", title=title,
        requirements=[dict(id=i + 1, statement=text) for i, text in enumerate(requirements)], **extra)


@pytest.mark.parametrize("title,requirements,expected", [
    ("AI应用工程师", ["RAG检索增强与智能体开发", "Python Java Redis SQL"], "ai_agent_application"),
    ("Java后端工程师", ["Spring微服务开发", "Redis Kafka MySQL"], "java_backend"),
    ("科技菁英", ["信息系统研发和数据安全", "数据库应用研发"], "bank_it"),
])
def test_role_and_duties_rank(title, requirements, expected):
    result = recommend(job(title, requirements))
    assert result["recommended_skill_id"] == expected
    assert result["auto_select"] and result["status"] == "RECOMMENDED"
    assert any(e["requirement_id"] for e in result["ranking"][0]["evidence"])
    assert result == recommend(job(title, requirements))


def test_company_not_a_signal_and_bank_marketing_is_excluded():
    value = job("综合营销岗", ["客户营销与金融产品销售"], company_name="银行")
    result = recommend(value)
    assert not result["auto_select"]
    bank = next(c for c in result["ranking"] if c["skill_id"] == "bank_it")
    assert bank["excluded"]
    value["company_name"] = "任意互联网企业"
    assert result == recommend(value)


def test_keyword_frequency_never_overrides_primary_role():
    value = job("AI应用工程师", ["RAG检索增强与Agent开发", "Java Python Redis SQL"])
    before = recommend(value)
    value["requirements"][1]["statement"] = "Java Python Redis SQL " * 100
    after = recommend(value)
    assert before["recommended_skill_id"] == after["recommended_skill_id"] == "ai_agent_application"
    assert [c["ranking_points"] for c in before["ranking"]] == [c["ranking_points"] for c in after["ranking"]]
    assert not recommend(job("开发岗", ["Java Python Redis SQL"]))["auto_select"]


def test_sparse_and_ambiguous_are_manual_not_forced():
    assert recommend(job("AI应用工程师", []))["status"] == "INSUFFICIENT_DATA"
    result = recommend(job("AI应用工程师 / Java后端工程师", ["大模型RAG与智能体开发", "Java Spring微服务开发 Kafka"]))
    assert result["status"] == "AMBIGUOUS" and result["recommended_skill_id"] is None


def python_skill(root):
    directory = root / "python_backend" / "1.0.0"
    shutil.copytree(ROOT / "java_backend" / "1.0.0", directory)
    path = directory / "manifest.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta.update(id="python_backend", label="Python 后端开发", professional_category="python_backend",
        applicable_roles=["Python后端工程师"], capability_tags=["Python", "FastAPI"])
    meta["matching"] = dict(categories=["python_backend"], title_aliases=["Python后端"],
        responsibility_terms=["FastAPI", "Python服务端"], capability_groups=[["Python"], ["FastAPI", "Django"]], exclusions=[])
    path.write_text(json.dumps(meta), encoding="utf-8")
    path = directory / "skill.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body.update(id="python_backend", label="Python 后端开发", priority_topics=["python"])
    path.write_text(json.dumps(body), encoding="utf-8")
    return directory


def test_new_skill_discovered_recommended_and_in_api_catalog_without_code_change(tmp_path):
    shutil.copytree(ROOT, tmp_path / "skills")
    root = tmp_path / "skills"
    python_skill(root)
    discover(root, refresh=True)
    reads = []
    original = Path.read_text
    def read(path, *args, **kwargs):
        reads.append(str(path))
        return original(path, *args, **kwargs)
    with patch.object(Path, "read_text", read):
        result = recommend(job("Python后端工程师", ["FastAPI服务端开发"]), root=root)
        options = SkillRegistry(root).options()
    assert result["recommended_skill_id"] == "python_backend"
    assert "python_backend" in [s["id"] for s in options["skills"]]
    assert not any(p.endswith(("SKILL.md", "skill.json")) for p in reads)
    pinned = SkillRegistry(root).freeze("python_backend", "technical_screen", "standard")
    assert pinned["skill"]["id"] == "python_backend"
    # Actual catalog endpoint serves the discovered entry, no frontend ID list.
    import asyncio
    import httpx
    from types import SimpleNamespace
    from interview_api.main import app
    from interview_api.api.deps import get_current_user
    from interview_api.modules.interview.schemas import CreateSessionRequest
    assert CreateSessionRequest(company_id=1, job_id=1, interviewer_skill_id="python_backend").interviewer_skill_id == "python_backend"
    async def request():
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                with patch("interview_api.modules.interview.skill_engine.SkillRegistry", return_value=SkillRegistry(root)):
                    response = await client.get("/api/v1/interview/interviewer-options")
                assert response.status_code == 200
                assert "python_backend" in [s["id"] for s in response.json()["data"]["skills"]]
        finally:
            app.dependency_overrides.clear()
    asyncio.run(request())


def test_invalid_manifest_and_unapproved_tool_are_quarantined(tmp_path):
    shutil.copytree(ROOT, tmp_path / "skills")
    root = tmp_path / "skills"
    directory = python_skill(root)
    path = directory / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for change in (dict(id="java_backend"), dict(instructions_path="../../../../outside.md"), dict(required_tools=["shell"])):
        path.write_text(json.dumps({**data, **change}), encoding="utf-8")
        entries, errors = discover(root, refresh=True)
        assert "python_backend" not in entries and errors
        assert len(SkillRegistry(root).options()["skills"]) == 3
        assert SkillRegistry(root).freeze("java_backend", "technical_screen", "standard")


def test_metadata_cache_and_no_internal_prompts_in_catalog():
    discover(refresh=True)
    with patch.object(Path, "read_text", side_effect=AssertionError("cached metadata")):
        assert discover()[0]
    view = json.dumps(SkillRegistry().options())
    for secret in ("evaluation_anchors", "instructions_path", "SKILL.md", "probe_mode", "missing_point_action"):
        assert secret not in view


def test_response_annotation_cannot_mutate_matching_or_frozen_inputs():
    candidate = {**job("Java后端工程师", ["Spring开发"]), "pre_rank": 1, "agent_rank": 2,
        "final_rank": 2, "eligibility_status": "UNKNOWN", "recommendation": {"fit_band": "GOOD_MATCH"}}
    original = deepcopy(candidate)
    output = attach_to_match(candidate)
    assert candidate == original
    assert {k: v for k, v in output.items() if k != "interviewer_skill_recommendation"} == original


def test_session_api_projection_hides_local_rules_without_mutating_snapshot():
    from interview_api.modules.interview.skill_registry import public_session_view
    original = dict(context_snapshot=dict(interviewer_config=SkillRegistry().freeze("java_backend", "technical_screen", "standard"),
        public_questions=[dict(id=1, question="Example", expected_points=["not before answer"])]))
    pinned = deepcopy(original)
    result = public_session_view(original)
    assert original == pinned
    text = json.dumps(result)
    for key in ("registry_manifest", "evaluation_anchors", "instructions", "expected_points", "prompt_versions"):
        assert key not in text
    assert result["context_snapshot"]["interviewer_config"]["skill"]["id"] == "java_backend"


def test_recommendation_api_auth_and_current_auto_selection_validation(database_url):
    import asyncio
    import httpx
    from types import SimpleNamespace
    from test_m4_interview import seed, run_with_database
    from interview_api.main import app
    from interview_api.api.deps import get_current_user
    from interview_api.infrastructure.db.session import get_db
    from interview_api.modules.interview import router as routes
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.core.exceptions import AppError

    async def scenario(factory):
        core, _, _, uid, old = await seed(factory)
        ctx = old["context_snapshot"]
        async def dependency():
            async with factory() as db: yield db
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                assert (await client.post("/api/v1/interview/skill-recommendations", json={"job_ids": [ctx["job_id"]]})).status_code == 401
                app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
                app.dependency_overrides[get_db] = dependency
                response = await client.post("/api/v1/interview/skill-recommendations", json={"job_ids": [ctx["job_id"]]})
                assert response.status_code == 200
                assert len(response.json()["data"]) == 1
                with pytest.raises(AppError) as failed:
                    await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"],
                        interviewer_skill_id="bank_it", interviewer_selection_source="AUTO_RECOMMENDED"))
                assert failed.value.code == "SKILL_RECOMMENDATION_CHANGED"
                manual = await core.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"],
                    interviewer_skill_id="bank_it", interviewer_selection_source="USER_SELECTED"))
                assert manual["context_snapshot"]["interviewer_selection"]["source"] == "USER_SELECTED"
        finally:
            app.dependency_overrides.clear()
    asyncio.run(run_with_database(database_url, scenario))
