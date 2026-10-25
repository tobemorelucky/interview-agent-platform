"""Rebuild from committed evidence, within the caller's transaction; no model calls."""
from datetime import datetime, timezone
from sqlalchemy import select, text
from interview_api.core.exceptions import AppError
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.interview.models import (InterviewSession, InterviewAnswer, InterviewEvaluation,
    InterviewSessionQuestion, InterviewContextSnapshot, InterviewMessage)
from interview_api.modules.memory.capability_models import CapabilityMemory, CapabilityMemoryVersion
from interview_api.modules.memory.capability_rules import project, memory_plan, RULE_VERSION


def observation_scope(snapshot, question):
    """Do not equate opposite answers to different rubric/source conditions.

    No semantic equivalence is invented: differing anchors remain separate scopes.
    Missing provenance is incomparable rather than evidence of a contradiction.
    """
    decision = (question.candidate_refs_json or {}).get("adaptive_decision", {})
    source = decision.get("question_source_ref") or f"unverified_question:{question.id}"
    rubric = sorted(line.strip() for line in (question.standard_answer or "").splitlines() if line.strip())
    return "scope_" + digest(dict(job_id=snapshot.job_id,
        round=snapshot.context_json.get("interviewer_config", {}).get("round", {}).get("id", "legacy"),
        difficulty=question.difficulty, source=source, rubric=rubric,
        question=" ".join(question.question.split())))[:24]


