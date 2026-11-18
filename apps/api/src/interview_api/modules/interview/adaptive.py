"""V2.1 frozen goals and deterministic evidence/decision constraints. No model I/O."""
from copy import deepcopy
import json
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, ValidationError

from interview_api.core.exceptions import AppError
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.interview.artifacts import StrictArtifact, QuestionArtifact, EvaluationArtifact
from interview_api.modules.interview.topics import (
    mentioned_topics, is_hard_condition, coverage, topic_terms, topic_diagnostic,
    is_project_fact, refers_to_candidate_work)

VERSION = "2.1.0"
LABELS = dict(java="Java 与并发", mysql_consistency="数据库事务与一致性", redis="缓存工程",
    kafka_reliability="消息可靠性", system_design="系统设计与异常处理", rag_retrieval="检索与 RAG",
    agent="Agent 工具与状态管理", llm="大模型应用", python="Python 工程", data_security="数据安全与权限",
    frontend="前端工程", testing="测试开发与质量保障", data_engineering="数据工程与数仓", recommendation="推荐与机器学习",
    network_os="操作系统与网络协议", algorithm="算法与数据结构")
STATES = Literal["INSUFFICIENT_EVIDENCE", "PARTIALLY_DEMONSTRATED", "DEMONSTRATED_IN_SESSION", "NEEDS_CLARIFICATION"]
ShortText = Annotated[str, Field(min_length=1, max_length=400)]


class KnowledgeObservation(StrictArtifact):
    knowledge_id: str = Field(max_length=100)
    state: STATES
    answer_quotes: list[ShortText] = Field(max_length=3)
    missing_mechanisms: list[ShortText] = Field(max_length=5)
    resolved_gap_ids: list[str] = Field(default_factory=list, max_length=5)


class AdaptiveEvaluation(EvaluationArtifact):
    knowledge_observations: list[KnowledgeObservation] = Field(min_length=1, max_length=1)


class Gap(StrictArtifact):
    id: str = Field(max_length=100)
    text: ShortText


class AnswerSpan(StrictArtifact):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    excerpt: ShortText


class ResolvedObservation(KnowledgeObservation):
    question_id: int = Field(gt=0)
    answer_id: int = Field(gt=0)
    evaluation_id: int | None
    open_gaps: list[Gap] = Field(max_length=200)
    answer_spans: list[AnswerSpan] = Field(max_length=3)
    # Leave old persisted observations (and their payload fingerprints) untouched.
    question_state: STATES | None = Field(default=None, exclude_if=lambda value: value is None)
    focus_gap_ids: list[str] | None = Field(default=None, max_length=5, exclude_if=lambda value: value is None)


class KnowledgeState(StrictArtifact):
    version: Literal["2.1.0"]
    items: dict[str, ResolvedObservation] = Field(max_length=8)
    observations: list[ResolvedObservation] = Field(max_length=40)


class AdaptiveDecision(StrictArtifact):
    decision_version: Literal["2.1.0"]
    strategy: Literal["FOLLOW_UP", "REQUEST_EXAMPLE", "DEEP_DIVE", "NEXT"]
    target_knowledge_id: str = Field(max_length=100)
    supporting_refs: list[str] = Field(min_length=1, max_length=12)
    reason_tags: list[Literal["OPENING", "ANSWER_TOO_GENERAL", "MISSING_MECHANISM", "NEEDS_EXAMPLE",
        "VERIFY_TRADEOFF", "GAP_RESOLVED", "COVERAGE_SWITCH", "COOLDOWN_SWITCH"]] = Field(min_length=1, max_length=3)
    proposed_question: QuestionArtifact
    question_source_ref: str = Field(max_length=100)
    fallback_action: Literal["SAFE_RETRY"] = "SAFE_RETRY"

    @property
    def recommendation(self):
        return "FOLLOW_UP" if self.strategy == "REQUEST_EXAMPLE" else self.strategy

    @property
    def question(self):
        return self.proposed_question

    @property
    def tool_result_ids(self):
        return []


PAIR_CONTRACT = "TARGET_SOURCE_PAIR_V1"
GAP_SCOPE_CONTRACT = "QUESTION_GAP_SCOPE_V1"
# A question the gate refuses is a business outcome, not a broken Main. Callers
# may replace it with the deterministic fallback, which passes the same gate, so
# the validated answer and evaluation survive. Anything outside this set (limits,
# frozen-snapshot drift, model attribution) makes the turn permanently terminal.
RECOVERABLE_DECISION_CODES = frozenset({
    "ADAPTIVE_TOPIC_MISMATCH", "INVALID_QUESTION_GAP_SCOPE", "ADAPTIVE_DECISION_REJECTED",
    "UNKNOWN_DIRECTOR_REFERENCE", "DIRECTOR_ANSWER_REQUIRED", "UNKNOWN_DIRECTOR_CANDIDATE",
    "DIRECTOR_PAIR_MISMATCH", "UNKNOWN_DIRECTOR_SOURCE",
})
# Only these two are replaced silently by the deterministic fallback. The other
# business rejections stay visible to the caller so the existing retry contract
# (same idempotency key, evaluation reused) is unchanged.
SILENT_FALLBACK_CODES = frozenset({"ADAPTIVE_TOPIC_MISMATCH", "INVALID_QUESTION_GAP_SCOPE"})


