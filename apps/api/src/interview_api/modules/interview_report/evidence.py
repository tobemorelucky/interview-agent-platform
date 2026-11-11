"""Bounded Coach evidence projection (COACH_EVIDENCE_V1).

A long interview produces more evidence than the Coach input budget allows. The
raw material contains duplicated copies (reference answer, answer framework,
`issues` repeated as `factual_or_reasoning_issues`), whole rubrics and context
that the Coach cannot cite anyway. This module keeps every fact the report needs
- evaluation id, topic and dimension, five scores, quoted support, factual issues,
missing points, unresolved gaps, the effective question count and why the session
ended - and clips only free text, counting every omission so nothing disappears
silently. If the projection still cannot fit, it fails with the per-section sizes.
"""
from interview_api.core.exceptions import AppError

VERSION = "COACH_EVIDENCE_V1"
# Per-item text limits (characters, not bytes) and list caps.
LIMITS = dict(question=300, answer=600, evaluation=800, point=140, points=4,
              issue=160, issues=3, focus=120, focuses=3, gap=160, gaps=3,
              requirement=140, requirements=10, skill=40)
# Soft budget for the whole `input` object; the envelope (schema+system) adds ~8KB.
MAX_INPUT_BYTES = 120_000


def _clip(value, limit):
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if len(text) <= limit:
        return text, 0
    return text[:limit] + "…", 1


def _clips(values, limit, cap):
    items, clipped = [], 0
    for value in list(values or [])[:cap]:
        text, cut = _clip(value, limit)
        items.append(text)
        clipped += cut
    return items, dict(shown=len(items), total=len(list(values or [])), clipped=clipped)


def project_snapshot(snapshot, omitted):
    snapshot = snapshot or {}
    profile = snapshot.get("profile") or {}
    skills, skill_meta = _clips([s.get("label") if isinstance(s, dict) else s
                                 for s in (profile.get("skills") or [])], LIMITS["skill"], 12)
    projects, project_meta = _clips([p.get("label") if isinstance(p, dict) else p
                                     for p in (profile.get("projects") or [])], LIMITS["skill"], 8)
    requirements, requirement_meta = _clips(
        [r.get("statement") if isinstance(r, dict) else r for r in (snapshot.get("requirements") or [])],
        LIMITS["requirement"], LIMITS["requirements"])
    interviewer = snapshot.get("interviewer_config") or {}
    skill = interviewer.get("skill") if isinstance(interviewer.get("skill"), dict) else interviewer
    approved, approved_meta = _clip(snapshot.get("approved_memory_context"), 600)
    recent, recent_meta = _clip(snapshot.get("recent_performance_observations"), 600)
    omitted["snapshot_fields"] = sorted(k for k in snapshot
        if k not in ("company_name", "job_title", "profile", "requirements", "budgets",
                     "approved_memory_context", "recent_performance_observations",
                     "jd_version_number", "profile_version_number", "interviewer_config"))
    return dict(company_name=snapshot.get("company_name"), job_title=snapshot.get("job_title"),
        jd_version_number=snapshot.get("jd_version_number"),
        profile_version_number=snapshot.get("profile_version_number"),
        profile=dict(skills=skills, projects=projects, skills_total=skill_meta["total"],
                     projects_total=project_meta["total"]),
        requirements=requirements, requirements_total=requirement_meta["total"],
        interviewer=dict(skill_id=skill.get("skill_id") or skill.get("id"),
                         version=skill.get("version"), name=skill.get("name")),
        budgets=snapshot.get("budgets"),
        approved_memory_context=approved, recent_performance_observations=recent,
        clipped=dict(approved=int(bool(approved_meta)), recent=int(bool(recent_meta))))


def project_questions(questions, omitted):
    result = []
    for question in questions or []:
        refs = question.get("candidate_refs") or {}
        decision = refs.get("adaptive_decision") or {}
        topic = decision.get("target_knowledge_id")
        result.append(dict(id=question.get("id"), question=question.get("question"),
            question_kind=question.get("question_kind"), status=question.get("status"),
            source=question.get("source"), assessment_topic=topic))
    omitted["question_fields"] = ["standard_answer", "candidate_refs", "job_requirement_id"]
    omitted["questions"] = len(result)
    return result


