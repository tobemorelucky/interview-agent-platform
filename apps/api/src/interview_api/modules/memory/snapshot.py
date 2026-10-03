"""Bounded, read-only Memory/observation projections pinned once per session."""
from datetime import datetime, timezone
import json

from sqlalchemy import exists, func, or_, select

from interview_api.core.config import settings
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.memory.models import UserMemoryEvent, UserMemoryItem
from interview_api.modules.memory.proposal_models import MemoryProposal


def _bytes(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _bounded(items, *, limit, budget, total, semantics, epistemic_status):
    """UTF-8 serialized bytes conservatively account for content AND item metadata.

    This is not an exact model tokenizer. No token/size figures are model-produced.
    Each emitted item includes its actual clipped content, not an untruncated prompt.
    """
    def envelope(selected, overhead):
        result = dict(semantics=semantics, epistemic_status=epistemic_status, is_stable_fact=False,
            items=selected, selected_count=len(selected), omitted_count=max(0, total - len(selected)),
            budgets=dict(item_limit=limit, token_budget=budget, accounting="FULL_CONTEXT_UTF8_BYTES_CONSERVATIVE_NOT_EXACT_TOKENIZER"),
            estimated_tokens=0, stored_context_bytes=0, fixed_overhead_bytes=overhead,
            budget_exceeded_by_fixed_overhead=overhead > budget)
        # All fields, including this accounting envelope/fingerprint, consume budget.
        # Stabilize self-reported byte-count digit lengths (the fingerprint is fixed size).
        for _ in range(12):
            result["fingerprint"] = digest({k: v for k, v in result.items() if k != "fingerprint"})
            size = _bytes(result)
            if result["stored_context_bytes"] == size:
                return result
            result["stored_context_bytes"] = result["estimated_tokens"] = size
        raise RuntimeError("Memory context byte accounting did not stabilize")

    overhead = 0
    for _ in range(12):
        empty = envelope([], overhead)
        size = empty["stored_context_bytes"]
        if size == overhead:
            break
        overhead = size
    selected = []
    used = overhead
    for item in items[:max(0, limit)]:
        if budget <= used:
            break
        actual = dict(item)
        original = actual["content"]
        low, high = 0, len(original)
        while low < high:
            mid = (low + high + 1) // 2
            actual["content"] = original[:mid]
            actual["content_truncated"] = mid < len(original)
            actual["fingerprint"] = digest({k: v for k, v in actual.items() if k != "fingerprint"})
            if envelope(selected + [actual], overhead)["stored_context_bytes"] <= budget:
                low = mid
            else:
                high = mid - 1
        if not low:
            break
        actual["content"] = original[:low]
        actual["content_truncated"] = low < len(original)
        actual["fingerprint"] = digest({k: v for k, v in actual.items() if k != "fingerprint"})
        selected.append(actual)
        used = envelope(selected, overhead)["stored_context_bytes"]
    return envelope(selected, overhead)


async def build_memory_snapshot(db, user_id):
    """No commits, read events, access-time changes, skill scores or mutable per-turn reads."""
    now = datetime.now(timezone.utc)
    # Most recent CONTENT-changing event must be a real user confirmation. READ events
    # deliberately do not count, even though legacy read APIs mark them actor_type USER.
    latest_content_event = select(UserMemoryEvent).where(
        UserMemoryEvent.memory_item_id == UserMemoryItem.id,
        UserMemoryEvent.user_id == user_id,
        UserMemoryEvent.event_type.in_(("CREATED", "UPDATED", "CONSOLIDATED", "CONFIRMED")),
    ).order_by(UserMemoryEvent.created_at.desc(), UserMemoryEvent.id.desc()).limit(1)
    last_actor = latest_content_event.with_only_columns(UserMemoryEvent.actor_type).scalar_subquery()
    last_actor_id = latest_content_event.with_only_columns(UserMemoryEvent.actor_id).scalar_subquery()
    last_event_id = latest_content_event.with_only_columns(UserMemoryEvent.id).scalar_subquery()
    approved_proposal = exists(select(MemoryProposal.id).where(
        MemoryProposal.memory_item_id == UserMemoryItem.id,
        MemoryProposal.user_id == user_id,
        MemoryProposal.confirmed_by == user_id,
        MemoryProposal.status.in_(("APPROVED", "CORRECTED")),
    ))
    conditions = (
        UserMemoryItem.user_id == user_id, UserMemoryItem.status == "ACTIVE",
        UserMemoryItem.scope.in_(("INTERVIEW", "RESUME", "SYSTEM")),
        or_(UserMemoryItem.expires_at.is_(None), UserMemoryItem.expires_at > now),
        last_actor == "USER", last_actor_id == user_id,
        or_(UserMemoryItem.source_type.is_(None), UserMemoryItem.source_type != "MEMORY_PROPOSAL", approved_proposal),
    )
    memory_total = await db.scalar(select(func.count()).select_from(UserMemoryItem).where(*conditions))
    memory_limit = max(0, settings.interview_approved_memory_limit)
    rows = (await db.execute(select(UserMemoryItem, last_event_id).where(*conditions)
        .order_by(UserMemoryItem.updated_at.desc(), UserMemoryItem.id.desc()).limit(memory_limit))).all() if memory_limit else []
    memories = [row[0] for row in rows]
    event_ids = [row[1] for row in rows]
    ids = [m.id for m in memories]
    # Fetch the exact event IDs from the same query that selected each content body;
    # do not swap its provenance for a later concurrent confirmation.
    events = (await db.scalars(select(UserMemoryEvent).where(UserMemoryEvent.id.in_(event_ids)))).all() if ids else []
    latest_events = {event.memory_item_id: event for event in events}
    proposals = (await db.scalars(select(MemoryProposal).where(MemoryProposal.memory_item_id.in_(ids),
        MemoryProposal.user_id == user_id, MemoryProposal.confirmed_by == user_id,
        MemoryProposal.status.in_(("APPROVED", "CORRECTED"))))).all() if ids else []
    by_memory = {p.memory_item_id: p for p in proposals}
    memory_items = []
    for memory in memories:
        event, proposal = latest_events[memory.id], by_memory.get(memory.id)
        provenance = dict(confirmation_event_id=event.id, changed_by="user", confirmed_by=event.actor_id,
            confirmed_at=event.created_at.isoformat(), confirmation_event_type=event.event_type)
        if proposal:
            provenance.update(proposal_id=proposal.id, report_id=proposal.report_id, session_id=proposal.session_id,
                supporting_evaluation_ids=list(proposal.supporting_evaluation_ids),
                observation_ids=list(proposal.observation_ids), proposal_confirmation_at=proposal.confirmed_at.isoformat())
        memory_items.append(dict(id=memory.id, content=memory.content, memory_type=memory.memory_type,
            updated_at=memory.updated_at.isoformat(),
            version=dict(updated_at=memory.updated_at.isoformat(), content_fingerprint=digest(memory.content)),
            provenance=provenance, user_confirmed=True))

    from interview_api.modules.interview_report.models import InterviewPerformanceObservation, FinalInterviewReport
    observation_limit = max(0, settings.interview_observation_limit)
    observation_conditions = (InterviewPerformanceObservation.user_id == user_id,
        FinalInterviewReport.user_id == user_id, FinalInterviewReport.is_current.is_(True),
        FinalInterviewReport.generation_status == "COMPLETED")
    observation_query = select(InterviewPerformanceObservation).join(FinalInterviewReport,
        FinalInterviewReport.id == InterviewPerformanceObservation.report_id).where(*observation_conditions)
    observation_total = await db.scalar(select(func.count()).select_from(observation_query.subquery()))
    observations = (await db.scalars(observation_query
        .order_by(InterviewPerformanceObservation.created_at.desc(), InterviewPerformanceObservation.id.desc())
        .limit(observation_limit))).all() if observation_limit else []
    observation_items = [dict(id=o.id, session_id=o.session_id, report_id=o.report_id,
        content=o.text, supporting_evaluation_ids=list(o.supporting_evaluation_ids),
        created_at=o.created_at.isoformat(), is_stable_fact=False, user_confirmed=False) for o in observations]
    return dict(
        approved_memory_context=_bounded(memory_items, limit=memory_limit,
            budget=max(0, settings.interview_approved_memory_token_budget), total=memory_total,
            semantics="Explicitly user-confirmed Career Memory; not resume evidence or proof of stable skill mastery.",
            epistemic_status="USER_CONFIRMED_MEMORY_NOT_OBJECTIVE_TRUTH"),
        recent_performance_observations=_bounded(observation_items, limit=observation_limit,
            budget=max(0, settings.interview_observation_token_budget), total=observation_total,
            semantics="Limited historical interview-answer performance observations; not stable candidate facts or user confirmation.",
            epistemic_status="INTERVIEW_PERFORMANCE_OBSERVATION_NOT_USER_CONFIRMED"),
    )
