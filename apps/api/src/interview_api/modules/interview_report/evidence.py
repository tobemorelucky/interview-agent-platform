"""Bounded Coach evidence projection (COACH_EVIDENCE_V2).

A long interview produces more evidence than the Coach input budget allows. The
raw material contains duplicated copies (reference answer, answer framework,
`issues` repeated as `factual_or_reasoning_issues`), whole rubrics and context
that the Coach cannot cite anyway. This module keeps every fact the report needs
- evaluation id, topic and dimension, five scores, quoted support, factual issues,
missing points, unresolved gaps, the effective question count and why the session
ended - and clips only free text, counting every omission so nothing disappears
silently. If the projection still cannot fit, it fails with the per-section sizes.

V2 exists because two memory fields were projected as `str(dict)` clipped to 600
characters: the model received a Python repr cut mid-token, not structured data,
and `memory_guided_plan` was not projected at all (it fell into `omitted`). Every
memory-shaped field is now a bounded structure that says what it is, where it
came from and what it may not be read as. See `docs/92` §1.
"""
from interview_api.core.exceptions import AppError
from interview_api.modules.candidate_profile.evidence import digest

VERSION = "COACH_EVIDENCE_V2"
# Per-item text limits (characters, not bytes) and list caps.
LIMITS = dict(question=300, answer=600, evaluation=800, point=140, points=4,
              issue=160, issues=3, focus=120, focuses=3, gap=160, gaps=3,
              requirement=140, requirements=10, skill=40,
              # memory / capability projections
              memory=140, memories=8, history=160, label=60, topic=80,
              plan_items=10, plan_refs=3, deprioritized=8, conflicts=2)
# Soft budget for the whole `input` object; the envelope (schema+system) adds ~8KB.
MAX_INPUT_BYTES = 120_000
# A memory item may only carry these keys; anything else stays out of the prompt.
MEMORY_ITEM_KEYS = ("id", "content", "text", "memory_type", "observation_type", "title")


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


def evaluation_content(row):
    """The fields an evaluation is allowed to be identified by.

    Used both when projecting (to freeze a fingerprint) and when a tool re-reads
    the same evaluation, so a changed row can never be disguised as the frozen
    one. Only immutable evaluation content is included, never provider metadata.
    """
    return {key: row.get(key) for key in (
        "id", "session_question_id", "question", "answer", "evaluation", "scores",
        "covered_points", "missing_points", "factual_or_reasoning_issues", "follow_up_focus")}


def evaluation_fingerprint(row):
    return digest(evaluation_content(row))


def project_memory_layer(layer, omitted, key):
    """Structure a memory layer instead of stringifying it.

    Only the whitelisted keys survive, each text field is clipped and counted,
    and the layer keeps the semantics the producer declared. An empty layer is
    still returned, so "no memory" is visibly different from "not projected".
    """
    layer = layer if isinstance(layer, dict) else {}
    raw = list(layer.get("items") or [])
    items, clipped = [], 0
    for item in raw[:LIMITS["memories"]]:
        if not isinstance(item, dict):
            text, cut = _clip(item, LIMITS["memory"])
            items.append(text)
            clipped += cut
            continue
        row = {}
        for name in MEMORY_ITEM_KEYS:
            if name not in item:
                continue
            if name in ("content", "text", "title"):
                row[name], cut = _clip(item[name], LIMITS["memory"])
                clipped += cut
            elif isinstance(item[name], (int, str)) and not isinstance(item[name], bool):
                row[name] = item[name]
        items.append(row)
    omitted[f"{key}_items"] = len(raw) - len(items)
    omitted[f"{key}_clipped_text"] = clipped
    return dict(
        semantics=layer.get("semantics"),
        epistemic_status=layer.get("epistemic_status"),
        is_stable_fact=layer.get("is_stable_fact"),
        items=items, shown=len(items), total=len(raw), clipped=clipped)


