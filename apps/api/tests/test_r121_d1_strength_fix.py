"""R121 D1: an "或" alternative chain is one preference, not a hard filter.

The three R120 rows were classified, not blanket-promoted: two are complete
preference expressions and are fixed here; the third is a genuine mandatory
clause plus a preference and deliberately stays REQUIRED.
"""
from __future__ import annotations

import pytest

from interview_api.modules.public_career.requirement_vocabulary import (
    RequirementStrength,
    consumer_strength,
    effective_strength,
    is_compound_mixed,
    split_strength_clauses,
)

OR_1 = "有漏洞挖掘、CTF竞赛经验，或安卓系统安全开发经验者优先"
OR_2 = ("有Linux内核漏洞挖掘经验（提交过CVE或给上游提交过修复补丁者优先），"
        "或具备Syzkaller、Trinity等Fuzzing工具二次开发经验者优先")
MIXED = ("熟悉 Verl、Slime、ms-swift、OpenRLHF、TRL 等训练或强化学习框架，"
         "了解分布式训练、推理服务和评测 infra 者优先")
HARD_PLUS_OR = "必须熟悉Linux操作系统，或有Go经验者优先"
KIRIN = ("熟悉Linux操作系统，了解系统API、进程权限机制，"
         "了解计算机体系结构与异构计算基础，有CUDA开发、高性能计算经验者优先")


@pytest.mark.parametrize("statement", (OR_1, OR_2))
def test_or_chain_with_trailing_preference_is_one_preferred_clause(statement):
    clauses = split_strength_clauses(statement)
    assert len(clauses) == 1
    assert clauses[0].strength is RequirementStrength.PREFERRED
    assert is_compound_mixed(statement) is False
    assert effective_strength(statement, "PREFERRED") is RequirementStrength.PREFERRED
    assert consumer_strength(statement, "PREFERRED") is RequirementStrength.PREFERRED


def test_real_mixed_sentence_still_produces_a_hard_clause():
    assert is_compound_mixed(MIXED) is True
    assert [c.strength for c in split_strength_clauses(MIXED)] == [
        RequirementStrength.REQUIRED, RequirementStrength.PREFERRED,
    ]
    assert consumer_strength(MIXED, "PREFERRED") is RequirementStrength.REQUIRED
    assert consumer_strength(KIRIN, "PREFERRED") is RequirementStrength.REQUIRED


def test_explicit_mandatory_clause_is_not_absorbed_into_an_or_chain():
    assert is_compound_mixed(HARD_PLUS_OR) is True
    assert consumer_strength(HARD_PLUS_OR, "PREFERRED") is RequirementStrength.REQUIRED


def test_unknown_and_unmarked_rows_are_unchanged():
    assert consumer_strength(OR_1, "UNKNOWN").value == "UNKNOWN"
    assert consumer_strength("参与大模型推理引擎的设计和研发", None).value == "UNKNOWN"
    assert consumer_strength("熟练掌握Python，熟悉C/C++等至少一种语言", "REQUIRED").value == "REQUIRED"


def test_merged_clause_keeps_exact_source_text_and_offset():
    clauses = split_strength_clauses(OR_1)
    assert clauses[0].text == OR_1
    assert clauses[0].offset == 0
