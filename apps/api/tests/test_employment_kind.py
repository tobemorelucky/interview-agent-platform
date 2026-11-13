"""Round 19: employment kind comes from the resume's own sections.

Every resume here is synthetic and marked as such. The sample labelled
"round 18 A" reproduces the synthetic resume that produced the real defect in
round 18 (a full-time job reported under `internships`) without any real
person's data.
"""
import pytest

from interview_api.modules.candidate_profile.evidence import normalize_employment_categories
from interview_api.modules.candidate_profile.schemas import CandidateProfileDelta
from interview_api.modules.resume.employment import POLICY, reconcile_employment
from interview_api.modules.resume.sections import stable_sections
from test_profile_context import catalog

# ── fixtures ──────────────────────────────────────────────────────────────

ROUND18_A = "\n".join([
    "张一凡（合成测试样本，非真实个人信息）",
    "求职意向：AI Agent 应用开发工程师",
    "教育经历",
    "2019.09-2023.06  华东某大学  计算机科学与技术  本科",
    "专业技能",
    "编程语言：Python（熟练，日常开发主力）、Java（了解）、Go（没有使用过）",
    "工作经历",
    "2023.07-2025.06  某互联网公司  Python 后端开发工程师",
    "负责订单服务的 Python 接口开发与维护，使用 FastAPI 与 PostgreSQL，峰值 QPS 约 800。",
    "参与 Redis 缓存的键设计与过期策略调整，把热点查询耗时从 120ms 降到 40ms。",
    "为消息通知做过一次 Kafka 接入预研，最终没有上线，因此没有生产环境的 Kafka 运维经验。",
    "项目经历",
    "企业内部知识库问答助手（2025.03-2025.06）",
    "使用 Python 与 LangGraph 搭建多轮检索问答流程，包含意图识别、检索、重排与答案生成四个节点。",
    "该项目只在公司内网小范围试用，约 30 名用户，没有做过正式的线上部署与容量压测。",
])

ROUND18_A_ENTRY = {
    "company": "某互联网公司",
    "role": "Python 后端开发工程师",
    "duration": "2023.07-2025.06",
    "responsibilities": [
        "负责订单服务的 Python 接口开发与维护，使用 FastAPI 与 PostgreSQL，峰值 QPS 约 800",
        "参与 Redis 缓存的键设计与过期策略调整，把热点查询耗时从 120ms 降到 40ms",
        "为消息通知做过一次 Kafka 接入预研，最终没有上线",
    ],
    "tech_stack": ["Python", "FastAPI", "PostgreSQL", "Redis", "Kafka"],
}

BOTH = "\n".join([
    "李四（合成测试样本）",
    "工作经历",
    "2021.07-2025.06  DEMO 云计算公司  后端开发工程师",
    "负责结算服务接口开发，使用 Java 与 MySQL。",
    "实习经历",
    "2020.07-2020.09  DEMO 电商公司  后端开发实习生",
    "参与商品搜索接口的联调与测试。",
])

PROJECT_ONLY = "\n".join([
    "王五（合成测试样本）",
    "项目经历",
    "DEMO 课程设计项目  二手交易平台",
    "独立完成 Django 后端与 MySQL 表结构设计。",
])

OTHER_SECTION = "\n".join([
    "赵六（合成测试样本）",
    "工作经历",
    "2022.03-2025.06  DEMO 支付公司  服务端开发",
    "负责清结算批处理任务。",
    "其他经历",
    "DEMO 开源社区  贡献者",
    "为开源项目提交过文档修订。",
])

NO_HEADINGS = "\n".join([
    "孙七（合成测试样本）",
    "DEMO 物流公司  后端开发  2023.01-2025.06",
    "负责运单查询接口开发。",
])

CONTENTS_BLOCK = "\n".join([
    "周八（合成测试样本）",
    "目录",
    "教育经历",
    "工作经历",
    "项目经历",
    "教育经历",
    "2018.09-2022.06  DEMO 大学  软件工程  本科",
    "工作经历",
    "2022.07-2025.06  DEMO 银行科技  开发工程师",
    "负责核心系统接口改造。",
    "项目经历",
    "DEMO 对账平台",
    "使用 Python 完成对账任务调度。",
])

SAME_COMPANY = "\n".join([
    "吴九（合成测试样本）",
    "工作经历",
    "2023.07-2025.06  DEMO 科技公司  后端开发工程师",
    "负责订单履约服务。",
    "实习经历",
    "2022.07-2022.09  DEMO 科技公司  后端开发实习生",
    "参与日志检索工具开发。",
])