class QuestionDraft(StrictArtifact):
    """Wording only; provenance comes from the explicitly selected legal pair."""
    question: str = Field(min_length=3, max_length=3000)
    expected_points: list[str] = Field(min_length=1, max_length=15)
    dimension: str = Field(min_length=1, max_length=50)
    difficulty: Literal["EASY", "MEDIUM", "HARD"]


class AdaptiveSelection(StrictArtifact):
    candidate_id: str = Field(min_length=1, max_length=64)
    strategy: Literal["FOLLOW_UP", "REQUEST_EXAMPLE", "DEEP_DIVE", "NEXT"]
    supporting_refs: list[str] = Field(min_length=1, max_length=12)
    reason_tags: list[Literal["OPENING", "ANSWER_TOO_GENERAL", "MISSING_MECHANISM", "NEEDS_EXAMPLE",
        "VERIFY_TRADEOFF", "GAP_RESOLVED", "COVERAGE_SWITCH", "COOLDOWN_SWITCH"]] = Field(min_length=1, max_length=3)
    proposed_question: QuestionDraft
    fallback_action: Literal["SAFE_RETRY"] = "SAFE_RETRY"


class ResolvedAdaptiveDecision(AdaptiveDecision):
    """Server-side artifact; never an invitation for the model to compose IDs."""
    candidate_id: str = Field(min_length=1, max_length=64)


class ScopedAdaptiveSelection(AdaptiveSelection):
    model_config = ConfigDict(title="AdaptiveSelection")
    focus_gap_ids: list[str] = Field(max_length=5)


class ResolvedScopedAdaptiveDecision(ResolvedAdaptiveDecision):
    focus_gap_ids: list[str] = Field(max_length=5)


def uses_pairs(payload):
    return payload.get("snapshot", {}).get("adaptive_policy", {}).get("decision_contract") == PAIR_CONTRACT


def uses_gap_scope(payload):
    return payload.get("snapshot", {}).get("adaptive_policy", {}).get("gap_scope_contract") == GAP_SCOPE_CONTRACT


def validate_gap_scope(focus, gaps, *, required=False):
    if (focus is None and required or focus is not None and (
            not isinstance(focus, list) or len(focus) > 5 or
            any(not isinstance(identifier, str) or identifier not in gaps for identifier in focus)
            or len(set(focus)) != len(focus))):
        raise AppError("INVALID_QUESTION_GAP_SCOPE", "本题聚焦缺口必须是本场同一目标的有效开放缺口", 422)


def gap_scope_diagnostic(artifact, adaptive, gaps):
    """Facts a reviewer needs when a question's gap scope is refused.

    The scope itself is never repaired here: an empty focus stays empty and an
    unknown ID stays unknown. This only records which target had which gaps and
    whether the model could have known their IDs from the frozen input.
    """
    knowledge_id = getattr(artifact, "target_knowledge_id", None)
    focus = getattr(artifact, "focus_gap_ids", None)
    projected = ((adaptive.get("knowledge_state") or {}).get("items") or {}).get(knowledge_id) or {}
    return dict(target_knowledge_id=knowledge_id, strategy=getattr(artifact, "strategy", None),
        focus_gap_ids=focus if isinstance(focus, list) else None,
        open_gap_ids=sorted(gaps)[:8], open_gap_count=len(gaps),
        candidate_id=getattr(artifact, "candidate_id", None),
        question_source_ref=getattr(artifact, "question_source_ref", None),
        # The harness projection keeps gap text/IDs on demand only. An empty
        # focus under FOLLOW_UP is then a look-before-you-leap miss, not proof
        # that the server offered an unsatisfiable scope.
        gap_ids_in_projection=("INLINE" if projected.get("open_gaps") else "ON_DEMAND_ONLY"))


def decision_candidates(context, choices):
    skill = context["interviewer_config"]["skill"]
    blueprint = context["adaptive_blueprint"]
    if blueprint.get("version") != VERSION or blueprint.get("fingerprint") != digest({k: v for k, v in blueprint.items() if k != "fingerprint"}):
        raise AppError("ADAPTIVE_BLUEPRINT_CHANGED", "冻结的考察蓝图版本或指纹不一致", 409)
    if blueprint["skill_id"] != skill["id"] or blueprint["skill_version"] != skill["version"]:
        raise AppError("ADAPTIVE_BLUEPRINT_CHANGED", "冻结蓝图与本场 Skill 版本不一致", 409)
    rows_by_type = {"JOB_REQUIREMENT": ("requirement", context["requirements"]),
        "PUBLIC_QUESTION": ("public_question", context["public_questions"]),
        "CANDIDATE_PROFILE": ("fact", context["candidate_facts"])}
    candidates = []
    for choice in choices:
        target = choice["target"]
        if target not in blueprint["targets"]:
            raise AppError("ADAPTIVE_BLUEPRINT_CHANGED", "候选目标不属于冻结蓝图", 409)
        sources = [s for s in target["sources"] if s["ref"] != choice.get("exclude_source_ref")]
        # An empty source list is not an approved public source. Only an actual
        # pinned Skill topic can supply an explicitly generic question fallback.
        if not sources and target["topic"] in skill["priority_topics"] and target["knowledge_id"] == f"{skill['id']}:{target['topic']}":
            sources = [dict(ref=f"skill:{skill['id']}@{skill['version']}", source_type="DYNAMIC_GENERATED", source_id=None)]
        for source in sources:
            if source["source_type"] in rows_by_type:
                prefix, rows = rows_by_type[source["source_type"]]
                if source["ref"] != f"{prefix}:{source['source_id']}" or not any(r["id"] == source["source_id"] for r in rows):
                    raise AppError("UNKNOWN_DIRECTOR_SOURCE", "冻结题源不属于本场资料", 422)
            elif source.get("kind") != "REPORTED_TOPIC" and source != dict(
                    ref=f"skill:{skill['id']}@{skill['version']}", source_type="DYNAMIC_GENERATED", source_id=None):
                raise AppError("UNKNOWN_DIRECTOR_SOURCE", "通用来源必须是本场真实 Skill", 422)
            identity = [context["adaptive_blueprint"]["fingerprint"], target["knowledge_id"], source]
            candidates.append(dict(candidate_id="C_" + digest(identity)[:24],
                target_knowledge_id=target["knowledge_id"], topic=target["topic"], label=target["label"],
                strategies=choice["strategies"], source=deepcopy(source),
                # What kind of NEW question this frozen source can naturally carry.
                # A project fact supports a project question and can extend into a
                # scenario; a requirement or a generic Skill topic supports a
                # fundamentals question or an engineering scenario. Follow-ups keep
                # the shape of the thread they continue.
                question_shapes=(question_shapes_for(context, source) if "NEXT" in choice["strategies"] else []),
                source_semantics="GENERIC_SKILL_NOT_COMPANY_EVIDENCE" if source["ref"].startswith("skill:") else
                    "REPORTED_TOPIC_NOT_VERBATIM" if source.get("kind") == "REPORTED_TOPIC" else "FROZEN_SOURCE"))
    return candidates


