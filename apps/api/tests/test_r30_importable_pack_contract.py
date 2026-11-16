"""R30：《2026_真实岗位与面经_可入库纠正版》导入契约（0 HTTP）。

关键边界（包内 README 与任务书共同要求）：
* 15 份是**改写**，不是官网逐字原文 → `full_verbatim_from_official=false`、`evidence_verified=false`；
* 50 份是**完全合成**，不得被当成真实招聘 → `is_real_job=false`、公司名自带「合成」；
* 3 场面经是发帖者自述，39 道题是按主题改写，不是模型出题也不是逐字记录；
* 40 份答案是合成 GOOD/POOR，不是人工金标，不含个人数据。

同时断言导入只落在 `sbx_*` 夹具表、业务表仍为 0 行。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK = REPO_ROOT / "data/incoming_20261116b/2026_真实岗位与面经_可入库纠正版"
ARTIFACTS = REPO_ROOT / "data/incoming_20261116b"


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def sourced() -> list[dict]:
    return load(PACK / "01_真实岗位来源_完整结构化改写_非逐字.jsonl")


@pytest.fixture(scope="module")
def synthetic() -> list[dict]:
    return load(PACK / "02_50份完整合成JD_可直接测试入库.jsonl")


@pytest.fixture(scope="module")
def import_result() -> dict:
    return json.loads((ARTIFACTS / "import_result.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def analysis() -> dict:
    return json.loads((ARTIFACTS / "pack_analysis.json").read_text(encoding="utf-8"))


def test_pack_checksums():
    expected = {}
    for line in (PACK / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            expected[name.strip()] = digest
    assert len(expected) == 12
    for name, digest in expected.items():
        actual = hashlib.sha256((PACK / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} 校验失败"


def test_import_landed_only_in_sandbox_fixture_tables(import_result):
    assert "_sandbox_" in import_result["database"]
    assert import_result["http_calls_to_model"] == 0
    expected = {"sbx_jds_sourced": 15, "sbx_jds_synthetic": 50, "sbx_interview_flows": 3,
                "sbx_interview_questions": 39, "sbx_answer_pairs": 40}
    for table, count in expected.items():
        detail = import_result["tables"][table]
        assert detail["source_rows"] == count
        assert detail["stored_rows"] == count
        assert detail["hash_mismatched"] == []
    assert import_result["business_tables_untouched"] is True
    assert all(value in (0, None) for value in import_result["business_tables_after_import"].values())


def test_sourced_jds_never_claim_verbatim_or_verified(sourced):
    assert len(sourced) == 15
    for row in sourced:
        assert row["full_verbatim_from_official"] is False
        assert row["evidence_verified"] is False
        assert row["provenance"] == "REAL_JOB_SOURCE_ORIGINAL_PARAPHRASE"
        assert row["body_type"] == "ORIGINAL_PARAPHRASE_OF_PUBLIC_JOB_NOT_VERBATIM"
        assert row["source_url"].startswith("http")
        assert len(row["jd_text"]) >= 200


def test_synthetic_jds_never_marked_real(synthetic):
    assert len(synthetic) == 50
    for row in synthetic:
        assert row["is_real_job"] is False
        assert row["source_verified"] is False
        assert row["provenance"] == "FULLY_SYNTHETIC_ORIGINAL_TEST_JD"
        assert row["source_url"] in (None, "")
        assert "合成" in row["company"] or "非真实" in row["company"]
        assert len(row["jd_text"]) >= 200


def test_no_duplicate_or_empty_jd(analysis, sourced, synthetic):
    duplicates = analysis["duplicates"]
    assert duplicates["within_sourced"] == []
    assert duplicates["within_synthetic"] == []
    assert duplicates["same_company_role"] == []
    assert duplicates["cross_manual"] == []
    assert analysis["empty_or_short_bodies"] == []
    assert analysis["missing_responsibility_or_requirement"] == []
    assert len({row["sha256"] for row in sourced + synthetic}) == 65


def test_provenance_problems_empty(analysis):
    assert analysis["provenance_problems"] == []


def test_parse_chain_extracts_requirements_for_every_jd(analysis):
    chain = analysis["parse_chain"]
    assert chain["sourced"]["with_requirements"] == 15
    assert chain["synthetic"]["with_requirements"] == 50
    assert chain["zero_requirement_jobs"] == []
    assert "RULE" in chain["note"]


def test_interview_flows_and_questions_match(analysis):
    flows = analysis["question_flows"]
    assert flows["declared"] == flows["per_flow"]
    assert sum(flows["per_flow"].values()) == 39
    assert set(flows["declared"].values()) == {16, 8, 15}


def test_question_turns_are_contiguous():
    rows = load(PACK / "04_三份2026面经_按顺序改写问题.jsonl")
    grouped: dict[str, list[int]] = {}
    for row in rows:
        grouped.setdefault(row["flow_id"], []).append(row["turn_index"])
    assert len(grouped) == 3
    for flow_id, turns in grouped.items():
        assert turns == list(range(1, len(turns) + 1)), f"{flow_id} 题号不连续"
        assert all(row["is_original_interviewer_wording"] is False for row in rows
                   if row["flow_id"] == flow_id)


def test_answers_are_synthetic_not_gold(analysis):
    answers = analysis["answers"]
    assert answers["labels"] == {"GOOD": 20, "POOR": 20}
    assert answers["unique_questions"] == 20
    assert answers["human_gold"] == 0
    assert answers["with_pii"] == 0


def test_formal_import_blockers_are_explicit(analysis):
    blockers = analysis["formal_import_blockers"]
    assert set(blockers) == {"verbatim_evidence", "requirement_labels", "owner_scope", "research_flow"}
    assert all(value.strip() for value in blockers.values())