def entry(company, role, responsibilities, tech=None, duration=""):
    return dict(company=company, role=role, duration=duration,
                responsibilities=list(responsibilities), tech_stack=list(tech or []))


def report(*entries, bucket="internships"):
    return {bucket: list(entries)}


# ── resume report reconciliation ─────────────────────────────────────────


def test_round18_full_time_job_is_not_an_internship_any_more():
    """The exact round-18 failure: work text reported under `internships`."""
    result, audit = reconcile_employment(report(ROUND18_A_ENTRY), ROUND18_A)
    assert audit["counts"] == dict(WORK=1, INTERNSHIP=0, CONFLICT=0, UNRESOLVED=0)
    assert [item["company"] for item in result["work_experience"]] == ["某互联网公司"]
    assert result["internships"] == []
    assert audit["entries"][0]["reported_as"] == "internships"
    assert audit["entries"][0]["resolved"] == "WORK"


def test_full_time_only_resume():
    result, audit = reconcile_employment(report(ROUND18_A_ENTRY), ROUND18_A)
    assert len(result["work_experience"]) == 1 and not result["internships"]


def test_internship_only_resume():
    intern = entry("DEMO 电商公司", "后端开发实习生", ["参与商品搜索接口的联调与测试"])
    text = "\n".join(["李四（合成测试样本）", "实习经历", "2020.07-2020.09  DEMO 电商公司  后端开发实习生",
                      "参与商品搜索接口的联调与测试。"])
    result, audit = reconcile_employment(report(intern, bucket="work_experience"), text)
    assert audit["counts"] == dict(WORK=0, INTERNSHIP=1, CONFLICT=0, UNRESOLVED=0)
    assert result["internships"] == [intern] and result["work_experience"] == []


def test_work_and_internship_in_one_resume_are_split():
    work = entry("DEMO 云计算公司", "后端开发工程师", ["负责结算服务接口开发"])
    intern = entry("DEMO 电商公司", "后端开发实习生", ["参与商品搜索接口的联调与测试"])
    result, audit = reconcile_employment(report(work, intern), BOTH)
    assert [item["company"] for item in result["work_experience"]] == ["DEMO 云计算公司"]
    assert [item["company"] for item in result["internships"]] == ["DEMO 电商公司"]
    assert audit["counts"] == dict(WORK=1, INTERNSHIP=1, CONFLICT=0, UNRESOLVED=0)


def test_project_text_cannot_become_work_experience():
    project = entry("DEMO 课程设计项目", "二手交易平台", ["独立完成 Django 后端与 MySQL 表结构设计"])
    result, audit = reconcile_employment(report(project, bucket="work_experience"), PROJECT_ONLY)
    assert result["work_experience"] == [] and result["internships"] == []
    assert result["unclassified_experience"] == [project]
    assert audit["counts"]["UNRESOLVED"] == 1


def test_generic_section_keeps_the_entry_unclassified():
    other = entry("DEMO 开源社区", "贡献者", ["为开源项目提交过文档修订"])
    result, audit = reconcile_employment(report(other, bucket="internships"), OTHER_SECTION)
    assert result["internships"] == []
    assert result["unclassified_experience"] == [other]
    assert audit["entries"][0]["resolved"] == "UNRESOLVED"


def test_no_heading_at_all_is_not_guessed():
    job = entry("DEMO 物流公司", "后端开发", ["负责运单查询接口开发"])
    result, audit = reconcile_employment(report(job), NO_HEADINGS)
    assert audit["counts"]["INTERNSHIP"] == 0
    assert result["internships"] == [] and result["unclassified_experience"] == [job]


def test_model_bucket_conflicting_with_explicit_section_is_moved():
    work = entry("DEMO 银行科技", "开发工程师", ["负责核心系统接口改造"])
    result, audit = reconcile_employment(report(work, bucket="internships"), CONTENTS_BLOCK)
    assert result["work_experience"] == [work]
    assert result["internships"] == []
    assert "目录" not in result["employment_kind"]["entries"][0].get("sections", [])


def test_contents_block_does_not_hijack_following_sections():
    sections = [section for _, section in stable_sections(CONTENTS_BLOCK)]
    first_education = CONTENTS_BLOCK.splitlines().index("2018.09-2022.06  DEMO 大学  软件工程  本科")
    assert sections[first_education] == "education"
    work_line = CONTENTS_BLOCK.splitlines().index("2022.07-2025.06  DEMO 银行科技  开发工程师")
    assert sections[work_line] == "work"
    project_line = CONTENTS_BLOCK.splitlines().index("DEMO 对账平台")
    assert sections[project_line] == "projects"