def question_shapes_for(context, source):
    """Legal shapes of a new question, derived from its frozen source.

    Shapes are presentation, not provenance: the target topic and its source are
    still enforced by the same gate. This exists so the interviewer can switch
    between a project question, a fundamentals question and an engineering
    scenario without inventing a new Agent or a new source type.
    """
    kind = (source or {}).get("source_type")
    if kind == "CANDIDATE_PROFILE":
        fact = next((f for f in context["candidate_facts"] if f["id"] == source.get("source_id")), None)
        if fact is not None and is_project_fact(fact.get("field_path")):
            return ["PROJECT", "SCENARIO"]
        return ["FUNDAMENTALS"]
    if kind == "PUBLIC_QUESTION":
        # A reported question is a reported topic, never a verbatim company question.
        return ["FUNDAMENTALS"]
    return ["FUNDAMENTALS", "SCENARIO"]


def resolve_selection(selection, payload):
    if not uses_pairs(payload):
        raise AppError("ADAPTIVE_CONTRACT_MISMATCH", "本场未启用组合选择契约", 422)
    pair = next((c for c in payload["adaptive"]["candidates"] if c["candidate_id"] == selection.candidate_id), None)
    if pair is None:
        raise AppError("UNKNOWN_DIRECTOR_CANDIDATE", "未知或过期的目标题源组合，原回答可重试", 422)
    source = pair["source"]
    provenance = dict(source_type=source["source_type"])
    if selection.strategy in ("FOLLOW_UP", "REQUEST_EXAMPLE", "DEEP_DIVE"):
        provenance["source_type"] = "DYNAMIC_GENERATED" if selection.strategy == "DEEP_DIVE" else "FOLLOW_UP"
    elif source["source_type"] == "PUBLIC_QUESTION":
        provenance["source_question_id"] = source["source_id"]
    elif source["source_type"] == "JOB_REQUIREMENT":
        provenance["job_requirement_id"] = source["source_id"]
    elif source["source_type"] == "CANDIDATE_PROFILE":
        fact = next((f for f in payload["snapshot"]["candidate_facts"] if f["id"] == source["source_id"]), None)
        if fact is None:
            raise AppError("UNKNOWN_CANDIDATE_EVIDENCE", "组合引用的事实不属于本场", 422)
        provenance.update(candidate_fact_ids=[fact["id"]], candidate_evidence_ids=list(fact["evidence_ids"]))
    schema = ResolvedScopedAdaptiveDecision if uses_gap_scope(payload) else ResolvedAdaptiveDecision
    return schema(decision_version=VERSION, candidate_id=selection.candidate_id,
        target_knowledge_id=pair["target_knowledge_id"], question_source_ref=source["ref"],
        **selection.model_dump(exclude={"candidate_id", "proposed_question"}),
        proposed_question=QuestionArtifact(**selection.proposed_question.model_dump(), **provenance))


def bounded(value, maximum=96_000):
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > maximum:
        raise AppError("ADAPTIVE_CONTEXT_BUDGET", "自适应面试材料超出有界预算，未静默截断证据", 422)
    return value


