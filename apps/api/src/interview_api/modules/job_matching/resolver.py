"""Models select supplied references; deterministic code owns all evidence/content."""
from interview_api.modules.job_matching.policy import BANDS, GAP_MESSAGE


def resolve_artifact(artifact, candidates, profile):
    pool = {f"job:{c['job_id']}": c for c in candidates if c["eligibility_status"] != "NOT_SATISFIED"}
    order = artifact.ordered_candidate_job_ids
    if len(order) != len(set(order)) or set(order) != set(pool):
        raise ValueError("Match rerank must be an exact eligible candidate permutation")
    outputs = {r.job_id: r for r in artifact.recommendations}
    if len(outputs) != len(artifact.recommendations) or set(outputs) != set(pool):
        raise ValueError("Every eligible candidate requires exactly one recommendation")
    evidence = {e["key"]: e for e in profile["evidence"]}
    resolved = {}
    for ref, candidate in pool.items():
        result = outputs[ref]
        ceiling = candidate["ranking_components"]["fit_band_ceiling"]
        if BANDS.index(result.fit_band) > BANDS.index(ceiling):
            raise ValueError("Fit band exceeds deterministic evidence/eligibility ceiling")
        matrix = {r["id"]: r for r in candidate["evidence_matrix"]}
        def associations(reasons, strength=False):
            items = []
            seen = set()
            for reason in reasons:
                row = matrix.get(reason.alignment_id)
                if not row or reason.job_requirement_id != row["requirement_ref"]:
                    raise ValueError("Unknown Job Requirement / alignment reference")
                match = next((s for s in row["supports"] if s["fact_ref"] == reason.candidate_fact_id
                    and set(s["evidence_ids"]) == set(reason.evidence_ids) and len(set(reason.evidence_ids)) == len(reason.evidence_ids)
                    and s["explanation"] == reason.explanation and (not strength or s["status"] == "SUPPORTED")), None)
                if not match or any(key not in evidence for key in reason.evidence_ids):
                    raise ValueError("Unsupported recommendation reason or unknown candidate Evidence")
                key = (row["id"], match["fact_ref"])
                if key in seen: raise ValueError("Duplicate support association")
                seen.add(key)
                items.append(dict(alignment_id=row["id"], requirement_id=row["job_requirement_id"],
                    statement=row["statement"], **match, evidence=[evidence[k] for k in match["evidence_ids"]]))
            return items
        gaps = {r["id"] for r in matrix.values() if r["status"] in ("PARTIAL", "NO_EVIDENCE")}
        if set(result.current_evidence_gaps) != gaps or len(result.current_evidence_gaps) != len(gaps):
            raise ValueError("Evidence gaps cannot be hidden or invented")
        if not set(result.preparation_focus) <= gaps or len(set(result.preparation_focus)) != len(result.preparation_focus):
            raise ValueError("Preparation focus must refer to real evidence gaps")
        if set(result.unknowns_to_confirm) != set(candidate["unknown_constraints"]) or len(set(result.unknowns_to_confirm)) != len(result.unknowns_to_confirm):
            raise ValueError("Hard constraint UNKNOWN cannot be guessed away")
        resolved[candidate["job_id"]] = dict(fit_band=result.fit_band,
            matched_strengths=associations(result.matched_strengths, True), alignment_reasons=associations(result.alignment_reasons),
            current_evidence_gaps=[dict(matrix[k], gap_message=GAP_MESSAGE) for k in sorted(gaps)],
            preparation_focus=[matrix[k] for k in result.preparation_focus], unknowns_to_confirm=result.unknowns_to_confirm,
            epistemic_status="EVIDENCE_INTERSECTION_NOT_SKILL_CERTIFICATION")
    return resolved
