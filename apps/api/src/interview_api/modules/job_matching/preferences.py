"""Read-only projection of the existing Memory truth source; never writes Memory."""
from datetime import datetime, timezone
from sqlalchemy import exists, or_, select, func
from interview_api.core.config import settings
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.memory.models import UserMemoryItem, UserMemoryEvent
from interview_api.modules.memory.proposal_models import MemoryProposal
from interview_api.modules.memory.snapshot import _bounded


async def approved_preferences(db, user_id):
    last = select(UserMemoryEvent).where(UserMemoryEvent.user_id == user_id,
        UserMemoryEvent.memory_item_id == UserMemoryItem.id,
        UserMemoryEvent.event_type.in_(("CREATED", "UPDATED", "CONSOLIDATED", "CONFIRMED")))
    last = last.order_by(UserMemoryEvent.created_at.desc(), UserMemoryEvent.id.desc()).limit(1)
    actor = last.with_only_columns(UserMemoryEvent.actor_type).scalar_subquery()
    actor_id = last.with_only_columns(UserMemoryEvent.actor_id).scalar_subquery()
    event_id = last.with_only_columns(UserMemoryEvent.id).scalar_subquery()
    approved = exists(select(MemoryProposal.id).where(MemoryProposal.memory_item_id == UserMemoryItem.id,
        MemoryProposal.user_id == user_id, MemoryProposal.confirmed_by == user_id,
        MemoryProposal.status.in_(("APPROVED", "CORRECTED")),
        MemoryProposal.proposal_type.in_(("PREFERENCE", "GOAL"))))
    goal = exists(select(MemoryProposal.id).where(MemoryProposal.memory_item_id == UserMemoryItem.id,
        MemoryProposal.user_id == user_id, MemoryProposal.status.in_(("APPROVED", "CORRECTED")), MemoryProposal.proposal_type == "GOAL"))
    conditions = (UserMemoryItem.user_id == user_id, UserMemoryItem.status == "ACTIVE",
        UserMemoryItem.visibility == "PRIVATE", UserMemoryItem.scope.in_(("INTERVIEW", "RESUME", "SYSTEM")),
        or_(UserMemoryItem.expires_at.is_(None), UserMemoryItem.expires_at > datetime.now(timezone.utc)),
        actor == "USER", actor_id == user_id,
        or_(UserMemoryItem.memory_type == "PREFERENCE", (UserMemoryItem.memory_type == "SEMANTIC") &
            (goal | UserMemoryItem.key.in_(("goal", "career_goal")))),
        or_(UserMemoryItem.source_type.is_(None), UserMemoryItem.source_type != "MEMORY_PROPOSAL", approved))
    total = await db.scalar(select(func.count()).select_from(UserMemoryItem).where(*conditions))
    rows = (await db.execute(select(UserMemoryItem, event_id).where(*conditions)
        .order_by(UserMemoryItem.updated_at.desc(), UserMemoryItem.id.desc()).limit(settings.job_matching_preference_limit))).all()
    items = [dict(id=m.id, content=m.content, memory_type=m.memory_type, user_confirmed=True,
        confirmation_event_id=eid, source_type=m.source_type, source_id=m.source_id,
        version=dict(updated_at=m.updated_at.isoformat(), content_fingerprint=digest(m.content))) for m, eid in rows]
    return _bounded(items, limit=settings.job_matching_preference_limit, budget=settings.job_matching_preference_budget,
        total=total, semantics="Explicitly approved/corrected preference/goal only; qualitative preference, never qualification or skill evidence.",
        epistemic_status="USER_CONFIRMED_PREFERENCE_NOT_HARD_ELIGIBILITY")