def build_blueprint(context, *, deprioritized_topics=()):
    skill = context["interviewer_config"]["skill"]
    priorities = skill["priority_topics"]
    targets = {}
    for topic in priorities:
        targets[topic] = dict(knowledge_id=f"{skill['id']}:{topic}", topic=topic,
            label=LABELS.get(topic, topic), category="TECHNICAL", purpose="验证机制、实际应用和边界条件",
            scope="THIS_INTERVIEW_NOT_STABLE_SKILL", priority=0,
            # Published so the model can literally name the topic; the NEXT gate
            # still checks the question text exactly as before.
            mention_terms=topic_terms(topic),
            sources=[dict(ref=f"skill:{skill['id']}@{skill['version']}", source_type="DYNAMIC_GENERATED", source_id=None)])
    pools = [("requirement", "JOB_REQUIREMENT", context["requirements"], "statement", 4),
        ("public_question", "PUBLIC_QUESTION", context["public_questions"], "question", 3),
        ("fact", "CANDIDATE_PROFILE", context["candidate_facts"], "statement", 1)]
    for prefix, source_type, rows, text_key, weight in pools:
        for row in rows:
            if prefix == "fact" and row["field_path"].split(".")[0] not in ("skills", "projects", "work_experience", "internship_experience"):
                continue
            text = row.get(text_key, "")
            if is_hard_condition(text):
                continue
            for topic in mentioned_topics(text):
                if topic not in targets:
                    continue
                target = targets[topic]
                target["priority"] += weight
                if len(target["sources"]) < 9:
                    target["sources"].append(dict(ref=f"{prefix}:{row['id']}", source_type=source_type,
                        source_id=row["id"], fingerprint=row.get("fingerprint") or digest(text)))
    for experience in context.get("interview_experiences", []):
        for anchor in experience.get("reported_topics", []):
            for topic in mentioned_topics(anchor.get("raw_excerpt", "")):
                if topic in targets and len(targets[topic]["sources"]) < 9:
                    targets[topic]["priority"] += 2
                    targets[topic]["sources"].append(dict(ref=f"reported_topic:{experience['id']}:{anchor['fragment_id']}",
                        source_type="DYNAMIC_GENERATED", source_id=None, kind="REPORTED_TOPIC",
                        fingerprint=digest(anchor), reported_excerpt=anchor["raw_excerpt"],
                        source_snapshot_id=anchor["source_snapshot_id"], location=anchor["location"]))
    # Approved memory/observations affect focus, not proof of competence or JD truth.
    for layer in ("approved_memory_context", "recent_performance_observations"):
        for item in context.get(layer, {}).get("items", []):
            for topic in mentioned_topics(item.get("content", "")):
                if topic in targets:
                    targets[topic]["priority"] += 1
    limit = min(8, max(1, context["budgets"]["question_count"]))
    selected = sorted(targets.values(), key=lambda t: (t["topic"] in deprioritized_topics,
        -t["priority"], priorities.index(t["topic"])))[:limit]
    value = dict(version=VERSION, skill_id=skill["id"], skill_version=skill["version"], targets=selected,
        source_semantics="Reviewed reports are reported topics, not proof of company interview policy",
        fallback="GENERIC_SKILL" if not any(s["ref"].startswith("public_question:") for t in selected for s in t["sources"]) else "REVIEWED_JOB_SOURCES")
    value["fingerprint"] = digest(value)
    return bounded(value)


def knowledge_state(plan):
    value = (plan or {}).get("adaptive_knowledge", dict(version=VERSION, items={}, observations=[]))
    try:
        return bounded(KnowledgeState.model_validate(value).model_dump())
    except ValidationError as exc:
        raise AppError("ADAPTIVE_STATE_INVALID", "本场知识观察结构或版本无效，未猜测修复历史状态", 409) from exc


def resolve_observations(artifact, payload):
    adaptive = payload["adaptive"]
    active_id = adaptive["active_knowledge_id"]
    answer = payload["answer"]
    old = adaptive["knowledge_state"]["items"].get(active_id, {})
    gaps = {g["id"]: g for g in old.get("open_gaps", [])}
    scoped = uses_gap_scope(payload)
    focus = adaptive.get("focus_gap_ids") if scoped else None
    if scoped:
        validate_gap_scope(focus, gaps)
    result = []
    for item in artifact.knowledge_observations:
        if item.knowledge_id != active_id:
            raise AppError("UNKNOWN_KNOWLEDGE", "评价只能诊断本题冻结的考察目标", 422)
        if any(not quote.strip() or quote not in answer for quote in item.answer_quotes):
            raise AppError("UNSUPPORTED_ANSWER_QUOTE", "回答依据必须精确定位到本轮回答", 422)
        if item.state in ("DEMONSTRATED_IN_SESSION", "PARTIALLY_DEMONSTRATED") and not item.answer_quotes:
            raise AppError("ANSWER_SUPPORT_REQUIRED", "正向表现观察需要真实回答片段", 422)
        if set(item.resolved_gap_ids) - gaps.keys() or (item.resolved_gap_ids and not item.answer_quotes):
            raise AppError("UNKNOWN_KNOWLEDGE_GAP", "解决缺口必须引用真实旧缺口和本轮回答", 422)
        missing = [dict(id="gap_" + digest([active_id, text])[:24], text=text) for text in dict.fromkeys(item.missing_mechanisms)]
        remaining = [g for key, g in gaps.items() if key not in item.resolved_gap_ids]
        remaining = list({g["id"]: g for g in remaining + missing}.values())
        # Unknown scope is not empty scope. Preserve the old strict check
        # when a historical or incomplete question has no reliable metadata.
        unresolved_here = remaining if focus is None else (
            missing or [g for g in remaining if g["id"] in focus])
        if item.state == "DEMONSTRATED_IN_SESSION" and unresolved_here:
            raise AppError("UNRESOLVED_KNOWLEDGE_GAP", "仍有具体未解决缺口，不能标记本场充分展示", 422)
        row = dict(item.model_dump(), open_gaps=remaining,
            answer_spans=[dict(start=answer.index(q), end=answer.index(q) + len(q), excerpt=q) for q in item.answer_quotes])
        if scoped:
            row["question_state"] = item.state
            row["focus_gap_ids"] = focus
            if remaining and item.state == "DEMONSTRATED_IN_SESSION":
                row["state"] = "PARTIALLY_DEMONSTRATED"
        result.append(row)
    return result


