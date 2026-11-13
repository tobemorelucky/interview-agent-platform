"""Read-time, deterministic training candidates. No model, no writes, no state.

Round 16 productises the round-15 *test* baseline without copying it: that
helper consumed a synthetic ``training_scope`` dict. This module consumes the
real ``target_views()`` / ``CapabilityMemoryService.list()`` projections, and
takes the legal training modes from ``interview.training.modes()`` -- the exact
function ``freeze_training`` uses to admit a creation request.

It is deliberately a projection, not a stored fact:
  * nothing is written to CapabilityMemory or to any report;
  * the Harness-OFF prompt / schema / generation_key are untouched;
  * a Coach report's historical conclusion and *the state right now* are kept
    apart by ``computed_at`` / ``state_fingerprint``;
  * clicking a candidate is still a separate, fully re-validated creation.
"""
import hashlib
import json

# Same ordering the formal memory plan uses; kept here only for candidate
# ranking, never to decide whether a mode is legal.
STATE_PRIORITY = dict(CONFLICTED=4, RECHECK_DUE=3, GAP_SUSPECTED=2, PRACTICING=1,
                      VERIFIED=0, UNKNOWN=0)
STATE_REASONS = {
    "CONFLICTED": "同一考察范围内先有独立达标、后有不达标表现，冲突尚未由独立复测解决；按现有准入规则只能独立复测。",
    "RECHECK_DUE": "此前有跨场次独立达标，但距上次独立验证已超过规则期限；按现有准入规则需要复核。",
    "GAP_SUSPECTED": "最近一次正式评价仍有未覆盖的关键机制；按现有准入规则进入薄弱项专项训练。",
    "PRACTICING": "有正式评价支持，但尚未形成跨场次独立掌握；按现有准入规则继续针对性练习。",
}
PROJECTION_VERSION = "TRAINING_CANDIDATES_V1"
CAPABILITY_SCOPE = "CROSS_JOB_TOPIC_AGGREGATE"
CAPABILITY_SCOPE_NOTE = (
    "能力状态按 (用户, topic) 跨岗位聚合，不是本岗位掌握度，也不是候选人确认的事实。"
    "当前岗位相关性只来自该 topic 出现在本场专业规则的合法考察目标里；"
    "通用能力 VERIFIED 不能解释为当前岗位已完全胜任。"
)
SEMANTICS = (
    "这些候选项是读取这一刻按正式能力状态与 training.modes() 重新计算的规则投影，"
    "不是报告生成时模型给出的历史结论，不写入能力记忆，也不自动创建训练会话。"
)


def capability_state_fingerprint(states):
    """Stable key over the formal state rows a candidate set was built from."""
    payload = [[str(s.get("topic")), s.get("state"), s.get("revision"),
                s.get("last_observed_at"), bool(s.get("revoked"))]
               for s in sorted(states, key=lambda row: str(row.get("topic")))]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _topic(knowledge_id):
    return knowledge_id.rsplit(":", 1)[-1]


def _launch_query(candidate, *, skill_id, job_target_id, job_id, target_version):
    query = dict(mode=candidate["mode"], recommended_skill_id=skill_id)
    if candidate["knowledge_id"]:
        query["knowledge_id"] = candidate["knowledge_id"]
    if job_target_id:
        query["job_target_id"] = str(job_target_id)
        if target_version is not None:
            query["target_version"] = str(target_version)
    elif job_id:
        query["job_id"] = str(job_id)
    return query