class CapabilityMemoryService:
    def __init__(self, db, *, clock=None, policy=None):
        self.db, self.clock, self.policy = db, clock or (lambda: datetime.now(timezone.utc)), policy

    async def lock(self, user_id):
        # One lock namespace, no second queue or session-state ledger. Acquired
        # before projection reads, so concurrent completions cannot lose evidence.
        await self.db.execute(text("SELECT pg_advisory_xact_lock(2202, :uid)"), dict(uid=user_id))

    async def observations(self, user_id):
        rows = (await self.db.execute(select(InterviewSession, InterviewEvaluation, InterviewAnswer,
            InterviewSessionQuestion, InterviewContextSnapshot, InterviewMessage)
            .join(InterviewEvaluation, InterviewEvaluation.session_id == InterviewSession.id)
            .join(InterviewAnswer, InterviewAnswer.id == InterviewEvaluation.answer_id)
            .join(InterviewSessionQuestion, InterviewSessionQuestion.id == InterviewEvaluation.session_question_id)
            .join(InterviewContextSnapshot, InterviewContextSnapshot.id == InterviewSession.context_snapshot_id)
            .join(InterviewMessage, InterviewMessage.id == InterviewAnswer.message_id)
            .where(InterviewSession.user_id == user_id, InterviewSession.status == "COMPLETED",
                InterviewContextSnapshot.user_id == user_id, InterviewAnswer.status == "COMPLETED"))).all()
        result = {}
        from interview_api.modules.interview.adaptive import LABELS
        for session, ev, answer, question, snapshot, message in rows:
            # Only Controller-resolved records that actually committed may count.
            ledger = (session.interview_plan_json or {}).get("adaptive_knowledge", {}).get("observations", [])
            valid = next((o for o in ledger if o.get("evaluation_id") == ev.id and
                o.get("answer_id") == answer.id and o.get("question_id") == question.id), None)
            if (not valid or question.session_id != session.id or answer.session_id != session.id
                or message.session_id != session.id or answer.session_question_id != question.id or message.role != "USER"):
                continue
            kid = valid["knowledge_id"]
            topic = kid.rsplit(":", 1)[-1]
            if topic not in LABELS or not any(t["knowledge_id"] == kid for t in snapshot.context_json.get("adaptive_blueprint", {}).get("targets", [])):
                continue
            quotes = valid.get("answer_quotes", [])
            if any(not quote or quote not in message.content for quote in quotes):
                continue
            # A follow-up reveals missing mechanisms even without explicit UI hints.
            # Treat it as assisted conservatively; difficulty and score don't prove independence.
            assisted = question.question_kind != "NEXT" or bool((question.candidate_refs_json or {}).get("interviewer_guidance"))
            scope = observation_scope(snapshot, question)
            entry = dict(evaluation_id=ev.id, session_id=session.id, question_id=question.id,
                topic=topic, scope=scope, observed_at=ev.created_at.isoformat(),
                state=valid["state"], open_gaps=[g["text"] for g in valid.get("open_gaps", [])],
                assisted=assisted, sufficient_quote=bool(quotes),
                disputed=bool(ev.artifact_json.get("factual_or_reasoning_issues")) and valid["state"] == "DEMONSTRATED_IN_SESSION")
            result.setdefault(topic, []).append(entry)
        return result

    async def refresh(self, user_id, *, actor="SYSTEM"):
        await self.lock(user_id)
        observations = await self.observations(user_id)
        roots = {r.topic: r for r in (await self.db.scalars(select(CapabilityMemory).where(CapabilityMemory.user_id == user_id))).all()}
        for topic in sorted(set(roots) | set(observations)):
            root = roots.get(topic)
            if root is None:
                root = CapabilityMemory(user_id=user_id, topic=topic, revision=0, revoked=False,
                    exclusions=[], projection={}, input_fingerprint="")
                self.db.add(root)
                await self.db.flush()
                roots[topic] = root
            value = project(observations.get(topic, []), self.clock(), excluded=root.exclusions,
                revoked=root.revoked, policy=self.policy)
            fingerprint = digest(dict(value=value, revoked=root.revoked, exclusions=sorted(root.exclusions),
                correction_note=root.correction_note, rule=RULE_VERSION))
            if fingerprint == root.input_fingerprint:
                continue
            previous = root.projection.get("state", "UNKNOWN")
            root.revision += 1
            root.projection, root.input_fingerprint = value, fingerprint
            self.db.add(CapabilityMemoryVersion(memory_id=root.id, revision=root.revision, actor=actor,
                previous_state=previous, projection={**value, "revoked": root.revoked,
                    "exclusions": root.exclusions, "correction_note": root.correction_note}, input_fingerprint=fingerprint))
        await self.db.flush()
        return [self.present(r) for r in roots.values()]

    def present(self, root):
        from interview_api.modules.interview.adaptive import LABELS
        # Read-time clock projection does not silently mutate history or snapshots.
        value = project(root.projection.get("observations", []), self.clock(), revoked=root.revoked,
            excluded=root.exclusions, policy=self.policy)
        return dict(id=root.id, topic=root.topic, label=LABELS.get(root.topic, root.topic), revision=root.revision,
            revoked=root.revoked, correction_note=root.correction_note, inferred=True, **value)

    async def list(self, user_id):
        roots = (await self.db.scalars(select(CapabilityMemory).where(CapabilityMemory.user_id == user_id).order_by(CapabilityMemory.topic))).all()
        items = []
        for root in roots:
            item = self.present(root)
            versions = (await self.db.scalars(select(CapabilityMemoryVersion).where(
                CapabilityMemoryVersion.memory_id == root.id).order_by(CapabilityMemoryVersion.revision.desc()).limit(20))).all()
            item["history"] = [dict(revision=v.revision, previous_state=v.previous_state,
                state=v.projection["state"], actor=v.actor, created_at=v.created_at.isoformat()) for v in versions]
            items.append(item)
        return items

    async def correct(self, user_id, memory_id, *, expected_revision, excluded, note, revoke=False):
        await self.lock(user_id)
        root = await self.db.scalar(select(CapabilityMemory).where(CapabilityMemory.id == memory_id, CapabilityMemory.user_id == user_id))
        if not root:
            raise AppError("CAPABILITY_NOT_FOUND", "能力记忆不存在", 404)
        if root.revision != expected_revision:
            raise AppError("CAPABILITY_VERSION_CONFLICT", "记录已更新，请刷新后重试", 409)
        available = {o["evaluation_id"] for o in root.projection.get("observations", [])}
        if set(excluded) - available:
            raise AppError("UNKNOWN_CAPABILITY_OBSERVATION", "只能排除本人的有效表现记录", 422)
        root.exclusions = sorted(set(root.exclusions) | set(excluded))
        root.revoked = root.revoked or revoke  # Sticky tombstone, regeneration cannot resurrect it.
        root.correction_note = note
        await self.db.flush()
        await self.refresh(user_id, actor="USER")
        return self.present(root)

    async def plan(self, user_id, blueprint, *, states=None):
        rows = states if states is not None else await self.refresh(user_id)
        return memory_plan(rows, blueprint["targets"], self.clock(), policy=self.policy)