def apply_observations(state, observations, *, question_id, answer_id, evaluation_id):
    state = deepcopy(state)
    for observation in observations:
        row = dict(observation, question_id=question_id, answer_id=answer_id, evaluation_id=evaluation_id)
        state["items"][row["knowledge_id"]] = row
        state["observations"].append(row)
    return bounded(KnowledgeState.model_validate(state).model_dump())


def thread_pacing(questions, active, before_state, after_state, evaluation, budgets):
    """Deterministic facts about the thread the interviewer is currently on.

    The model owns the strategy, but it should not have to infer whether its own
    last clarification moved the answer forward. ``stalled`` is derived from
    persisted questions plus the evaluated knowledge state, never from wording
    or answer length: one clarification has already been spent on this thread
    and the latest answer neither resolved an open gap nor produced a positive
    observation. When that is true the controller stops offering yet another
    follow-up on the same narrow point, so a short interview cannot spend every
    remaining slot re-asking one detail. A rich answer keeps follow-ups legal.
    """
    from interview_api.modules.interview.controller import SessionController
    if active is None:
        return None
    active_id = ((active.candidate_refs_json or {}).get("adaptive_decision") or {}).get("target_knowledge_id")
    used = SessionController.follow_up_consumption(questions, active)
    before = {g["id"] for g in (before_state.get("items", {}).get(active_id) or {}).get("open_gaps") or []}
    after = {g["id"] for g in (after_state.get("items", {}).get(active_id) or {}).get("open_gaps") or []}
    resolved = before - after
    observations = list(getattr(evaluation, "knowledge_observations", None) or []) if evaluation else []
    positive = any(item.state in ("DEMONSTRATED_IN_SESSION", "PARTIALLY_DEMONSTRATED") for item in observations)
    gained = bool(resolved) or positive
    technical = [q for q in questions if q.question_kind != "INTRODUCTION"]
    remaining = max(0, budgets["question_count"] - len(technical))
    pacing = coverage(questions, budgets["question_count"])
    return dict(
        root_question_id=SessionController.root_question_id(questions, active),
        follow_ups_used=used,
        follow_ups_allowed=budgets["max_follow_ups"],
        same_topic_streak=pacing["consecutive_count"],
        remaining_slots=remaining,
        latest_answer_added_information=gained if evaluation else None,
        resolved_gap_count=len(resolved),
        # Only the second clarification onward can be "stalled": the first one is
        # always a legitimate attempt to clear up a vague answer.
        stalled=bool(used >= 1 and evaluation is not None and not gained),
        # A soft hint, not a rule: with one slot left, a third question on the
        # same narrow point is usually worth less than a different topic.
        prefer_switch=bool(pacing["consecutive_count"] >= 2 and remaining <= 1),
    )


def decision_space(context, questions, active, state, *, allow_followups, must_switch=False):
    pacing = coverage(questions, context["budgets"]["question_count"])
    active_id = ((active.candidate_refs_json or {}).get("adaptive_decision") or {}).get("target_knowledge_id") if active else None
    active_source_ref = ((active.candidate_refs_json or {}).get("adaptive_decision") or {}).get(
        "question_source_ref") if active else None

    def build(allow):
        rows = []
        for target in context["adaptive_blueprint"]["targets"]:
            if target["topic"] in pacing["blocked_topics"]:
                continue
            item = state["items"].get(target["knowledge_id"], {})
            if item.get("state") == "DEMONSTRATED_IN_SESSION":
                continue
            strategies = ["NEXT"]
            exclude_source = None
            if active and active_id == target["knowledge_id"]:
                strategies = ["FOLLOW_UP", "REQUEST_EXAMPLE", "DEEP_DIVE"] if allow else []
                # The same target can still be approached from a *different* frozen
                # source: a project thread may move to the fundamentals of the same
                # subject instead of drilling the same detail again. The old source
                # is excluded so this cannot be a rewording of the active question.
                other_sources = [s for s in target["sources"] if s["ref"] != active_source_ref]
                if not must_switch and other_sources:
                    strategies = ["NEXT", *strategies]
                    exclude_source = active_source_ref
            if strategies:
                rows.append(dict(target=target, strategies=strategies,
                                 **({"exclude_source_ref": exclude_source} if exclude_source else {})))
        return rows

    choices = build(allow_followups)
    if must_switch and any(choice["target"]["knowledge_id"] != active_id for choice in choices):
        # Never strand the model without a legal choice: if nothing else is
        # available, the thread keeps its follow-ups instead of failing.
        switched = build(False)
        if switched:
            choices = switched
    return choices


def all_goals_demonstrated(context, state):
    targets = context["adaptive_blueprint"]["targets"]
    return bool(targets) and all(state["items"].get(t["knowledge_id"], {}).get("state") == "DEMONSTRATED_IN_SESSION"
        and not state["items"][t["knowledge_id"]].get("open_gaps") for t in targets)


