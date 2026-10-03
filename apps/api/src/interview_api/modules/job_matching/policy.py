"""Versioned deterministic qualification, literal Evidence matching and rank policy."""
import re
from interview_api.modules.public_career.fingerprints import normalize_text

POLICY_VERSION = "literal-evidence-eligibility-v1"
GAP_MESSAGE = "当前 CandidateProfile / ResumeEvidence 中没有相关证明材料，不表示候选人不会。"
BANDS = ("UNCERTAIN", "EXPLORATORY", "GOOD_MATCH", "STRONG_MATCH")
HARD_FIELDS = ("education_minimum", "majors", "graduation_years")


def norm(value):
    return normalize_text(str(value))


def eligibility(job, filters):
    """No inference from free text, Memory, titles, or education.description.

    Only explicitly structured JD `matching_constraints` is consumed. Null/empty
    qualification fields explicitly mean no such restriction; missing/malformed
    fields are UNKNOWN. Profile M3 has no reliable degree/major/year fields, so
    a non-empty qualification restriction is always UNKNOWN in this first version.
    """
    reasons, unknown = [], []
    def record(field, status, reason):
        reasons.append(dict(field=field, status=status, reason=reason))
        if status == "UNKNOWN": unknown.append(field)
    record("job_status", "SATISFIED" if job["status"] == "ACTIVE" else "NOT_SATISFIED", "公共岗位状态")
    if job["company_id"] in filters["exclude_company_ids"] or job["job_id"] in filters["exclude_job_ids"]:
        record("explicit_exclusion", "NOT_SATISFIED", "本次用户明确排除的公司或岗位")
    for field, selected in (("campus_type", filters["campus_types"]), ("job_family", filters["job_families"])):
        value = job.get(field)
        if selected:
            record(field, "UNKNOWN" if not value else "SATISFIED" if norm(value) in {norm(v) for v in selected} else "NOT_SATISFIED",
                "仅比较本次显式筛选与岗位结构化字段")
        else: record(field, "NOT_APPLICABLE", "本次未设置该筛选条件")
    tags = {norm(v) for v in job["tags"]}
    if tags & {norm(v) for v in filters["exclude_tags"]}:
        record("excluded_tags", "NOT_SATISFIED", "岗位包含用户明确排除的技术标签")
    if filters["tags"]:
        record("tags", "UNKNOWN" if not tags else "SATISFIED" if tags & {norm(v) for v in filters["tags"]} else "NOT_SATISFIED",
            "结构化 JobTag 至少匹配一个显式技术标签；缺少标签不能猜测")
    constraints = job.get("matching_constraints")
    constraints = constraints if isinstance(constraints, dict) else {}
    if filters["locations"]:
        locations = constraints.get("locations")
        record("locations", "UNKNOWN" if not isinstance(locations, list) or not locations or not all(isinstance(v, str) for v in locations)
            else "SATISFIED" if {norm(v) for v in locations} & {norm(v) for v in filters["locations"]} else "NOT_SATISFIED",
            "地区仅比较 JD 结构化 locations 与本次显式地区选择")
    else: record("locations", "NOT_APPLICABLE", "本次未限制地区")
    for field in HARD_FIELDS:
        if field not in constraints:
            record(field, "UNKNOWN", "JD 未提供可靠结构化条件")
        elif constraints[field] is None or constraints[field] == []:
            record(field, "NOT_APPLICABLE", "JD 明确结构化标记无此限制")
        else:
            record(field, "UNKNOWN", "当前 CandidateProfile 没有可靠结构化资格字段，不能从描述或 Memory 推断")
    status = "NOT_SATISFIED" if any(r["status"] == "NOT_SATISFIED" for r in reasons) else "UNKNOWN" if unknown else "SATISFIED"
    return dict(eligibility_status=status, eligibility_reasons=reasons, unknown_constraints=unknown)


def contains_literal(text, term):
    text, term = norm(text), norm(term)
    if not term: return False
    # Java is not JavaScript; Redis/cache aliases are NOT invented.
    return bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text))


