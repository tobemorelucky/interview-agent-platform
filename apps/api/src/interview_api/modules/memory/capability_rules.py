"""Explainable, replayable inference, NOT user-confirmed facts. No model or I/O."""
from datetime import datetime, timezone

RULE_VERSION = "2.2.0"
DEFAULT_POLICY = dict(single_context_days=30, independent_days=60, diversified_days=90,
    retrieval_days=180, max_plan_items=3, max_plan_chars=6000)


def instant(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def project(observations, now, *, excluded=(), revoked=False, policy=None):
    policy = {**DEFAULT_POLICY, **(policy or {})}
    rows = sorted({o["evaluation_id"]: o for o in observations
        if o["evaluation_id"] not in excluded and instant(o["observed_at"]) <= now}.values(),
        key=lambda o: (instant(o["observed_at"]), o["evaluation_id"]))
    if revoked:
        rows = []
    def good(o):
        return (o["state"] == "DEMONSTRATED_IN_SESSION" and o["sufficient_quote"]
            and not o["open_gaps"] and not o["disputed"])
    independent = [o for o in rows if good(o) and not o["assisted"]]
    sessions = {o["session_id"] for o in independent}
    scopes = {o["scope"] for o in independent}
    conflicts, reasons, differences = [], [], []
    for i, row in enumerate(rows):
        same = [o for o in rows[:i] if o["scope"] == row["scope"] and not o["assisted"]]
        opposite = [o for o in same if good(o) and not good(row)]
        if row["disputed"] or (opposite and not row["assisted"]):
            later = {o["session_id"] for o in rows[i+1:] if o["scope"] == row["scope"] and good(o) and not o["assisted"]}
            conflicts.append(dict(evaluation_ids=[o["evaluation_id"] for o in opposite[-1:]] + [row["evaluation_id"]],
                scope=row["scope"], reason="EVALUATION_DISPUTE" if row["disputed"] else "OPPOSITE_INDEPENDENT_OBSERVATIONS",
                status="RESOLVED_BY_INDEPENDENT_RECHECK" if len(later) >= 2 else "OPEN"))
        if not good(row) and any(good(o) and o["scope"] != row["scope"] for o in rows[:i]):
            differences.append(dict(scope=row["scope"], evaluation_id=row["evaluation_id"]))
    latest = rows[-1] if rows else None
    last_verified = max((instant(o["observed_at"]) for o in independent), default=None) if len(sessions) >= 2 else None
    # Contradictory evidence always wins over an old aggregate, never overwritten.
    state = "UNKNOWN"
    if latest:
        if any(c["status"] == "OPEN" for c in conflicts):
            state = "CONFLICTED"
            reasons.append("INDEPENDENT_RECHECK_REQUIRED")
        elif good(latest):
            state = "VERIFIED" if len(sessions) >= 2 and not latest["assisted"] else "PRACTICING"
            reasons.append("MULTI_SESSION_INDEPENDENT_SUPPORT" if state == "VERIFIED" else
                "ASSISTED_SUCCESS_NOT_MASTERY" if latest["assisted"] else "ONE_INDEPENDENT_SESSION_ONLY")
        else:
            state = "PRACTICING" if latest["state"] == "PARTIALLY_DEMONSTRATED" else "GAP_SUSPECTED"
            reasons.append("RECENT_MISSING_EVIDENCE")
            if any(good(o) and o["assisted"] for o in rows):
                reasons.append("HINT_DEPENDENCE")
    days = policy["diversified_days"] if len(sessions) >= 3 and len(scopes) >= 2 else (
        policy["independent_days"] if len(scopes) >= 2 else policy["single_context_days"])
    if state == "VERIFIED" and last_verified and (now - last_verified).total_seconds() >= days * 86400:
        state = "RECHECK_DUE"
        reasons.append("VERIFICATION_EXPIRED")
    if state == "CONFLICTED":
        disputed_ids = {c["evaluation_ids"][-1] for c in conflicts if c["status"] == "OPEN"}
        cutoff = min(instant(o["observed_at"]) for o in rows if o["evaluation_id"] in disputed_ids)
        before = [o for o in independent if instant(o["observed_at"]) < cutoff]
        last_verified = max((instant(o["observed_at"]) for o in before), default=None) if len({o["session_id"] for o in before}) >= 2 else None
    tasks = []
    if state in ("GAP_SUSPECTED", "PRACTICING", "RECHECK_DUE", "CONFLICTED"):
        from interview_api.modules.candidate_profile.evidence import digest
        tasks.append(dict(id="verify_" + digest(dict(refs=[o["evaluation_id"] for o in rows], state=state))[:20],
            status="OPEN", intent="INDEPENDENT_VERIFICATION", reason_tags=reasons,
            source_evaluation_ids=[o["evaluation_id"] for o in rows[-3:]]))
    return dict(state=state, rule_version=RULE_VERSION, observations=rows, independent_sessions=len(sessions),
        last_verified_at=last_verified.isoformat() if last_verified else None,
        last_observed_at=latest["observed_at"] if latest else None, recheck_after_days=days,
        reason_tags=reasons, conflicts=conflicts, scope_differences=differences,
        verification_tasks=tasks, verification_required=bool(tasks))


def memory_plan(states, targets, now, *, policy=None):
    """References constrain relevance, never grant new question-source permissions."""
    import json
    from interview_api.modules.candidate_profile.evidence import digest
    policy = {**DEFAULT_POLICY, **(policy or {})}
    index = {s["topic"]: s for s in states if not s.get("revoked")}
    candidates = []
    priorities = dict(CONFLICTED=4, RECHECK_DUE=3, GAP_SUSPECTED=2, PRACTICING=1)
    for target in targets:
        topic = target["knowledge_id"].rsplit(":", 1)[-1]
        row = index.get(topic)
        if not row or not row["verification_required"] or row["state"] not in priorities:
            continue
        age = (now - instant(row["last_observed_at"])).days if row["last_observed_at"] else 9999
        if age > policy["retrieval_days"] and target.get("priority", 0) < 4:
            continue
        gaps = list(dict.fromkeys(g for o in row["observations"][-3:] for g in o["open_gaps"]))[:3]
        summary = ("此前经过提示或追问的回答不算独立掌握，本次建议换场景独立验证。" if
                any(o["assisted"] for o in row["observations"]) else "根据历史面试表现推断，本次建议核对具体机制；这不是候选人确认的事实。")
        candidates.append(dict(knowledge_id=target["knowledge_id"], label=target.get("label", topic), state=row["state"],
            intent="INDEPENDENT_VERIFICATION", priority=priorities[row["state"]] + min(target.get("priority", 0), 4),
            needs_recheck=row["state"] in ("CONFLICTED", "RECHECK_DUE"),
            reason_tags=row["reason_tags"], memory_revision=row["revision"],
            observation_refs=[f"evaluation:{o['evaluation_id']}" for o in row["observations"][-3:]],
            history_summary=("历史待核对：" + "；".join(g[:120] for g in gaps) + "。" if gaps else "") + summary))
    items = []
    for item in sorted(candidates, key=lambda c: (-c["priority"], c["knowledge_id"]))[:policy["max_plan_items"]]:
        if len(json.dumps(items + [item], ensure_ascii=False)) <= policy["max_plan_chars"]:
            items.append(item)
    deprioritized = [dict(knowledge_id=t["knowledge_id"], memory_revision=index[t["knowledge_id"].rsplit(":", 1)[-1]]["revision"],
        reason="RECENT_INDEPENDENT_VERIFICATION") for t in targets if t["knowledge_id"].rsplit(":", 1)[-1] in index and
        index[t["knowledge_id"].rsplit(":", 1)[-1]]["state"] == "VERIFIED"][:8]
    value = dict(version=RULE_VERSION, inferred=True, items=items, deprioritized_targets=deprioritized, frozen_at=now.isoformat())
    return dict(**value, fingerprint=digest(value))
