"""R124: job matching must use the technologies the material literally names.

The user's real profile listed Java/Spring/MySQL/Redis/Kafka/ES plus
Python/FastAPI/LangChain/Milvus/RAG/Agent, yet every technical requirement showed
"当前材料未覆盖" and a test-development job ranked first. Two deterministic
defects caused that:

* ``normalized_value`` is empty for the curated JDs, so v1 compared the whole
  requirement sentence ("掌握Python/C++/Java之一") against each fact;
* no JobTag rows exist, so retrieval fell back to ``job.id`` order.

These tests pin the fixed behaviour without any model call.
"""
from interview_api.modules.job_matching.policy import (
    POLICY_VERSION, eligibility, evidence_matrix, pre_rank_key, ranking_components,
)
from interview_api.modules.job_matching.skills import technical_terms

FILTERS = dict(campus_types=[], job_families=[], tags=[], exclude_tags=[], locations=[],
               exclude_company_ids=[], exclude_job_ids=[])


def _fact(i, category, statement, *, state="DEMONSTRATED", label=None):
    return dict(id=i, category=category, field_path=f"{category}.{i}", statement=statement,
                label=label, evidence_ids=[f"re_{i}"], record_id=i, evidence_state=state)


PROFILE = dict(id=20, facts=[
    _fact(1, "skills", "技术栈：Java、Spring Boot、MySQL、Redis、Kafka、Elasticsearch"),
    _fact(2, "skills", "技术栈： Python、FastAPI、LangChain 、Milvus、BGE-M3"),
    _fact(3, "projects", "高并发秒杀优化：设计 Redis + Kafka 异步削峰方案"),
    _fact(4, "projects", "混合检索优化：结合 BGE-M3 向量检索与 Milvus BM25，通过 RRF 融合多路召回"),
    _fact(5, "skills", "大模型应用开发：掌握 Prompt Engineering、Tool Calling，了解 LangGraph"),
], evidence=[dict(key=f"re_{i}", id=i, kind="RESUME") for i in range(1, 6)])


def _job(job_id, title, requirements):
    return dict(job_id=job_id, company_id=100 + job_id, company_name=f"公司{job_id}", title=title,
                status="ACTIVE", jd_id=job_id, campus_type=None, job_family=None, tags=[],
                matching_constraints={}, requirements=requirements,
                sql_tag_overlap=0, eligibility_status="SATISFIED")


def _req(i, statement, importance="REQUIRED", kind="OTHER"):
    return dict(id=i, statement=statement, requirement_type=kind, normalized_value=None,
                importance=importance, claim_id=None)


def _rank(jobs):
    scored = []
    for job in jobs:
        job.update(eligibility(job, FILTERS))
        job["evidence_matrix"] = evidence_matrix(job, PROFILE)
        job["ranking_components"] = ranking_components(job)
        scored.append(job)
    return sorted(scored, key=pre_rank_key)


def test_or_chain_requirement_is_satisfied_by_one_language():
    job = _job(1, "Java 后端", [_req(1, "掌握Python/C++/Java之一")])
    row = _rank([job])[0]["evidence_matrix"][0]
    assert row["status"] == "SUPPORTED"
    assert "java" in row["matched_topics"] and "python" in row["matched_topics"]


def test_agent_and_rag_jobs_outrank_test_development_without_test_evidence():
    agent = _job(1, "AI Agent 开发", [_req(1, "熟悉 RAG、Agent 与向量数据库"), _req(2, "熟悉 Python")])
    java = _job(2, "Java 后端", [_req(3, "熟悉 Java、Spring Boot、MySQL、Redis、Kafka")])
    qa = _job(3, "测试开发工程师", [_req(4, "熟悉 pytest、Selenium 等自动化测试框架"),
                                   _req(5, "有接口测试平台建设经验")])
    order = [j["job_id"] for j in _rank([agent, java, qa])]
    assert order.index(1) < order.index(3) and order.index(2) < order.index(3)


def test_unmatched_requirements_do_not_become_fake_precision():
    qa = _job(9, "测试开发工程师", [_req(1, "熟悉 pytest、Selenium 等自动化测试框架")])
    ranked = _rank([qa])[0]
    assert ranked["ranking_components"]["evidence_basis"] == "NONE"
    assert ranked["ranking_components"]["fit_band_ceiling"] == "UNCERTAIN"


def test_preferred_items_never_block_a_match():
    job = _job(5, "后端开发", [_req(1, "熟悉 Java"), _req(2, "有开源贡献者优先", "PREFERRED")])
    ranked = _rank([job])[0]
    components = ranked["ranking_components"]
    assert components["required_counts"]["SUPPORTED"] == 1
    assert components["preferred_supported"] == 0
    assert components["fit_band_ceiling"] in ("GOOD_MATCH", "STRONG_MATCH")


def test_non_technical_requirements_are_not_scored():
    job = _job(6, "后端开发", [_req(1, "2027届本科及以上学历", kind="EDUCATION"),
                              _req(2, "沟通表达清晰、有团队协作意识", kind="SOFT_SKILL")])
    ranked = _rank([job])[0]
    assert all(r["status"] == "NOT_APPLICABLE" for r in ranked["evidence_matrix"])
    assert ranked["ranking_components"]["evidence_basis"] == "NONE"


def test_javascript_does_not_satisfy_java():
    assert "java" not in technical_terms("熟悉 JavaScript 与 TypeScript")
    assert "java" in technical_terms("熟悉 Java 与 Spring")


def test_policy_version_is_bumped_so_old_runs_are_not_reused():
    assert POLICY_VERSION == "literal-evidence-eligibility-v2"