def test_same_company_in_both_sections_uses_discriminating_evidence():
    work = entry("DEMO 科技公司", "后端开发工程师", ["负责订单履约服务"])
    intern = entry("DEMO 科技公司", "后端开发实习生", ["参与日志检索工具开发"])
    result, audit = reconcile_employment(report(work, intern), SAME_COMPANY)
    assert [item["role"] for item in result["work_experience"]] == ["后端开发工程师"]
    assert [item["role"] for item in result["internships"]] == ["后端开发实习生"]
    assert audit["counts"] == dict(WORK=1, INTERNSHIP=1, CONFLICT=0, UNRESOLVED=0)


def test_reconciliation_never_mutates_input_or_loses_entries():
    original = report(ROUND18_A_ENTRY)
    snapshot = {key: [dict(item) for item in value] for key, value in original.items()}
    result, _ = reconcile_employment(original, ROUND18_A)
    assert original == snapshot
    total = sum(len(value) for value in result.values() if isinstance(value, list))
    assert total == 1
    assert result["employment_kind"]["policy"] == POLICY


def test_empty_raw_text_is_not_reclassified():
    result, audit = reconcile_employment(report(ROUND18_A_ENTRY), "")
    assert result["internships"] == [ROUND18_A_ENTRY]
    assert audit["skipped"] == "NO_RAW_TEXT"


# ── profile-side verification ────────────────────────────────────────────


def _delta(raw, category, section):
    items = catalog(raw)
    key = next(key for key, item in items.items() if item["section"] == section)
    excerpt = items[key]["excerpt"]
    fact = dict(label=excerpt[:30], statement=excerpt, evidence_ids=[key])
    return CandidateProfileDelta.model_validate({category: [fact]}), items


@pytest.mark.parametrize("raw,section,category,expected", [
    (ROUND18_A, "work", "internship_experience", "work_experience"),
    (BOTH, "internship", "work_experience", "internship_experience"),
])
def test_profile_moves_a_fact_to_the_section_it_quotes(raw, section, category, expected):
    delta, items = _delta(raw, category, section)
    audit = normalize_employment_categories(delta, items)
    assert audit["decisions"][0]["resolution"] == "MOVED"
    assert audit["decisions"][0]["moved_to"] == expected
    assert len(getattr(delta, expected)) == 1 and getattr(delta, category) == []


def test_profile_drops_a_work_fact_supported_only_by_projects():
    delta, items = _delta(PROJECT_ONLY, "work_experience", "projects")
    audit = normalize_employment_categories(delta, items)
    assert delta.work_experience == []
    assert audit["dropped"][0]["resolution"] == "DROPPED_PROJECT_EVIDENCE_ONLY"
    assert delta.unknowns and "项目小节" in delta.unknowns[0]


def test_profile_drops_a_fact_that_quotes_two_employment_sections():
    items = catalog(BOTH)
    work_key = next(key for key, item in items.items() if item["section"] == "work")
    intern_key = next(key for key, item in items.items() if item["section"] == "internship")
    excerpt = items[work_key]["excerpt"]
    delta = CandidateProfileDelta.model_validate({"internship_experience": [
        dict(label=excerpt[:30], statement=excerpt, evidence_ids=[work_key, intern_key])]})
    audit = normalize_employment_categories(delta, items)
    assert delta.internship_experience == []
    assert audit["dropped"][0]["resolution"] == "DROPPED_CONFLICTING_SECTIONS"


def test_profile_keeps_a_fact_when_the_resume_has_no_sections_at_all():
    delta, items = _delta(NO_HEADINGS, "work_experience", "general")
    audit = normalize_employment_categories(delta, items)
    assert len(delta.work_experience) == 1
    assert audit["unverified"][0]["resolution"] == "UNVERIFIED_NO_EMPLOYMENT_HEADING"


def test_profile_drops_an_unsectioned_fact_when_other_sections_exist():
    items = catalog(ROUND18_A)
    general_key = next(key for key, item in items.items() if item["section"] == "general")
    excerpt = items[general_key]["excerpt"]
    delta = CandidateProfileDelta.model_validate({"internship_experience": [
        dict(label=excerpt[:20], statement=excerpt, evidence_ids=[general_key])]})
    audit = normalize_employment_categories(delta, items)
    assert delta.internship_experience == []
    assert audit["dropped"][0]["resolution"] == "DROPPED_NO_EMPLOYMENT_SECTION"


def test_profile_keeps_label_and_statement_verbatim_when_moving():
    delta, items = _delta(ROUND18_A, "internship_experience", "work")
    before = delta.internship_experience[0].model_dump()
    normalize_employment_categories(delta, items)
    after = delta.work_experience[0].model_dump()
    assert after == before
