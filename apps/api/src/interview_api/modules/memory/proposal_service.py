"""Proposal persistence and transactional, explicit user Memory approval."""
from datetime import datetime, timezone
import re

from sqlalchemy import select

from interview_api.core.exceptions import AppError
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.interview.artifacts import DIMENSIONS
from interview_api.modules.interview.models import InterviewEvaluation, InterviewSessionQuestion
from interview_api.modules.memory.models import UserMemoryEvent, UserMemoryItem
from interview_api.modules.memory.proposal_models import (
    MemoryProposal, MemoryProposalEvaluation, MemoryProposalObservation, PROPOSAL_TYPES,
)
from interview_api.modules.memory.repository import UserMemoryRepository


MEMORY_TYPE_MAP = {
    "GOAL": "SEMANTIC", "PREFERENCE": "PREFERENCE", "EXPERIENCE": "EPISODIC",
    "SKILL_OBSERVATION": "EPISODIC", "PREPARATION_FOCUS": "SEMANTIC",
}


def validate_observation_content(content: str) -> str:
    """Do not promote a one-session inference into an absolute skill fact."""
    text = content.strip()
    if not text or len(text) > 4000:
        raise AppError("INVALID_MEMORY_PROPOSAL", "记忆建议内容不能为空或超过长度限制", 422)
    prohibited = (
        r"(?:用户|候选人|你).{0,12}(?:精通|不会|不具备|不擅长|天生|总是|从不)",
        r"(?:精通|不会|不具备|不擅长).{0,12}(?:技能|技术|能力|RAG|Redis|Kafka)",
        r"\b(?:candidate|user|you)\s+(?:is\s+(?:an?\s+)?expert|cannot|can\s+never|always|never|lacks|does\s+not\s+have)\b",
    )
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in prohibited):
        raise AppError("ABSOLUTE_SKILL_MEMORY_REJECTED", "面试表现不能成为绝对长期技能事实，请保留本场/本轮观察限定", 422)
    return text


async def create_report_proposals(db, user_id, report, proposals, observations=()):
    """Participates in the caller's report transaction; never commits."""
    if report.user_id != user_id:
        raise AppError("MEMORY_PROPOSAL_OWNER_MISMATCH", "记忆建议报告不属于当前用户", 404)
    created = []
    for artifact in proposals:
        item = artifact.model_dump() if hasattr(artifact, "model_dump") else dict(artifact)
        proposal_type = item.get("proposal_type", item.get("type"))
        if proposal_type not in PROPOSAL_TYPES:
            raise AppError("INVALID_MEMORY_PROPOSAL", "未知记忆建议类型", 422)
        content = validate_observation_content(item["content"])
        ids = sorted(set(item.get("supporting_evaluation_ids") or []))
        if not ids:
            raise AppError("MEMORY_SUPPORT_REQUIRED", "记忆建议必须引用真实评价", 422)
        evaluations = (await db.scalars(select(InterviewEvaluation).where(
            InterviewEvaluation.id.in_(ids), InterviewEvaluation.session_id == report.session_id,
        ))).all()
        if {e.id for e in evaluations} != set(ids):
            raise AppError("UNKNOWN_EVALUATION", "记忆建议引用了未知或其他面试的评价", 422)
        # Resolve only matching, same-report observations; never trust model observation IDs.
        matched = [o for o in observations if o.report_id == report.id and o.session_id == report.session_id
                   and set(getattr(o, "supporting_evaluation_ids", getattr(o, "evaluation_ids", [])) or []) & set(ids)]
        observation_ids = sorted({o.id for o in matched})
        fingerprint = digest(dict(proposal_type=proposal_type, content=content, evaluation_ids=ids))
        proposal = await db.scalar(select(MemoryProposal).where(
            MemoryProposal.report_id == report.id, MemoryProposal.content_fingerprint == fingerprint,
        ))
        if proposal:
            created.append(proposal)
            continue
        proposal = MemoryProposal(user_id=user_id, session_id=report.session_id, report_id=report.id,
            proposal_type=proposal_type, original_content=content, content_fingerprint=fingerprint,
            status="PENDING", confidence="REPEATED_EVALUATIONS" if len(ids) >= 2 else "SINGLE_EVALUATION",
            supporting_evaluation_ids=ids, observation_ids=observation_ids)
        db.add(proposal)
        await db.flush()
        db.add_all([MemoryProposalEvaluation(proposal_id=proposal.id, evaluation_id=id_) for id_ in ids])
        db.add_all([MemoryProposalObservation(proposal_id=proposal.id, observation_id=id_) for id_ in observation_ids])
        created.append(proposal)
    await db.flush()
    return created