def build_training_candidates(views, states, *, skill_id, computed_at,
                              job_target_id=None, job_id=None, target_version=None,
                              target_priorities=None):
    """Pure function: legal, evidence-bearing candidates from formal projections.

    ``views`` comes from ``training.target_views(context, states)``; every
    ``available_modes`` in it was produced by ``training.modes()``. We never
    widen that set. A state with only FULL yields no *targeted* candidate.
    """
    priorities = target_priorities or {}
    index = {str(s.get("topic")): s for s in states}
    current_topics = {_topic(v["knowledge_id"]) for v in views}
    candidates = []
    for view in views:
        kid, state = view["knowledge_id"], view["state"]
        targeted = [m for m in view["available_modes"] if m != "FULL"]
        if not targeted:
            continue  # UNKNOWN / VERIFIED / revoked: never invent a weakness.
        if any(m not in view["available_modes"] for m in targeted):
            continue  # defensive: this cannot happen, but never relax admission.
        observations = view.get("observations") or []
        evaluation_refs = [f"evaluation:{o['evaluation_id']}" for o in observations]
        row = index.get(_topic(kid), {})
        mode = targeted[0]
        priority = STATE_PRIORITY.get(state, 0) * 10 + min(int(priorities.get(kid, 0)), 4)
        candidates.append(dict(
            knowledge_id=kid, topic=_topic(kid), label=view["label"], skill_id=skill_id,
            mode=mode, available_modes=list(view["available_modes"]),
            state=state, state_revision=view.get("revision"),
            state_last_observed_at=row.get("last_observed_at"),
            priority=priority,
            reason=STATE_REASONS.get(state, "当前正式能力状态允许该训练模式。"),
            reason_tags=list(view.get("reason_tags") or []),
            evidence=dict(
                capability_source="CAPABILITY_MEMORY_SERVICE",
                memory_revision=view.get("revision"),
                independent_sessions=view.get("independent_sessions", 0),
                evaluation_refs=evaluation_refs,
                observations=[dict(evaluation_id=o["evaluation_id"], session_id=o["session_id"],
                                   observed_at=o["observed_at"], assisted=o["assisted"], state=o["state"])
                              for o in observations],
                conflicts=list(view.get("conflicts") or []),
                evidence_note=("引用的是已提交并计入能力记忆的正式评价与修订；"
                               "没有正式评价时该列表为空，不编造弱项。"
                               if evaluation_refs else "尚无正式评价引用，不编造弱项或复测状态。"),
            ),
            capability_scope=CAPABILITY_SCOPE,
            target_relevance="CURRENT_TARGET_BLUEPRINT",
            scope_note=CAPABILITY_SCOPE_NOTE,
            revalidation_note=("点击开始训练时，服务端会重新校验身份、当前状态、JD 版本与模式准入；"
                               "状态若已变化，创建会被拒绝，请刷新候选。"),
        ))
    candidates.sort(key=lambda c: (-c["priority"], c["knowledge_id"], c["mode"]))

    # Cross-job history is reusable *evidence*, but is never presented as a
    # currently actionable training goal for this target.
    general_only = [dict(topic=str(s.get("topic")), label=s.get("label") or str(s.get("topic")),
                         state=s.get("state"), memory_revision=s.get("revision"),
                         observation_count=len(s.get("observations") or []),
                         relation="GENERAL_HISTORY_NOT_CURRENT_TARGET",
                         note="跨岗位聚合的通用历史状态，不作为当前岗位的直接训练候选。")
                    for s in sorted(states, key=lambda row: str(row.get("topic")))
                    if str(s.get("topic")) not in current_topics and (s.get("observations") or [])]

    empty_reason = None
    if not views:
        empty_reason = "NO_LEGAL_TARGETS"
    elif not candidates:
        any_observation = any(v.get("observations") for v in views)
        empty_reason = ("NO_ACTIONABLE_WEAKNESS_OR_RECHECK" if any_observation
                        else "NO_HISTORY_EVALUATIONS")

    result = dict(
        projection=PROJECTION_VERSION, source="DETERMINISTIC_RULE", model_calls=0,
        computed_at=computed_at, state_fingerprint=capability_state_fingerprint(states),
        skill_id=skill_id, job_target_id=job_target_id, job_id=job_id,
        target_version=target_version,
        candidates=candidates, general_only=general_only,
        empty_reason=empty_reason, semantics=SEMANTICS,
        verified_note=("通用能力 VERIFIED 只说明该 topic 有跨场次独立支持，"
                       "不代表当前岗位已完全胜任，也不触发针对性训练候选。"),
        disclaimer=("规则候选与模型 Coach 建议来源不同：模型建议是报告生成时的历史结论，"
                    "此处候选是读取时按当前正式状态重算的可执行方向；两者不等价。"),
    )
    if not candidates:
        # FULL is always legal (modes() returns it for every state). Offer it
        # explicitly as a generic start, never as a fabricated weakness.
        result["fallback"] = dict(mode="FULL", reason="尚无针对性弱项或复测条件；可从完整模拟面试开始积累正式评价。",
                                  evidence_refs=[], launch=dict(path="/training", query=_launch_query(
                                      dict(mode="FULL", knowledge_id=""), skill_id=skill_id,
                                      job_target_id=job_target_id, job_id=job_id,
                                      target_version=target_version)))
    for candidate in candidates:
        candidate["launch"] = dict(path="/training", query=_launch_query(
            candidate, skill_id=skill_id, job_target_id=job_target_id,
            job_id=job_id, target_version=target_version))
    return result