def planning_payload(context, questions, active, plan, *, answer=None, answer_id=None, evaluation=None, evaluation_run_id=None):
    from interview_api.modules.interview.context import model_context
    from interview_api.modules.interview.controller import SessionController
    state = knowledge_state(plan)
    active_id = ((active.candidate_refs_json or {}).get("adaptive_decision") or {}).get("target_knowledge_id") if active else None
    adaptive = dict(version=VERSION, active_knowledge_id=active_id, knowledge_state=state,
        topic_coverage=coverage(questions, context["budgets"]["question_count"]))
    # The opening slot lasts until one scored technical question exists. A
    # scripted self-introduction does not consume it: the first technical
    # question may still open from the candidate's own project.
    adaptive["opening_slot"] = not any(
        (q.candidate_refs_json or {}).get("adaptive_decision") for q in questions)
    snapshot = model_context(context)
    sources = [s for t in context["adaptive_blueprint"]["targets"] for s in t["sources"]]
    refs = {s["ref"] for s in sources}
    snapshot["public_questions"] = [q for q in snapshot["public_questions"] if f"public_question:{q['id']}" in refs]
    snapshot["requirements"] = [r for r in snapshot["requirements"] if f"requirement:{r['id']}" in refs]
    snapshot["candidate_facts"] = [f for f in snapshot["candidate_facts"] if f"fact:{f['id']}" in refs]
    keys = {key for f in snapshot["candidate_facts"] for key in f["evidence_ids"]}
    snapshot["candidate_evidence"] = [e for e in snapshot["candidate_evidence"] if e["key"] in keys]
    # The frozen registry reconstructs prompts; don't send both complete prompt bodies in user context again.
    snapshot["adaptive_policy"] = deepcopy(context["adaptive_policy"])
    payload = dict(snapshot=snapshot, adaptive=adaptive,
        requested_action="NEXT", preferred_source={}, desired_difficulty=active.difficulty if active else "MEDIUM",
        current_question=dict(question_id=active.id, question=active.question) if active else None,
        rubric=(active.standard_answer or "").splitlines() if active else [], answer=answer,
        history=[dict(question_id=q.id, question=q.question, status=q.status,
            knowledge_id=((q.candidate_refs_json or {}).get("adaptive_decision") or {}).get("target_knowledge_id")) for q in questions],
        evaluation=evaluation.model_dump() if evaluation else None)
    if uses_gap_scope(payload) and active:
        # Controller already persists the validated decision atomically with
        # the question. Evaluation never supplies or changes this scope.
        adaptive["focus_gap_ids"] = ((active.candidate_refs_json or {}).get("adaptive_decision") or {}).get("focus_gap_ids")
    # Snapshot the state the thread arrived with, before this answer's pending
    # projection: whether the latest clarification resolved anything is exactly
    # the before/after difference.
    before_state = state
    if evaluation:
        observations = resolve_observations(evaluation, payload)
        # Pending projection is NOT persisted until the Evaluation commits.
        state = apply_observations(state, observations, question_id=active.id, answer_id=answer_id, evaluation_id=None)
    thread = thread_pacing(questions, active, before_state, state, evaluation, context["budgets"])
    can_probe = bool(active) and SessionController.follow_up_consumption(questions, active) < context["budgets"]["max_follow_ups"]
    adaptive.update(thread=thread,
        choices=decision_space(context, questions, active, state, allow_followups=can_probe,
                               must_switch=bool(thread and thread["stalled"])),
        knowledge_state=state, current_answer_ref=f"answer:{answer_id}" if answer_id else None,
        current_evaluation_ref=f"evaluation_run:{evaluation_run_id}" if evaluation_run_id else None)
    refs = {s["ref"] for t in context["adaptive_blueprint"]["targets"] for s in t["sources"]}
    refs.update(f"question:{q.id}" for q in questions)
    refs.update((context.get("reviewed_experience_scope") or {}).get("source_refs") or [])
    refs.update(r for r in (adaptive["current_answer_ref"], adaptive["current_evaluation_ref"]) if r)
    adaptive["allowed_refs"] = sorted(refs)
    if uses_pairs(payload):
        adaptive["candidates"] = decision_candidates(context, adaptive["choices"])
        legal_targets = {c["target_knowledge_id"] for c in adaptive["candidates"]}
        adaptive["choices"] = [c for c in adaptive["choices"] if c["target"]["knowledge_id"] in legal_targets]
        adaptive["allowed_refs"] = sorted(refs | {c["source"]["ref"] for c in adaptive["candidates"]})
    from interview_api.modules.interview.context_projection import enabled
    # The internal plan retains full provenance, frozen prompts and observation
    # evidence. Versioned role DTOs are separately bounded at their wire seam.
    return bounded(payload, maximum=192_000 if enabled(payload) else 96_000)


def is_project_opening(payload, source, artifact):
    """A natural technical question taken from the candidate's own real project.

    This relaxes ONLY the "name the topic literally" wording check, and only in
    the session's opening slot — before any scored technical question exists,
    i.e. a fresh session or one whose only prior question was the scripted
    self-introduction. Provenance, target legality and evidence binding are all
    still enforced by the caller and by ``validate_question``.
    """
    if artifact.strategy != "NEXT" or not payload["adaptive"].get("opening_slot"):
        return False
    if (source or {}).get("source_type") != "CANDIDATE_PROFILE":
        return False
    fact = next((f for f in payload["snapshot"].get("candidate_facts") or []
                 if f["id"] == source.get("source_id")), None)
    if fact is None or not is_project_fact(fact.get("field_path")):
        return False
    return refers_to_candidate_work(artifact.question.question)


