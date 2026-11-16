"""R32: requirement vocabulary + compound-sentence strength.

Offline only: no database, no network, no model.
"""

from __future__ import annotations

import pytest

from interview_api.modules.public_career.requirement_vocabulary import (
    RequirementStrength, RequirementType,
    describe, effective_strength, is_compound_mixed, normalize_requirement_type,
    normalize_strength, split_strength_clauses, technical_clauses,
)


@pytest.mark.parametrize("raw,expected", [
    ("SKILL", RequirementType.SKILL),
    ("skill", RequirementType.SKILL),
    ("技能", RequirementType.SKILL),
    ("技术技能", RequirementType.SKILL),
    ("编程能力", RequirementType.SKILL),
    ("计算机基础", RequirementType.SKILL),
    ("EDUCATION", RequirementType.EDUCATION),
    ("education", RequirementType.EDUCATION),
    ("学历", RequirementType.EDUCATION),
    ("MAJOR", RequirementType.MAJOR),
    ("专业要求", RequirementType.MAJOR),
    ("GRADUATION_YEAR", RequirementType.GRADUATION_YEAR),
    ("毕业年份", RequirementType.GRADUATION_YEAR),
    ("LOCATION", RequirementType.LOCATION),
    ("工作地点", RequirementType.LOCATION),
    ("ELIGIBILITY", RequirementType.ELIGIBILITY),
    ("硬性条件", RequirementType.ELIGIBILITY),
    ("SOFT_SKILL", RequirementType.SOFT_SKILL),
    ("软技能", RequirementType.SOFT_SKILL),
    ("通用能力", RequirementType.SOFT_SKILL),
    ("EXPERIENCE", RequirementType.EXPERIENCE),
    ("领域兴趣/经验", RequirementType.EXPERIENCE),
])
def test_known_synonyms_map_to_one_canonical_type(raw, expected):
    result = normalize_requirement_type(raw)
    assert result.value is expected
    assert result.status == "KNOWN"


@pytest.mark.parametrize("raw", ["", None, "   ", "有意思的标签", "SKILL/EXPERIENCE", "??", "bonus"])
def test_unmapped_labels_never_become_technical(raw):
    result = normalize_requirement_type(raw)
    assert result.value is RequirementType.UNKNOWN
    assert result.status == "UNKNOWN"
    assert result.can_be_technical is False


@pytest.mark.parametrize("raw", ["学历/专业", "education/major"])
def test_multi_category_labels_are_ambiguous_but_not_technical(raw):
    result = normalize_requirement_type(raw)
    assert result.status == "AMBIGUOUS"
    assert result.is_hard_eligibility is True
    assert result.can_be_technical is False


@pytest.mark.parametrize("raw,expected", [
    ("REQUIRED", RequirementStrength.REQUIRED), ("required", RequirementStrength.REQUIRED),
    ("必须", RequirementStrength.REQUIRED), ("PREFERRED", RequirementStrength.PREFERRED),
    ("preferred", RequirementStrength.PREFERRED), ("加分项", RequirementStrength.PREFERRED),
    ("优先", RequirementStrength.PREFERRED), ("", RequirementStrength.UNKNOWN), (None, RequirementStrength.UNKNOWN),
])
def test_strength_synonyms(raw, expected):
    assert normalize_strength(raw) is expected


# ── Compound sentences ──────────────────────────────────────────────────────

KIRIN_LINUX = ("熟悉Linux操作系统，了解系统API、进程权限机制，了解计算机体系结构与异构计算基础，"
               "有CUDA开发、高性能计算经验者优先")


def test_kylinos_linux_sentence_splits_into_three_mandatory_and_one_preferred():
    clauses = split_strength_clauses(KIRIN_LINUX)
    assert [c.text for c in clauses] == [
        "熟悉Linux操作系统", "了解系统API、进程权限机制",
        "了解计算机体系结构与异构计算基础", "有CUDA开发、高性能计算经验者优先",
    ]
    assert [c.strength for c in clauses] == [RequirementStrength.REQUIRED] * 3 + [RequirementStrength.PREFERRED]
    assert is_compound_mixed(KIRIN_LINUX) is True


def test_mixed_sentence_stays_required_even_if_stored_preferred():
    # This is the R31 defect: stored PREFERRED because of the trailing 优先.
    assert effective_strength(KIRIN_LINUX, "PREFERRED") is RequirementStrength.REQUIRED
    assert technical_clauses(KIRIN_LINUX) == [
        "熟悉Linux操作系统", "了解系统API、进程权限机制", "了解计算机体系结构与异构计算基础",
    ]


