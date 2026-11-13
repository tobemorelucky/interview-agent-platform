"""Resolve string Evidence refs against this session's persisted evaluations only.

The number guard is unit-aware: a technical idiom ("at least once", "one round
trip") is not a statistic, while a score or a count is only accepted when the
same value was computed by the server and handed to the Coach in `statistics`.
"""
import re

from interview_api.core.exceptions import AppError
from interview_api.modules.memory.proposal_service import validate_observation_content

# Values with a score unit must come from the five persisted dimensions.
SCORE_UNITS = ("分", "/5", "out of 5")
COUNT_UNITS = ("次", "道题", "场面试", "轮回答")
CHINESE_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                  "七": 7, "八": 8, "九": 9, "十": 10, "百": 100}
# "一次" is idiomatic in this domain (at-least-once delivery, one round trip,
# first attempt). Every larger Chinese count must be a server-provided number.
# "一次" stops being an idiom as soon as it is scoped to this interview, because
# then it is a claim about how many times the candidate actually answered.
ONE_TIME_IDIOM = re.compile(r"[至最多少再第首每]?一次(?!性)")
SESSION_SCOPE = re.compile(r"(本场|这场|该场|全场|本次|整场|总共|一共|合计|累计)")
ONLY_COUNT_VERB = re.compile(r"(只|仅)(回答|作答|答|说|讲|做|写|用)了$")
ARABIC_CLAIM = re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(分|%|/\s*5|次|道题|场面试|轮回答)")
CHINESE_CLAIM = re.compile(r"([一二两三四五六七八九十百]+)\s*(分|%|次|道题|场面试|轮回答)")
ENGLISH_CLAIM = re.compile(r"\b(score|average|count|occurrences?)\s*(?:is|of|:|=)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def _chinese_number(text):
    if not text or any(ch not in CHINESE_DIGITS for ch in text):
        return None
    if text == "百":
        return 100
    total, section = 0, 0
    for ch in text:
        value = CHINESE_DIGITS[ch]
        if value == 10:
            section = (section or 1) * 10
        elif value == 100:
            total, section = (total + (section or 1)) * 100, 0
        else:
            section += value
    return total + section


def supported_statistics(statistics, evaluations=()):
    """Numbers the server computed for this session; anything else is invented.

    Score claims may only quote a published aggregate (dimension or overall
    average). Individual item scores are deliberately not accepted: "本场总分 5分"
    would otherwise pass whenever any single answer scored 5.
    """
    scores, counts = set(), set()

    def add_scores(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            scores.add(round(float(value), 2))
            scores.add(round(float(value), 1))

    def add_counts(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            counts.add(float(value))

    for key, value in (statistics or {}).items():
        if key == "dimension_averages" and isinstance(value, dict):
            for inner in value.values():
                add_scores(inner)
        elif key.endswith("_average"):
            add_scores(value)
        elif "count" in key or key in ("total_questions", "question_budget"):
            add_counts(value)
    return dict(scores=scores, counts=counts)


def _scoped_once(text, start):
    """True when the "一次" counts something that happened in this interview.

    "本场只回答一次" / "一共只答了一次" are claims about the session, so they must
    match the server count. "至少一次投递" / "再试一次" stay idiomatic.
    """
    window = text[max(0, start - 8):start]
    return bool(SESSION_SCOPE.search(window) or ONLY_COUNT_VERB.search(window))


def _unsupported(text, catalog):
    """Return (phrase, unit, value) for the first statistic the server did not provide."""
    for match in ARABIC_CLAIM.finditer(text or ""):
        phrase, unit, value = match.group(0), match.group(2).strip(), float(match.group(1))
        if unit in SCORE_UNITS or unit == "/5":
            if value not in catalog["scores"]:
                return phrase, unit, value
        elif unit == "%":
            return phrase, unit, value
        elif value not in catalog["counts"]:
            return phrase, unit, value
    for match in CHINESE_CLAIM.finditer(text or ""):
        phrase, unit = match.group(0), match.group(2)
        if unit == "次" and ONE_TIME_IDIOM.fullmatch(phrase) and not _scoped_once(text or "", match.start()):
            continue
        value = _chinese_number(match.group(1))
        if value is None:
            continue
        if unit in SCORE_UNITS:
            if float(value) not in catalog["scores"]:
                return phrase, unit, float(value)
        elif unit == "%":
            return phrase, unit, float(value)
        elif float(value) not in catalog["counts"]:
            return phrase, unit, float(value)
    for match in ENGLISH_CLAIM.finditer(text or ""):
        value = float(match.group(2))
        if value not in catalog["counts"] and value not in catalog["scores"]:
            return match.group(0), "english", value
    return None


def _qualitative(text, path="unknown", catalog=None):
    validate_observation_content(text)
    # Technical names such as HTTP2 remain legal; unsupported statistics do not.
    catalog = catalog or dict(scores=set(), counts=set())
    found = _unsupported(text, catalog)
    if found:
        phrase, unit, value = found
        error = AppError("COACH_COMPUTED_NUMBER",
                         "Coach 只能引用服务端已计算的统计；此处数字没有数据支持", 422)
        error.diagnostics = dict(field_path=path, rule="FABRICATED_STATISTIC",
                                 matched_number=phrase[:40], unit=unit, value=value)
        raise error
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


def resolve_coach_artifact(artifact, evaluations, statistics=None, *, training_scope=None):
    catalog = {e["id"]: e for e in evaluations}
    numbers = supported_statistics(statistics, evaluations)
    result = dict(
        overall_summary=_qualitative(artifact.overall_summary, "overall_summary", numbers),
        recommended_actions=[_qualitative(t, f"recommended_actions.{i}", numbers)
                             for i, t in enumerate(artifact.recommended_actions)],
        practice_plan=[_qualitative(t, f"practice_plan.{i}", numbers)
                       for i, t in enumerate(artifact.practice_plan)], memory_proposals=[],
        epistemic_status="AGENT_SYNTHESIS_OF_THIS_INTERVIEW_NOT_STABLE_CANDIDATE_FACTS")
    for category in ("strengths", "weaknesses", "recurring_issues", "job_alignment"):
        result[category] = []
        for index, item in enumerate(getattr(artifact, category)):
            ids = resolve_evaluations(item.supporting_evaluation_ids, catalog)
            if category == "recurring_issues" and len(ids) < 2:
                raise AppError("RECURRING_SUPPORT_REQUIRED", "重复问题必须支持至少两个不同的真实评价", 422)
            result[category].append(dict(
                statement=_qualitative(item.statement, f"{category}.{index}.statement", numbers),
                supporting_evaluation_ids=ids, occurrence_count=len(ids),
                supporting_evaluations=[dict(id=id_, evaluation_id=id_,
                    session_question_id=catalog[id_]["session_question_id"], question_id=catalog[id_]["session_question_id"],
                    question=catalog[id_]["question"], evaluation=catalog[id_]["evaluation"],
                    scores=dict(catalog[id_]["scores"])) for id_ in ids]))
    for index, item in enumerate(artifact.memory_proposals):
        ids = resolve_evaluations(item.supporting_evaluation_ids, catalog)
        result["memory_proposals"].append(dict(proposal_type=item.proposal_type,
            content=_qualitative(item.content, f"memory_proposals.{index}.content", numbers),
            supporting_evaluation_ids=ids))
    # Only the harness artifact declares this field; the OFF path stays byte-identical.
    if hasattr(artifact, "suggested_training"):
        suggestion, rejected = _resolve_suggestion(
            artifact.suggested_training, catalog, numbers, training_scope)
        result["suggested_training"] = suggestion
        if rejected:
            result["suggested_training_rejected"] = rejected
    return result


def _resolve_suggestion(suggestion, catalog, numbers, training_scope):
    """Validate an advisory training idea against the same rules Training uses.

    A rejected suggestion never fails the report: the findings, the statistics
    and the memory proposals are already legal, and losing them because an
    optional piece of advice was wrong would be a worse outcome than dropping
    the advice. What is never allowed is storing it as if it were legal.
    """
    if suggestion is None:
        return None, None

    def reject(code, detail, stage):
        return None, dict(code=code, detail=detail, stage=stage)

    try:
        ids = resolve_evaluations(suggestion.supporting_evaluation_ids, catalog)
    except AppError as exc:
        return reject(exc.code, exc.message, "EVIDENCE")
    scope = training_scope or {}
    target = (scope.get("targets") or {}).get(suggestion.knowledge_id)
    if target is None:
        # Without a frozen blueprint there is no way to prove this is a legal
        # point for this job, so it cannot be presented as one.
        return reject("KNOWLEDGE_NOT_IN_FROZEN_TARGETS",
                      f"{suggestion.knowledge_id} 不属于本场冻结的岗位考察目标", "SCOPE")
    available = list(target.get("available_modes") or [])
    if suggestion.mode not in available:
        return reject("MODE_NOT_ALLOWED_FOR_STATE",
                      f"状态 {target.get('state')} 只允许 {available}，不能建议 {suggestion.mode}", "MODE")
    try:
        reason = _qualitative(suggestion.reason, "suggested_training.reason", numbers)
    except AppError as exc:
        return reject(exc.code, exc.message, "REASON")
    return dict(mode=suggestion.mode, knowledge_id=suggestion.knowledge_id,
                reason=reason, supporting_evaluation_ids=ids, scope=suggestion.scope,
                skill_id=scope.get("skill_id"), state=target.get("state"),
                available_modes=available, memory_revision=target.get("memory_revision"),
                validation="LEGAL_AT_REPORT_TIME_ADVISORY_ONLY"), None