def frozen_source_text(payload, source):
    """Frozen wording behind a candidate source, never model-authored text."""
    source_type = (source or {}).get("source_type")
    key = (source or {}).get("source_id")
    if source_type == "JOB_REQUIREMENT":
        rows = payload["snapshot"].get("requirements") or []
        row = next((item for item in rows if item.get("id") == key), None)
        return (row or {}).get("statement") or ""
    if source_type == "CANDIDATE_PROFILE":
        rows = payload["snapshot"].get("candidate_facts") or []
        row = next((item for item in rows if item.get("id") == key), None)
        return (row or {}).get("statement") or ""
    if source_type == "PUBLIC_QUESTION":
        rows = payload["snapshot"].get("public_questions") or []
        row = next((item for item in rows if item.get("id") == key), None)
        return (row or {}).get("question") or ""
    return ""


def source_grounds_topic(payload, source, topic):
    """A frozen requirement/fact can carry the target without restating it.

    build_blueprint only attaches such a source to a target because the frozen
    text itself mentions that topic. A question bound to that source is already
    semantically tied to the target; forcing the model to repeat one literal
    token turns natural Chinese into keyword reporting.
    """
    return bool(topic) and topic in mentioned_topics(frozen_source_text(payload, source))


def conflicting_topics(question, target_topic):
    """Explicit other technical subjects in the question, never inferred."""
    return sorted(topic for topic in mentioned_topics(question) if topic != target_topic)


def grounded_question(statement, label, source_type):
    """Zero-model wording for a source-bound fallback NEXT question."""
    text = (statement or "").strip()
    if not text:
        return (f"我们换个方向，聊聊{label}。请讲一个你实际遇到过的场景，"
                "重点说清楚你自己的判断依据。")
    if source_type == "JOB_REQUIREMENT":
        return (f"岗位要求里有一条：「{text}」。请结合你做过的一个项目，"
                f"讲讲你在{label}方面的具体做法，并说一次你踩过的坑。")
    if source_type == "CANDIDATE_PROFILE":
        return (f"你简历里提到：{text}。请围绕{label}讲清楚你自己负责的部分，"
                "以及当时一个需要取舍的具体决定。")
    return (f"我们接着看{label}：{text}。请讲一个你实际处理过的场景，"
            "重点说清楚你自己的判断依据。")


def fallback_selection(payload, rejected_target=None):
    """A deterministic, source-bound NEXT after the model drifted.

    It never accepts the rejected wording: a fresh question is built from a
    different frozen candidate and passes the same validate_decision gate.
    """
    adaptive = payload["adaptive"]
    refs = set(adaptive.get("allowed_refs") or [])
    support = [ref for ref in (adaptive.get("current_answer_ref"), adaptive.get("current_evaluation_ref"))
               if ref and ref in refs]
    next_candidates = [c for c in (adaptive.get("candidates") or []) if "NEXT" in (c.get("strategies") or [])]
    # Prefer another target with frozen wording, then the same target with a
    # different frozen source, then a generic Skill target named by its own
    # label. The rejected question itself is never reused.
    ordered = ([c for c in next_candidates if c["target_knowledge_id"] != rejected_target and
                frozen_source_text(payload, c["source"])] +
               [c for c in next_candidates if frozen_source_text(payload, c["source"])] +
               [c for c in next_candidates if c["target_knowledge_id"] != rejected_target] +
               next_candidates)
    seen = set()
    last = None
    for candidate in ordered:
        if candidate["candidate_id"] in seen:
            continue
        seen.add(candidate["candidate_id"])
        target = candidate["target_knowledge_id"]
        source = candidate["source"]
        label = candidate.get("label") or candidate.get("topic") or target.rsplit(":", 1)[-1]
        statement = frozen_source_text(payload, source)
        draft = QuestionDraft(question=grounded_question(statement, label, source["source_type"]),
            expected_points=[f"{label}的实际做法", "一次真实取舍"], dimension=label[:50],
            difficulty=payload["desired_difficulty"])
        selection = dict(candidate_id=candidate["candidate_id"], strategy="NEXT",
            supporting_refs=list(dict.fromkeys([source["ref"], *support]))[:12],
            reason_tags=["COVERAGE_SWITCH"], proposed_question=draft)
        if uses_gap_scope(payload):
            # NEXT does not have to test an existing open gap; [] is explicit scope.
            selection = ScopedAdaptiveSelection(**selection, focus_gap_ids=[])
        try:
            artifact = resolve_selection(selection, payload)
            return artifact, validate_decision(artifact, payload)
        except AppError as exc:
            # A stale candidate must not prevent trying the next legal source.
            last = exc
            continue
    reason = f"（最后一项：{getattr(last, 'code', last)}）" if last is not None else ""
    raise AppError("ADAPTIVE_FALLBACK_UNAVAILABLE", f"没有可安全复用的保底题目，请明确重试{reason}", 409)