class MemoryProposalService:
    """All decisions flush only. API/caller commits proposal + Memory + event together."""

    def __init__(self, db):
        self.db = db
        self.repository = UserMemoryRepository(db)

    async def list(self, user_id, report_id=None, *, limit=100, offset=0):
        from sqlalchemy import func
        conditions = [MemoryProposal.user_id == user_id]
        if report_id is not None:
            conditions.append(MemoryProposal.report_id == report_id)
        total = await self.db.scalar(select(func.count()).select_from(MemoryProposal).where(*conditions))
        rows = (await self.db.scalars(select(MemoryProposal).where(*conditions)
            .order_by(MemoryProposal.created_at.desc(), MemoryProposal.id.desc()).offset(offset).limit(limit))).all()
        return dict(items=[await self.serialize(row) for row in rows], total=total)

    async def decide(self, user_id, proposal_id, decision, *, content=None, reason=None):
        if decision not in {"APPROVED", "CORRECTED", "REJECTED"}:
            raise AppError("INVALID_PROPOSAL_DECISION", "无效记忆建议操作", 422)
        proposal = await self.db.scalar(select(MemoryProposal).where(
            MemoryProposal.id == proposal_id, MemoryProposal.user_id == user_id,
        ).with_for_update())
        if not proposal:
            raise AppError("MEMORY_PROPOSAL_NOT_FOUND", "记忆建议不存在", 404)
        content = content.strip() if isinstance(content, str) else content
        reason = reason.strip() if isinstance(reason, str) else reason
        if decision == "CORRECTED" and (not content or not reason):
            raise AppError("CORRECTION_REASON_REQUIRED", "修正必须提供内容和原因", 422)
        if proposal.status != "PENDING":
            if proposal.status == decision and (decision != "CORRECTED" or
                (proposal.corrected_content == content and proposal.confirmation_reason == reason)):
                return await self.serialize(proposal)
            raise AppError("MEMORY_PROPOSAL_CONFLICT", "建议已处理，不能覆盖先前用户决定", 409)
        now = datetime.now(timezone.utc)
        if decision in {"APPROVED", "CORRECTED"}:
            approved_content = content if decision == "CORRECTED" else proposal.original_content
            # No inferred absolute skill fact, including through the correction endpoint.
            approved_content = validate_observation_content(approved_content)
            metadata = dict(proposal_id=proposal.id, report_id=proposal.report_id, session_id=proposal.session_id,
                proposal_type=proposal.proposal_type, supporting_evaluation_ids=proposal.supporting_evaluation_ids,
                observation_ids=proposal.observation_ids, original_content=proposal.original_content,
                corrected_content=content if decision == "CORRECTED" else None,
                user_confirmed=True, confirmed_by=user_id, confirmed_at=now.isoformat(),
                confidence_semantics=proposal.confidence, is_stable_skill_fact=False,
                legacy_confidence_semantics="Fixed compatibility value, not ability score or truth probability")
            memory = await self.repository.create_memory_item(UserMemoryItem(user_id=user_id,
                memory_type=MEMORY_TYPE_MAP[proposal.proposal_type], scope="INTERVIEW",
                key=f"interview_memory_proposal:{proposal.id}", content=approved_content,
                summary=approved_content[:500], metadata_json=metadata, confidence=0.8, importance=0.6,
                source_type="MEMORY_PROPOSAL", source_id=proposal.id, status="ACTIVE", visibility="PRIVATE"))
            await self.repository.create_memory_event(UserMemoryEvent(user_id=user_id, memory_item_id=memory.id,
                event_type="CREATED", actor_type="USER", actor_id=user_id,
                before_json=dict(proposal_id=proposal.id, original_content=proposal.original_content, status="PENDING"),
                after_json=dict(memory_item_id=memory.id, content=approved_content, proposal_status=decision,
                    user_confirmed=True, provenance=metadata), reason=reason or "explicit_proposal_approval"))
            proposal.memory_item_id = memory.id
        proposal.status = decision
        proposal.confirmed_by = user_id
        proposal.confirmation_reason = reason
        proposal.confirmed_at = now
        proposal.changed_by = "user"
        if decision == "CORRECTED":
            proposal.corrected_content = content
            proposal.corrected_at = now
        await self.db.flush()
        return await self.serialize(proposal)

    async def serialize(self, proposal):
        rows = (await self.db.execute(select(InterviewEvaluation, InterviewSessionQuestion)
            .join(InterviewSessionQuestion, InterviewSessionQuestion.id == InterviewEvaluation.session_question_id)
            .join(MemoryProposalEvaluation, MemoryProposalEvaluation.evaluation_id == InterviewEvaluation.id)
            .where(MemoryProposalEvaluation.proposal_id == proposal.id).order_by(InterviewEvaluation.id))).all()
        return dict(id=proposal.id, session_id=proposal.session_id, report_id=proposal.report_id,
            proposal_type=proposal.proposal_type, original_content=proposal.original_content,
            corrected_content=proposal.corrected_content, status=proposal.status, confidence=proposal.confidence,
            confidence_semantics="支持该建议的有效评价覆盖数量，不表示能力等级或事实真实性概率",
            supporting_evaluation_ids=proposal.supporting_evaluation_ids, observation_ids=proposal.observation_ids,
            supporting_evaluations=[dict(id=e.id, session_question_id=q.id, question=q.question,
                evaluation=e.artifact_json.get("evaluation", ""), scores={key: getattr(e, key) for key in DIMENSIONS}) for e, q in rows],
            confirmed_by=proposal.confirmed_by, confirmation_reason=proposal.confirmation_reason,
            confirmed_at=_iso(proposal.confirmed_at), changed_by=proposal.changed_by,
            corrected_at=_iso(proposal.corrected_at), memory_item_id=proposal.memory_item_id,
            created_at=_iso(proposal.created_at))


def _iso(value):
    return value.isoformat() if value else None