def project_capability(snapshot, omitted):
    """The frozen memory-guided plan, as a structure the model can reason over.

    This is the *session-time* plan: it was computed when the interview was
    created and frozen into the snapshot. It is deliberately not re-read here.
    """
    plan = snapshot.get("memory_guided_plan")
    if not isinstance(plan, dict) or not plan:
        return None
    raw_items = list(plan.get("items") or [])
    items = []
    for item in raw_items[:LIMITS["plan_items"]]:
        if not isinstance(item, dict):
            continue
        refs = [ref for ref in (item.get("observation_refs") or []) if isinstance(ref, str)]
        summary, cut = _clip(item.get("history_summary"), LIMITS["history"])
        items.append(dict(
            knowledge_id=item.get("knowledge_id"),
            label=_clip(item.get("label"), LIMITS["label"])[0],
            state=item.get("state"),
            intent=item.get("intent"),
            priority=item.get("priority"),
            needs_recheck=item.get("needs_recheck"),
            memory_revision=item.get("memory_revision"),
            reason_tags=list(item.get("reason_tags") or [])[:4],
            observation_refs=refs[:LIMITS["plan_refs"]],
            observation_refs_total=len(refs),
            history_summary=summary,
            history_clipped=bool(cut)))
    deprioritized = []
    for item in list(plan.get("deprioritized_targets") or [])[:LIMITS["deprioritized"]]:
        if isinstance(item, dict):
            deprioritized.append({k: item[k] for k in ("knowledge_id", "memory_revision", "reason") if k in item})
    omitted["capability_plan_items"] = len(raw_items) - len(items)
    return dict(
        version=plan.get("version"),
        inferred=plan.get("inferred"),
        frozen_at=plan.get("frozen_at"),
        fingerprint=plan.get("fingerprint"),
        semantics=("RULE_GENERATED_TRAINING_HINT_FROZEN_AT_SESSION_START: "
                   "prioritised knowledge points with their inferred capability state"),
        scope_note=("Capability states aggregate this user's observations per topic across all "
                    "interviews and are not evidence of mastery or weakness on this particular "
                    "job: never restate a topic state as a job-specific fact."),
        items=items, items_total=len(raw_items),
        deprioritized_targets=deprioritized)


def project_training(snapshot, omitted):
    """The frozen training scope of this session, when it is a training session."""
    training = snapshot.get("training")
    if not isinstance(training, dict) or not training:
        return None
    before = []
    for item in list(training.get("before") or [])[:LIMITS["plan_items"]]:
        if isinstance(item, dict):
            before.append({k: item[k] for k in ("knowledge_id", "label", "state", "revision") if k in item})
    omitted["training_before_items"] = max(0, len(list(training.get("before") or [])) - len(before))
    return dict(
        mode=training.get("mode"),
        selected_knowledge_id=training.get("selected_knowledge_id"),
        version=training.get("version"),
        semantics=("THIS_SESSION_FROZEN_TRAINING_SCOPE: mode and knowledge points selected when "
                   "this training session was created; later JD or memory changes do not apply"),
        targets=before)


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
    approved = project_memory_layer(snapshot.get("approved_memory_context"), omitted, "approved_memory")
    recent = project_memory_layer(snapshot.get("recent_performance_observations"), omitted, "recent_performance")
    omitted["snapshot_fields"] = sorted(k for k in snapshot
        if k not in ("company_name", "job_title", "profile", "requirements", "budgets",
                     "approved_memory_context", "recent_performance_observations",
                     "jd_version_number", "profile_version_number", "interviewer_config",
                     "memory_guided_plan", "training"))
    return dict(company_name=snapshot.get("company_name"), job_title=snapshot.get("job_title"),
        jd_version_number=snapshot.get("jd_version_number"),
        profile_version_number=snapshot.get("profile_version_number"),
        profile=dict(skills=skills, projects=projects, skills_total=skill_meta["total"],
                     projects_total=project_meta["total"]),
        requirements=requirements, requirements_total=requirement_meta["total"],
        interviewer=dict(skill_id=skill.get("skill_id") or skill.get("id"),
                         version=skill.get("version"), name=skill.get("name")),
        budgets=snapshot.get("budgets"),
        # Distinct provenance per section: user-confirmed context is not skill
        # proof, and prior practice observations are not stable facts.
        approved_memory_context={**approved, "section": "USER_CONFIRMED_CONTEXT"},
        recent_performance_observations={**recent, "section": "PRIOR_PRACTICE_OBSERVATIONS"},
        capability_context=project_capability(snapshot, omitted),
        training_context=project_training(snapshot, omitted))


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
            # The frozen identity of this evaluation: a tool may re-read the row
            # only while this still matches, so it can never return a changed one.
            fingerprint=evaluation_fingerprint(evaluation),
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
