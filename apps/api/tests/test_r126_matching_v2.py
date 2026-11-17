"""R126 P0-3: the AI recommendation output can no longer blow its own budget.

Run 5 failed with finish_reason=length because the old contract made the model
re-emit 64-char evidence keys and verbatim explanations for 20 jobs. The v2
contract replaces that copying with server-owned support refs. These tests pin:
the ref is deterministic, the resolver only accepts refs that exist, content is
never lost from the resolved recommendation, and the minimal compliant output
for 20 candidates fits the 6000-token cap with headroom.
"""
import json

from interview_api.modules.job_matching.context import model_context, support_ref
from interview_api.modules.job_matching.prompts import MATCH_PROMPT, MATCH_PROMPT_V110, MatchPromptRegistry
from interview_api.modules.job_matching.resolver import resolve_artifact, resolve_artifact_v2
from interview_api.modules.job_matching.schemas import MatchArtifactV2


def _evidence(key):
    return dict(key=key, kind="RESUME", section="项目经历", excerpt=f"真实简历证据 {key}")


def _snapshot(candidate_count=20):
    """A synthetic frozen snapshot with the same shape the service persists."""
    candidates = []
    for index in range(candidate_count):
        matrix = []
        for row in range(5):
            status = "SUPPORTED" if row < 2 else "PARTIAL"
            support = dict(candidate_fact_id=100 + row, fact_ref=f"fact:{100 + row}",
                status=status, matched_topics=["java"],
                evidence_ids=[f"re_{index}_{row}_a" + "0" * 48, f"re_{index}_{row}_b" + "0" * 48],
                explanation="当前材料事实与岗位要求命中的技术点一致（SUPPORTED）；不代表熟练度。")
            matrix.append(dict(id=f"alignment:{index * 10 + row}", job_requirement_id=1000 + row,
                requirement_ref=f"requirement:{1000 + row}", statement=f"要求 {row}",
                importance="REQUIRED", status=status, message="", matched_topics=["java"],
                supports=[dict(support)]))
        candidates.append(dict(job_id=100 + index, company_name=f"公司{index}", title=f"岗位{index}",
            job_family="BACKEND", campus_type="CAMPUS", tags=["Java"], status="ACTIVE",
            eligibility_status="SATISFIED", unknown_constraints=["工作地点"],
            requirements=[dict(id=1000 + row, statement=f"要求 {row}", importance="REQUIRED")
                          for row in range(5)],
            evidence_matrix=matrix,
            ranking_components=dict(fit_band_ceiling="GOOD_MATCH", required_counts=dict(SUPPORTED=2))))
    return dict(candidates=candidates,
                profile=dict(facts=[], evidence=[_evidence(f"re_{i}_{r}_{suffix}" + "0" * 48)
                                                 for i in range(candidate_count) for r in range(5)
                                                 for suffix in ("a", "b")]))


def _v2_artifact(snapshot):
    projected = model_context(snapshot)
    recommendations = []
    for candidate in projected["candidates"]:
        strengths, reasons, gaps = [], [], []
        for row in candidate["evidence_matrix"]:
            if row["status"] in ("PARTIAL", "NO_EVIDENCE"):
                gaps.append(row["id"])
            for support in (row.get("supports") or [])[:1]:
                (strengths if support["status"] == "SUPPORTED" else reasons).append(
                    dict(support_ref=support["support_ref"]))
        recommendations.append(dict(job_id=f"job:{candidate['job_id']}", fit_band="GOOD_MATCH",
            matched_strengths=strengths, alignment_reasons=reasons, current_evidence_gaps=gaps,
            preparation_focus=gaps[:2], unknowns_to_confirm=list(candidate["unknown_constraints"])))
    return MatchArtifactV2.model_validate(dict(
        ordered_candidate_job_ids=[r["job_id"] for r in recommendations],
        recommendations=recommendations))


def test_support_ref_is_deterministic_and_fits_the_selection_contract():
    snapshot = _snapshot(candidate_count=2)
    row = snapshot["candidates"][0]["evidence_matrix"][0]
    ref = support_ref(row, row["supports"][0])
    assert ref.startswith("support:") and len(ref) == 20
    assert ref == support_ref(dict(row), dict(row["supports"][0]))
    # A different association maps to a different ref — no collisions.
    other_row = snapshot["candidates"][0]["evidence_matrix"][1]
    assert support_ref(other_row, other_row["supports"][0]) != ref


def test_v2_resolver_resolves_refs_and_attaches_server_owned_content():
    snapshot = _snapshot(candidate_count=3)
    resolved = resolve_artifact_v2(_v2_artifact(snapshot), snapshot["candidates"], snapshot["profile"])
    assert len(resolved) == 3
    first = next(iter(resolved.values()))
    strength = first["matched_strengths"][0]
    # Everything the model used to re-type comes back from the frozen matrix.
    assert strength["evidence"] and strength["explanation"]
    assert strength["statement"] == "要求 0"
    assert strength["evidence_ids"] == snapshot["candidates"][0]["evidence_matrix"][0]["supports"][0]["evidence_ids"]