def project_evaluations(evaluations, omitted):
    result, clipped_fields = [], 0
    for evaluation in evaluations or []:
        scores = dict(evaluation.get("scores") or {})
        covered, covered_meta = _clips(evaluation.get("covered_points"), LIMITS["point"], LIMITS["points"])
        missing, missing_meta = _clips(evaluation.get("missing_points"), LIMITS["point"], LIMITS["points"])
        issues, issue_meta = _clips(evaluation.get("factual_or_reasoning_issues"), LIMITS["issue"], LIMITS["issues"])
        focus, focus_meta = _clips(evaluation.get("follow_up_focus"), LIMITS["focus"], LIMITS["focuses"])
        answer, answer_cut = _clip(evaluation.get("answer"), LIMITS["answer"])
        narrative, narrative_cut = _clip(evaluation.get("evaluation"), LIMITS["evaluation"])
        clipped_fields += answer_cut + narrative_cut + covered_meta["clipped"] + missing_meta["clipped"] \
            + issue_meta["clipped"] + focus_meta["clipped"]
        result.append(dict(id=evaluation.get("id"), evidence_ref=evaluation.get("evidence_ref"),
            session_question_id=evaluation.get("session_question_id"),
            question=evaluation.get("question"), question_kind=evaluation.get("question_kind"),
            scores=scores, evaluation=narrative, answer=answer,
            covered_points=covered, covered_points_total=covered_meta["total"],
            missing_points=missing, missing_points_total=missing_meta["total"],
            factual_or_reasoning_issues=issues, issues_total=issue_meta["total"],
            follow_up_focus=focus, follow_up_total=focus_meta["total"]))
    omitted["evaluation_fields"] = ["reference_answer", "answer_framework", "issues",
                                    "provider", "model", "design_revision", "tool_evidence"]
    omitted["evaluations"] = len(result)
    omitted["clipped_text_fields"] = clipped_fields
    return result


def project_review(review, omitted):
    if not review:
        return None
    targets, gaps = [], 0
    for target in review.get("targets") or []:
        mechanisms, meta = _clips(target.get("missing_mechanisms"), LIMITS["gap"], LIMITS["gaps"])
        gaps += meta["total"]
        targets.append(dict(label=target.get("label"), state=target.get("state"),
            missing_mechanisms=mechanisms, missing_total=meta["total"]))
    omitted["review_observations"] = sum(len(t.get("observations") or []) for t in (review.get("targets") or []))
    omitted["review_missing_mechanisms"] = gaps - sum(len(t["missing_mechanisms"]) for t in targets)
    return dict(version=review.get("version"), semantics=review.get("semantics"), targets=targets)


def coach_evidence(*, snapshot, statistics, questions, evaluations, completion=None,
                   review=None, fingerprint=None, max_bytes=MAX_INPUT_BYTES):
    """Return (payload, report). Raises when the bounded projection still cannot fit."""
    omitted = {}
    payload = dict(evidence_projection=dict(version=VERSION, limits=LIMITS, max_bytes=max_bytes, omitted=omitted),
        snapshot=project_snapshot(snapshot, omitted), statistics=statistics,
        questions=project_questions(questions, omitted),
        evaluations=project_evaluations(evaluations, omitted))
    if completion:
        payload["session_completion"] = completion
    if fingerprint:
        payload["evaluation_fingerprint"] = fingerprint
    projected = project_review(review, omitted)
    if projected is not None:
        payload["adaptive_review"] = projected
    sizes = {key: len(_json(payload[key])) for key in payload}
    total = len(_json(payload))
    report = dict(version=VERSION, bytes=total, section_bytes=sizes,
                  evidence_ids=[e["id"] for e in payload["evaluations"]],
                  questions=len(payload["questions"]), omitted=dict(omitted))
    if total > max_bytes:
        error = AppError("COACH_EVIDENCE_BUDGET", "压缩后的面试材料仍超过 Coach 输入预算", 422)
        error.diagnostics = dict(rule="COACH_EVIDENCE_BUDGET", bytes=total, max_bytes=max_bytes,
                                 section_bytes=sizes, omitted=dict(omitted))
        raise error
    return payload, report


def _json(value):
    import json
    return json.dumps(value, ensure_ascii=False).encode("utf-8")
