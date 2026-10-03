"""Resolve string Evidence refs against this session's persisted evaluations only."""
import re
from interview_api.core.exceptions import AppError
from interview_api.modules.memory.proposal_service import validate_observation_content


def _qualitative(text):
    validate_observation_content(text)
    # Technical names such as HTTP2 remain legal; generated scores/count assertions do not.
    if re.search(r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百]+)\s*(?:分|次|道题|场面试|轮回答|%|/\s*5|out of\s+5)|(?:score|average|count|occurrences?)\s*(?:is|of|:|=)\s*\d+", text, re.IGNORECASE):
        raise AppError("COACH_COMPUTED_NUMBER", "Coach 不得生成评分、出现次数或统计数字", 422)
    return text


def resolve_evaluations(refs, catalog):
    ids = []
    for ref in refs:
        if not isinstance(ref, str) or not re.fullmatch(r"evaluation:[1-9][0-9]*", ref):
            raise AppError("UNKNOWN_EVALUATION", "评价引用必须是提供的 normalized Evaluation ID", 422)
        identifier = int(ref.split(":", 1)[1])
        if identifier not in catalog:
            raise AppError("UNKNOWN_EVALUATION", "未知或其他面试的评价引用", 422)
        ids.append(identifier)
    ids = sorted(set(ids))
    if not ids:
        raise AppError("EVALUATION_SUPPORT_REQUIRED", "Coach 观察必须有真实评价支持", 422)
    return ids


def resolve_coach_artifact(artifact, evaluations):
    catalog = {e["id"]: e for e in evaluations}
    result = dict(overall_summary=_qualitative(artifact.overall_summary),
        recommended_actions=[_qualitative(t) for t in artifact.recommended_actions],
        practice_plan=[_qualitative(t) for t in artifact.practice_plan], memory_proposals=[],
        epistemic_status="AGENT_SYNTHESIS_OF_THIS_INTERVIEW_NOT_STABLE_CANDIDATE_FACTS")
    for category in ("strengths", "weaknesses", "recurring_issues", "job_alignment"):
        result[category] = []
        for item in getattr(artifact, category):
            ids = resolve_evaluations(item.supporting_evaluation_ids, catalog)
            if category == "recurring_issues" and len(ids) < 2:
                raise AppError("RECURRING_SUPPORT_REQUIRED", "重复问题必须支持至少两个不同的真实评价", 422)
            result[category].append(dict(statement=_qualitative(item.statement), supporting_evaluation_ids=ids,
                occurrence_count=len(ids), supporting_evaluations=[dict(id=id_, evaluation_id=id_,
                    session_question_id=catalog[id_]["session_question_id"], question_id=catalog[id_]["session_question_id"],
                    question=catalog[id_]["question"], evaluation=catalog[id_]["evaluation"],
                    scores=dict(catalog[id_]["scores"])) for id_ in ids]))
    for item in artifact.memory_proposals:
        ids = resolve_evaluations(item.supporting_evaluation_ids, catalog)
        result["memory_proposals"].append(dict(proposal_type=item.proposal_type,
            content=_qualitative(item.content), supporting_evaluation_ids=ids))
    return result