def evidence_matrix(job, profile):
    facts, evidence = profile["facts"], {e["key"]: e for e in profile["evidence"]}
    rows = []
    for requirement in job["requirements"]:
        row = dict(id=f"alignment:{requirement['id']}", job_requirement_id=requirement["id"],
            requirement_ref=f"requirement:{requirement['id']}", statement=requirement["statement"],
            job_description_version_id=job["jd_id"], candidate_profile_version_id=profile["id"],
            status="NO_EVIDENCE", supports=[], message=GAP_MESSAGE)
        if norm(requirement.get("requirement_type") or "") in {"education", "major", "graduation_year", "location", "eligibility"}:
            row.update(status="NOT_APPLICABLE", message="硬资格条件独立处理，不计作技术适配证据")
        else:
            # Requirement normalized_value must name one literal topic. Existing
            # free-form values are conservative: only exact/literal text overlaps.
            topic = requirement.get("normalized_value") or requirement["statement"]
            for fact in facts:
                refs = fact["evidence_ids"]
                sources = [evidence.get(ref) for ref in refs]
                if not refs or any(s is None for s in sources): continue
                text = fact["statement"]
                label = fact.get("label") or ""
                if not contains_literal(text, topic) and norm(label) != norm(topic): continue
                # A corrected label alone is a user mention, not proof of project use.
                status = "SUPPORTED" if fact["category"] in ("projects", "work_experience", "internship_experience") or (
                    fact["category"] == "skills" and fact.get("evidence_state") in ("DEMONSTRATED", "STRONG_EVIDENCE")) else "PARTIAL"
                explanation = f"当前材料事实“{text}”与岗位要求“{requirement['statement']}”存在确定性文本交集（{status}）；不代表熟练度或资格认证。"
                row["supports"].append(dict(candidate_fact_id=fact["id"], fact_ref=f"fact:{fact['id']}",
                    category=fact["category"], record_id=fact.get("record_id"), evidence_ids=list(refs),
                    resume_evidence_ids=[s["id"] for s in sources if s["kind"] == "RESUME"],
                    user_evidence_ids=[s["id"] for s in sources if s["kind"] == "USER_CONFIRMED"],
                    status=status, explanation=explanation))
            if row["supports"]:
                row["status"] = "SUPPORTED" if any(s["status"] == "SUPPORTED" for s in row["supports"]) else "PARTIAL"
                row["message"] = "材料支持的文本交集，不做开放式技能推断"
        rows.append(row)
    return rows


def ranking_components(candidate):
    matrix = candidate["evidence_matrix"]
    counts = {state: sum(r["status"] == state for r in matrix) for state in ("SUPPORTED", "PARTIAL", "NO_EVIDENCE", "NOT_APPLICABLE")}
    total = len(matrix) - counts["NOT_APPLICABLE"]
    if counts["SUPPORTED"] and counts["SUPPORTED"] * 2 >= total:
        ceiling = "STRONG_MATCH" if candidate["eligibility_status"] == "SATISFIED" and counts["SUPPORTED"] >= 2 and counts["SUPPORTED"] == total else "GOOD_MATCH"
    elif counts["SUPPORTED"] or counts["PARTIAL"]: ceiling = "EXPLORATORY"
    else: ceiling = "UNCERTAIN"
    return dict(policy_version=POLICY_VERSION, counts=counts, applicable_requirements=total,
        eligibility_tier=0 if candidate["eligibility_status"] == "SATISFIED" else 1,
        evidence_tier=3 - BANDS.index(ceiling), fit_band_ceiling=ceiling)


def pre_rank_key(candidate):
    c = candidate["ranking_components"]
    return (c["eligibility_tier"], c["evidence_tier"], -c["counts"]["SUPPORTED"], -c["counts"]["PARTIAL"],
            c["counts"]["NO_EVIDENCE"], -candidate.get("sql_tag_overlap", 0), candidate["job_id"])


def final_rank_key(candidate):
    c = candidate["ranking_components"]
    # Semantic rerank only within immutable eligibility/evidence ceiling tiers.
    return (c["eligibility_tier"], c["evidence_tier"], candidate["agent_rank"], candidate["pre_rank"], candidate["job_id"])