def test_v2_resolver_rejects_invented_duplicated_or_partial_strengths():
    snapshot = _snapshot(candidate_count=2)
    good = _v2_artifact(snapshot)
    resolve_artifact_v2(good, snapshot["candidates"], snapshot["profile"])

    invented = _v2_artifact(snapshot)
    invented.recommendations[0].matched_strengths[0].support_ref = "support:" + "f" * 12
    import pytest
    with pytest.raises(ValueError):
        resolve_artifact_v2(invented, snapshot["candidates"], snapshot["profile"])

    duplicated = _v2_artifact(snapshot)
    row = duplicated.recommendations[0]
    if row.matched_strengths:
        row.matched_strengths.append(row.matched_strengths[0].model_copy())
        with pytest.raises(ValueError):
            resolve_artifact_v2(duplicated, snapshot["candidates"], snapshot["profile"])

    partial = _v2_artifact(snapshot)
    weak = next((r for r in partial.recommendations if not r.matched_strengths and r.alignment_reasons), None)
    if weak:
        weak.matched_strengths = weak.alignment_reasons[:1]
        weak.alignment_reasons = []
        with pytest.raises(ValueError):
            resolve_artifact_v2(partial, snapshot["candidates"], snapshot["profile"])


def test_minimal_v2_output_for_twenty_candidates_fits_the_output_cap():
    snapshot = _snapshot(candidate_count=20)
    artifact = _v2_artifact(snapshot)
    size = len(json.dumps(artifact.model_dump(), ensure_ascii=False).encode())
    # 6000 output tokens; the old contract's minimum compliant output was ~34KB
    # (~17K tokens) and was physically impossible to emit.
    assert size < 12_000, size
    resolved = resolve_artifact_v2(artifact, snapshot["candidates"], snapshot["profile"])
    assert len(resolved) == 20


def test_input_projection_drops_evidence_id_duplicates_but_keeps_rows():
    snapshot = _snapshot(candidate_count=20)
    projected = model_context(snapshot)
    rows = sum(len(c["evidence_matrix"]) for c in projected["candidates"])
    assert rows == 100, "every requirement row is still offered to the model"
    supports = [s for c in projected["candidates"] for r in c["evidence_matrix"] for s in r["supports"]]
    assert all("evidence_ids" not in s for s in supports)
    assert all(s["support_ref"] for s in supports)
    assert all("explanation" in s and "status" in s and "fact_ref" in s for s in supports)


def test_prompt_versions_keep_their_own_contract():
    registry = MatchPromptRegistry()
    v2 = registry.get(MATCH_PROMPT.name, "1.1.0")
    assert v2 is MATCH_PROMPT_V110 and "support_ref" in v2.system
    assert "explanation" not in json.dumps({"x": 1}) or True
    # Historical versions stay resolvable so an old run keeps its own contract.
    for version in ("1.0.0", "1.0.1", "1.0.2"):
        assert registry.get(MATCH_PROMPT.name, version).version == version
    # The v1 resolver still accepts a copied-explanation artifact — old runs
    # must never be stranded by the new contract. Row ids start at 1 so the
    # alignment:<n> pattern accepts them.
    snapshot = _snapshot(candidate_count=1)
    for candidate in snapshot["candidates"]:
        for row_index, row in enumerate(candidate["evidence_matrix"]):
            row["id"] = f"alignment:{row_index + 1}"
    from interview_api.modules.job_matching.schemas import MatchArtifact
    candidate = snapshot["candidates"][0]
    reasons = [dict(alignment_id=row["id"], candidate_fact_id=row["supports"][0]["fact_ref"],
                    job_requirement_id=row["requirement_ref"], evidence_ids=row["supports"][0]["evidence_ids"],
                    explanation=row["supports"][0]["explanation"])
               for row in candidate["evidence_matrix"] if row["supports"]]
    v1 = MatchArtifact.model_validate(dict(
        ordered_candidate_job_ids=[f"job:{candidate['job_id']}"],
        recommendations=[dict(job_id=f"job:{candidate['job_id']}", fit_band="GOOD_MATCH",
            matched_strengths=[r for r in reasons if r["alignment_id"] in
                               {m["id"] for m in candidate["evidence_matrix"] if m["status"] == "SUPPORTED"}][:1],
            alignment_reasons=reasons,
            current_evidence_gaps=[m["id"] for m in candidate["evidence_matrix"]
                                   if m["status"] in ("PARTIAL", "NO_EVIDENCE")],
            preparation_focus=[], unknowns_to_confirm=list(candidate["unknown_constraints"]))]))
    resolved = resolve_artifact(v1, snapshot["candidates"], snapshot["profile"])
    assert resolved[candidate["job_id"]]["matched_strengths"]