def validate_decision(artifact, payload):
    adaptive = payload["adaptive"]
    choice = next((c for c in adaptive["choices"] if c["target"]["knowledge_id"] == artifact.target_knowledge_id), None)
    if not choice or artifact.strategy not in choice["strategies"]:
        raise AppError("ADAPTIVE_DECISION_REJECTED", "目标或策略不在当前合法空间，原回答可安全重试", 422)
    if uses_gap_scope(payload):
        gaps = {g["id"] for g in adaptive["knowledge_state"]["items"].get(
            artifact.target_knowledge_id, {}).get("open_gaps", [])}
        focus = getattr(artifact, "focus_gap_ids", None)
        try:
            validate_gap_scope(focus, gaps, required=True)
        except AppError as exc:
            if not getattr(exc, "diagnostic", None):
                exc.diagnostic = gap_scope_diagnostic(artifact, adaptive, gaps)
            raise
        if gaps and not focus and artifact.strategy == "FOLLOW_UP":
            error = AppError("INVALID_QUESTION_GAP_SCOPE", "追问已有缺口时须明确聚焦至少一个开放缺口", 422)
            error.diagnostic = gap_scope_diagnostic(artifact, adaptive, gaps)
            raise error
    if set(artifact.supporting_refs) - set(adaptive["allowed_refs"]):
        raise AppError("UNKNOWN_DIRECTOR_REFERENCE", "面试决策引用不存在或不属于本场", 422)
    if payload.get("answer") is not None and not set(artifact.supporting_refs) & {adaptive["current_answer_ref"], adaptive["current_evaluation_ref"]}:
        raise AppError("DIRECTOR_ANSWER_REQUIRED", "后续决策必须引用本轮实际回答或评价", 422)
    if uses_pairs(payload):
        pair = next((c for c in adaptive["candidates"] if c["candidate_id"] == getattr(artifact, "candidate_id", None)), None)
        if pair is None:
            raise AppError("UNKNOWN_DIRECTOR_CANDIDATE", "未知或过期的目标题源组合", 422)
        if pair["target_knowledge_id"] != artifact.target_knowledge_id or pair["source"]["ref"] != artifact.question_source_ref:
            raise AppError("DIRECTOR_PAIR_MISMATCH", "不能将组合的目标或来源替换为其他引用", 422)
        source = pair["source"]
    else:
        source = next((s for s in choice["target"]["sources"] if s["ref"] == artifact.question_source_ref), None)
    if not source:
        raise AppError("UNKNOWN_DIRECTOR_SOURCE", "题源必须属于选定考察目标", 422)
    project_opening = is_project_opening(payload, source, artifact)
    target = choice["target"]
    topic = target["topic"]
    if artifact.strategy == "NEXT" and topic not in mentioned_topics(artifact.question.question) and not project_opening:
        # A NEXT may speak naturally about the frozen project/requirement that
        # already carries this target. It may NOT wander into another explicit
        # technical subject, and a generic Skill candidate still has to name
        # its own topic because no frozen wording ties the question to it.
        grounded = source_grounds_topic(payload, source, topic)
        conflicts = conflicting_topics(artifact.question.question, topic)
        if not grounded or conflicts:
            label = target.get("label") or topic
            terms = "、".join(topic_terms(topic)) or topic
            recovery = ("可以重新生成第一题，也可以改用岗位要求开场。" if adaptive.get("opening_slot")
                        else "本题不保存、不评价；可以继续处理本轮，不会重新评价上一题。")
            error = AppError("ADAPTIVE_TOPIC_MISMATCH",
                f"这一题偏离了「{label}」（该主题需点到：{terms}）。{recovery}", 422)
            # Kept off the wire; the service writes it to the audit trail so a
            # rejected question is diagnosable instead of vanishing.
            error.diagnostic = topic_diagnostic(artifact.question.question, topic,
                source_ref=source["ref"], source_grounded=grounded, conflicting_topics=conflicts)
            raise error
    policy = dict(source_type=source["source_type"], source_id=source["source_id"],
        target_topic=choice["target"]["topic"], target_category="TECHNICAL",
        topic_coverage=adaptive["topic_coverage"])
    if artifact.strategy in ("FOLLOW_UP", "REQUEST_EXAMPLE", "DEEP_DIVE"):
        policy.update(source_type="DYNAMIC_GENERATED" if artifact.strategy == "DEEP_DIVE" else "FOLLOW_UP", source_id=None)
    if project_opening:
        policy["project_opening"] = True
    from interview_api.modules.interview.skill_engine import interaction_policy
    policy = interaction_policy(payload["snapshot"], policy)
    from interview_api.modules.interview.context import validate_question
    validate_question(artifact.question, payload["snapshot"], policy)
    if artifact.question.difficulty != payload["desired_difficulty"]:
        raise AppError("DIFFICULTY_POLICY", "V2.1 保持本场既定难度，风格不能改变评分或难度", 422)
    return policy


def review_projection(context, plan):
    state = knowledge_state(plan)
    rows = []
    for target in context["adaptive_blueprint"]["targets"]:
        item = state["items"].get(target["knowledge_id"])
        if item:
            rows.append(dict(label=target["label"], state=item["state"],
                missing_mechanisms=[g["text"] for g in item["open_gaps"]],
                observations=[dict(question_id=o["question_id"], evaluation_id=o["evaluation_id"], state=o["state"],
                    answer_spans=o["answer_spans"], resolved_gap_count=len(o["resolved_gap_ids"]))
                    for o in state["observations"] if o["knowledge_id"] == target["knowledge_id"]]))
    return dict(version=VERSION, semantics="仅描述本场回答，不是永久技能结论", targets=rows)