def test_whole_sentence_preferred_stays_preferred():
    statement = "有开源贡献、开发者社区运营或 AI 应用生态建设经验者优先"
    assert effective_strength(statement, "PREFERRED") is RequirementStrength.PREFERRED
    assert is_compound_mixed(statement) is False


def test_plain_required_sentence_keeps_recorded_strength():
    statement = "熟练掌握Python，熟悉C/C++/Go/Java等至少一种编程语言"
    assert effective_strength(statement, "REQUIRED") is RequirementStrength.REQUIRED


def test_conditional_preference_is_preferred_not_mandatory():
    statement = "同等条件下，有智能汽车或端侧 AI 经验者优先"
    assert effective_strength(statement, "PREFERRED") is RequirementStrength.PREFERRED


def test_semicolon_separated_mixed_clauses_are_detected():
    statement = "本科及以上学历；有大规模分布式系统经验者优先"
    clauses = split_strength_clauses(statement)
    assert [c.strength for c in clauses] == [RequirementStrength.REQUIRED, RequirementStrength.PREFERRED]
    assert effective_strength(statement, "PREFERRED") is RequirementStrength.REQUIRED


def test_describe_reports_the_review_projection_without_mutating_input():
    statement = KIRIN_LINUX
    out = describe(statement, "skill", "PREFERRED")
    assert out["type"] == "SKILL"
    assert out["effective_strength"] == "REQUIRED"
    assert out["compound_mixed"] is True
    assert len(out["clauses"]) == 4
    assert statement == KIRIN_LINUX


# ── Matching consumption boundary ───────────────────────────────────────────

def _job(requirements):
    return {"id": 1, "jd_id": 1, "requirements": requirements}


def _profile():
    return {"id": 1, "facts": [], "evidence": []}


@pytest.mark.parametrize("requirement_type", ["EDUCATION", "education", "学历", "学历/专业", "MAJOR", "专业要求"])
def test_education_labels_are_not_counted_as_a_technical_gap(requirement_type):
    from interview_api.modules.job_matching.policy import evidence_matrix
    rows = evidence_matrix(_job([{"id": 1, "statement": "本科及以上学历", "requirement_type": requirement_type,
                                  "normalized_value": "本科"}]), _profile())
    assert rows[0]["status"] == "NOT_APPLICABLE"
    assert "硬资格条件" in rows[0]["message"]


@pytest.mark.parametrize("requirement_type", ["某些未知标签", "", "SKILL/EXPERIENCE", "bonus"])
def test_unknown_category_is_not_scored_as_technical(requirement_type):
    from interview_api.modules.job_matching.policy import evidence_matrix
    rows = evidence_matrix(_job([{"id": 1, "statement": "了解Kubernetes", "requirement_type": requirement_type,
                                  "normalized_value": "Kubernetes"}]), _profile())
    assert rows[0]["status"] == "NOT_APPLICABLE"
    assert "需人工确认" in rows[0]["message"]


@pytest.mark.parametrize("requirement_type", ["SKILL", "skill", "技能", "编程能力"])
def test_skill_labels_still_enter_technical_alignment(requirement_type):
    from interview_api.modules.job_matching.policy import evidence_matrix
    rows = evidence_matrix(_job([{"id": 1, "statement": "熟悉Python", "requirement_type": requirement_type,
                                  "normalized_value": "Python"}]), _profile())
    assert rows[0]["status"] == "NO_EVIDENCE"
    assert "没有相关证明材料" in rows[0]["message"]


def test_schema_coerces_on_ingestion_and_keeps_unknown_distinct():
    from interview_api.modules.public_career.research.schemas import RequirementExtraction
    evidence = ["ev_" + "a" * 24]
    assert RequirementExtraction(statement="x", evidence_ids=evidence, requirement_type="技能",
                                 importance="preferred").requirement_type == "SKILL"
    assert RequirementExtraction(statement="x", evidence_ids=evidence, requirement_type="技能",
                                 importance="preferred").importance == "PREFERRED"
    unmapped = RequirementExtraction(statement="x", evidence_ids=evidence, requirement_type="奇怪的标签",
                                     importance="必须")
    assert unmapped.requirement_type == "UNKNOWN"
    assert unmapped.importance == "REQUIRED"
