"""R36: contract for the minimal correction path (M1).

Two layers are pinned here:

  * `literal_within` — the correction gate. It must accept restored page wording
    and reject plausible-sounding sentences that are not in the cited text. It may
    join adjacent slices, never bridge a gap.
  * the rehearsal artefact — what actually happened when the path was run against
    the two held JDs. The write is refused by an existing schema constraint, so the
    artefact must show `blocked_by` and zero rows written.

No model, no network, no database (the rehearsal already happened in a rolled-back
savepoint; this file only reads its output).
"""

from __future__ import annotations

import json
from pathlib import Path

from interview_api.modules.public_career.corrections import literal_within

DATA = Path(__file__).resolve().parents[3] / "data"
ARTEFACT = DATA / "r54_corrections/corrections.json"


def slice_(start: int, end: int, excerpt: str, snapshot: int = 1) -> dict:
    return {"source_snapshot_id": snapshot, "start": start, "end": end, "excerpt": excerpt}


# ── the gate itself ──────────────────────────────────────────────────────────
def test_restored_page_wording_is_accepted():
    evidence = [slice_(0, 40, "…降低推理成本，包括但不限于:算子/编译优化、异构推理、分布式并行等；")]
    assert literal_within(evidence, "…降低推理成本，包括但不限于:算子/编译优化、异构推理、分布式并行等")


def test_a_removed_qualifier_can_be_put_back_only_if_it_is_in_the_text():
    evidence = [slice_(0, 30, "降低推理成本，包括但不限于:算子/编译优化")]
    assert literal_within(evidence, "降低推理成本，包括但不限于:算子/编译优化")
    # "包括但不限于:算子/编译优化、量化" is not in this slice.
    assert not literal_within(evidence, "降低推理成本，包括但不限于:算子/编译优化、量化")


def test_invented_technical_scope_is_rejected():
    evidence = [slice_(0, 28, "熟悉至少一种主流的深度学习框架(如 PyTorch、TensorFlow)")]
    assert not literal_within(evidence, "熟悉至少一种主流的深度学习框架，了解其编译器后端实现")


def test_adjacent_slices_are_joined_and_gaps_are_not_bridged():
    adjacent = [slice_(0, 3, "第一段"), slice_(3, 6, "第二段")]
    assert literal_within(adjacent, "第一段第二段")
    gapped = [slice_(0, 3, "第一段"), slice_(10, 13, "第二段")]
    assert not literal_within(gapped, "第一段第二段")


def test_normalisation_ignores_width_and_case_but_not_words():
    """`normalize_text` is NFKC + whitespace collapse + casefold: width and case
    differences pass, a re-ordered list or a dropped space does not."""
    evidence = [slice_(0, 40, "熟悉至少一种主流的深度学习框架(如 PyTorch、TensorFlow)")]
    assert literal_within(evidence, "熟悉至少一种主流的深度学习框架（如 PyTorch、TensorFlow）")
    assert literal_within(evidence, "熟悉至少一种主流的深度学习框架(如 pytorch、tensorflow)")
    assert not literal_within(evidence, "熟悉至少一种主流的深度学习框架(如 TensorFlow、PyTorch)")
    assert not literal_within(evidence, "熟悉至少一种主流的深度学习框架(如PyTorch、TensorFlow)")


def test_empty_or_missing_evidence_is_rejected():
    assert literal_within([], "任何句子") is False
    assert literal_within([slice_(0, 4, "包括但不限于")], "   ") is False


# ── what the rehearsal actually produced ────────────────────────────────────
def test_four_corrections_restore_page_wording_and_all_are_literal():
    payload = json.loads(ARTEFACT.read_text(encoding="utf-8"))
    runs = payload["runs"]
    assert len(runs) == 4
    assert {(row["jd_version_id"], row["plan"][0]["requirement_id"]) for row in runs} == {
        (4, 40), (6, 57), (6, 58), (6, 63)}
    for row in runs:
        plan = row["plan"][0]
        assert plan["after_supported"] is True, row["label"]
        # The model's own wording is not literal — that is why it is being fixed.
        assert plan["before_supported"] is False, row["label"]


def test_the_write_is_refused_by_the_schema_and_leaves_no_rows():
    payload = json.loads(ARTEFACT.read_text(encoding="utf-8"))
    for row in payload["runs"]:
        assert row["applied"] is False
        assert row["rolled_back"] is True
        assert row["blocked_by"] == "SCHEMA_UNIQUE_FINGERPRINT"
    assert payload["rows_written"] == {"versions": 0, "requirements": 0, "claims": 0,
                                      "evidence": 0, "audit": 0}
    assert payload["before"] == payload["after"]


def test_a_fabricated_correction_is_refused_before_any_write():
    payload = json.loads(ARTEFACT.read_text(encoding="utf-8"))
    assert payload["negative"]["rejected"] is True
    assert payload["negative"]["code"] == "CORRECTION_NOT_LITERAL"


def test_correction_targets_only_held_versions():
    payload = json.loads(ARTEFACT.read_text(encoding="utf-8"))
    assert {row["jd_version_id"] for row in payload["runs"]} == {4, 6}  # both WAITING_REVIEW


def test_vernacular_self_check_of_the_gate():
    payload = json.loads(ARTEFACT.read_text(encoding="utf-8"))
    check = payload["literal_within_selfcheck"]
    assert check["supported"] is True
    assert check["substring_still_supported"] is True
    assert check["unsupported"] is False
    assert check["gap_not_bridged"] is False
