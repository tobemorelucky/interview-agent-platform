"""Short, atomic profile transactions; all external work happens elsewhere."""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from interview_api.core.exceptions import AppError
from interview_api.modules.users.models import User
from interview_api.modules.resume.models import Resume
from interview_api.modules.candidate_profile.evidence import digest, resolve_and_validate
from interview_api.modules.candidate_profile.models import (
    CandidateProfile, CandidateProfileVersion, ResumeEvidence, UserConfirmedEvidence,
    CandidateFact, CandidateFactEvidence, ProjectExperience, WorkExperience,
    EducationExperience, UserSkill, ProfileCorrection, ProfileGeneration,
)
from interview_api.modules.candidate_profile.schemas import CandidateProfileDelta
from interview_api.modules.candidate_profile.context import apply_section_map


CATEGORIES = ("skills", "projects", "education", "work_experience", "internship_experience", "strengths")
STABLE_CATEGORIES = CATEGORIES[:-1]


def conflict(code, message):
    return AppError(code, message, 409)


def iter_facts(snapshot, *, stable_only=False):
    # Default retains legacy path lookup compatibility for frozen consumers.
    # New CandidateFact writes MUST explicitly use stable_only=True.
    if snapshot.get("candidate_summary") and not stable_only:
        yield "candidate_summary", snapshot["candidate_summary"]
    for category in STABLE_CATEGORIES if stable_only else CATEGORIES:
        for index, item in enumerate(snapshot.get(category, [])):
            yield f"{category}.{index}", item


async def evidence_catalog(db, user_id: int, *, keys=None, resume_id=None):
    query = select(ResumeEvidence).join(Resume, Resume.id == ResumeEvidence.resume_id).where(Resume.user_id == user_id)
    if keys is not None:
        query = query.where(ResumeEvidence.evidence_key.in_(keys))
    if resume_id is not None:
        query = query.where(ResumeEvidence.resume_id == resume_id)
    result = {}
    for row in (await db.scalars(query)).all():
        result[row.evidence_key] = dict(id=row.id, key=row.evidence_key, kind="RESUME", section=row.section,
                                       excerpt=row.excerpt, resume_id=row.resume_id, page=row.page,
                                       start_offset=row.start_offset, end_offset=row.end_offset,
                                       content_hash=row.content_hash)
    query = select(UserConfirmedEvidence).where(UserConfirmedEvidence.user_id == user_id)
    if keys is not None:
        query = query.where(UserConfirmedEvidence.evidence_key.in_(keys))
    # User evidence is included only when explicitly requested; new resume cannot use arbitrary past confirmations.
    if keys is not None:
        for row in (await db.scalars(query)).all():
            result[row.evidence_key] = dict(id=row.id, key=row.evidence_key, kind="USER_CONFIRMED",
                                           section="user_confirmed", excerpt=row.statement,
                                           reason=row.reason, created_at=row.created_at.isoformat())
    return result


class CandidateProfileService:
    def __init__(self, db):
        self.db = db

    async def current(self, user_id):
        profile = await self.db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        return await self.db.get(CandidateProfileVersion, profile.current_version_id) if profile and profile.current_version_id else None

    async def lock_profile(self, user_id):
        # User row serializes first-root creation too; never held during LLM calls.
        await self.db.scalar(select(User).where(User.id == user_id).with_for_update())
        profile = await self.db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id).with_for_update())
        if not profile:
            profile = CandidateProfile(user_id=user_id)
            self.db.add(profile)
            await self.db.flush()
        return profile

    async def persist(self, *, user_id, resume_id, snapshot, generation_key, base_version_id,
                      producer, provider=None, model=None, prompt_version=None, agent_run_id=None,
                      evidence_sections=None):
        """Caller commits once: version + normalized facts + provenance + pointer."""
        profile = await self.lock_profile(user_id)
        existing = await self.db.scalar(select(CandidateProfileVersion).where(CandidateProfileVersion.generation_key == generation_key))
        if existing:
            if existing.candidate_profile_id != profile.id:
                raise conflict("PROFILE_GENERATION_CONFLICT", "画像生成标识冲突")
            return existing  # Never reset pointer after later user corrections.
        if profile.current_version_id != base_version_id:
            raise conflict("PROFILE_VERSION_CONFLICT", "画像已变化，请基于最新版本重试")
        resume = await self.db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id).with_for_update())
        if not resume:
            raise AppError("RESUME_NOT_FOUND", "简历不存在", 404)
        delta = CandidateProfileDelta.model_validate(snapshot)
        base = await self.db.get(CandidateProfileVersion, base_version_id) if base_version_id else None
        # No synthesis producer is introduced here. Preserve unchanged legacy
        # display content on correction, but never accept new derived content
        # through the stable-fact write path or recreate its CandidateFact rows.
        for field in ("candidate_summary", "strengths"):
            if snapshot.get(field) and (base is None or snapshot[field] != base.snapshot_json.get(field)):
                raise conflict("DERIVED_PROFILE_ANALYSIS_NOT_ALLOWED", "摘要和优势不属于稳定事实抽取或修正入口")
        keys = {key for _, fact in iter_facts(snapshot, stable_only=True) for key in fact["evidence_ids"]}
        catalog = await evidence_catalog(self.db, user_id, resume_id=resume_id)
        catalog.update(await evidence_catalog(self.db, user_id, keys=keys))
        # A call's deterministic groups are frozen with its audit, not written
        # back to immutable ResumeEvidence. Corrections inherit that boundary.
        inherited_sections = await self._version_sections(base_version_id)
        catalog = apply_section_map(catalog, {**inherited_sections, **(evidence_sections or {})})
        # User confirmations describe entire corrected facts; ability state still needs actual source signals.
        resolve_and_validate(delta, catalog, stable_only=True)
        version = CandidateProfileVersion(
            candidate_profile_id=profile.id, version_number=base.version_number + 1 if base else 1,
            base_version_id=base_version_id, source_resume_id=resume_id, generation_key=generation_key,
            summary=delta.candidate_summary.statement if delta.candidate_summary else None,
            unknowns=[{"topic": topic, "message": "当前简历材料中未提供相关证据"} for topic in delta.unknowns],
            snapshot_json=deepcopy(snapshot), producer=producer, provider=provider, model=model,
            prompt_version=prompt_version, agent_run_id=agent_run_id,
        )
        self.db.add(version)
        await self.db.flush()
        for path, item in iter_facts(snapshot, stable_only=True):
            supports = [catalog[key] for key in dict.fromkeys(item["evidence_ids"])]
            fact = CandidateFact(version_id=version.id, field_path=path, statement=item["statement"],
                                 provenance="USER_CONFIRMED" if any(e["kind"] == "USER_CONFIRMED" for e in supports) else "RESUME_EXTRACTED")
            self.db.add(fact)
            await self.db.flush()
            for support in supports:
                self.db.add(CandidateFactEvidence(fact_id=fact.id,
                    resume_evidence_id=support["id"] if support["kind"] == "RESUME" else None,
                    user_evidence_id=support["id"] if support["kind"] == "USER_CONFIRMED" else None))
            category = path.split(".")[0]
            common = dict(version_id=version.id, fact_id=fact.id)
            if category == "skills":
                self.db.add(UserSkill(**common, name=item["label"], evidence_state=item["evidence_state"]))
            elif category == "projects":
                self.db.add(ProjectExperience(**common, name=item["label"], description=item["statement"]))
            elif category == "education":
                self.db.add(EducationExperience(**common, institution=item["label"], description=item["statement"]))
            elif category in ("work_experience", "internship_experience"):
                self.db.add(WorkExperience(**common, employer=item["label"], description=item["statement"], experience_type="INTERNSHIP" if category == "internship_experience" else "WORK"))
        await self.db.flush()  # All normalized facts/FKs must succeed BEFORE pointer update.
        profile.current_version_id = version.id
        await self.db.flush()
        return version

    async def _version_sections(self, version_id):
        from interview_api.modules.experience.models import ExperienceAgentRun
        visited = set()
        while version_id and version_id not in visited:
            visited.add(version_id)
            version = await self.db.get(CandidateProfileVersion, version_id)
            if not version:
                break
            if version.agent_run_id:
                run = await self.db.get(ExperienceAgentRun, version.agent_run_id)
                context = (run.output_json or {}).get("profile_context") if run else None
                if context:
                    return context["section_map"]
            version_id = version.base_version_id
        return {}

    async def correct(self, user_id, request):
        profile = await self.lock_profile(user_id)
        if profile.current_version_id != request.base_version_id:
            raise conflict("PROFILE_VERSION_CONFLICT", "画像已变化，请刷新后修正")
        base = await self.db.get(CandidateProfileVersion, request.base_version_id)
        if base is None:
            raise AppError("PROFILE_NOT_FOUND", "画像尚未生成", 404)
        snapshot = deepcopy(base.snapshot_json)
        parts = request.field_path.split(".")
        try:
            if len(parts) == 3 and parts[0] in STABLE_CATEGORIES and parts[1].isdigit() and parts[2] in ("label", "statement"):
                item = snapshot[parts[0]][int(parts[1])]
                field = parts[2]
            else:
                raise ValueError()
            old = item[field]
        except (IndexError, KeyError, TypeError, ValueError):
            raise AppError("INVALID_CORRECTION_PATH", "只能修正已存在事实的 label / statement", 422)
        item[field] = request.new_value
        key = "uc_" + digest([profile.id, base.id, request.field_path, request.new_value, request.reason, uuid4().hex])
        confirmed = UserConfirmedEvidence(candidate_profile_id=profile.id, user_id=user_id, evidence_key=key,
                                          statement=request.new_value, reason=request.reason)
        self.db.add(confirmed)
        await self.db.flush()
        # A name correction confirms the name only, not every unchanged detail.
        # Preserve original support for unchanged fields alongside the new self-report.
        item["evidence_ids"] = list(dict.fromkeys([*item["evidence_ids"], key]))
        # Corrected skill is a user mention, never a self-certified performance grade.
        if parts[0] == "skills":
            item["evidence_state"] = "MENTIONED"
        version = await self.persist(user_id=user_id, resume_id=base.source_resume_id, snapshot=snapshot,
            generation_key=digest(["correction", key]), base_version_id=base.id, producer="USER_CORRECTION")
        self.db.add(ProfileCorrection(base_version_id=base.id, new_version_id=version.id,
            field_path=request.field_path, old_value=old, new_value=request.new_value,
            changed_by="user", user_id=user_id, reason=request.reason))
        await self.db.flush()
        return await self.serialize(user_id, version)

    async def serialize(self, user_id, version=None):
        version = version or await self.current(user_id)
        if not version:
            return None
        profile = await self.db.get(CandidateProfile, version.candidate_profile_id)
        if profile.user_id != user_id:
            raise AppError("PROFILE_NOT_FOUND", "画像不存在", 404)
        keys = {key for _, fact in iter_facts(version.snapshot_json) for key in fact["evidence_ids"]}
        evidence = await evidence_catalog(self.db, user_id, keys=keys)
        normalized = {}
        for name, table in (("projects", ProjectExperience), ("work_experience", WorkExperience), ("education", EducationExperience), ("skills", UserSkill)):
            rows = (await self.db.scalars(select(table).where(table.version_id == version.id).order_by(table.id))).all()
            normalized[name] = [{column.name: getattr(row, column.name) for column in table.__table__.columns} for row in rows]
        provenance = {row.field_path: row.provenance for row in (await self.db.scalars(select(CandidateFact).where(CandidateFact.version_id == version.id))).all()}
        return dict(id=version.id, profile_id=profile.id, version_number=version.version_number,
                    base_version_id=version.base_version_id, source_resume_id=version.source_resume_id,
                    summary=version.summary, unknowns=version.unknowns, snapshot=version.snapshot_json,
                    producer=version.producer, provider=version.provider, model=version.model,
                    prompt_version=version.prompt_version, created_at=version.created_at.isoformat(),
                    evidence=list(evidence.values()), normalized=normalized, provenance=provenance)

    async def generations(self, user_id):
        rows = (await self.db.scalars(select(ProfileGeneration).join(Resume, Resume.id == ProfileGeneration.resume_id)
            .where(Resume.user_id == user_id).order_by(ProfileGeneration.id.desc()).limit(20))).all()
        return [dict(id=row.id, resume_id=row.resume_id, status=row.status, error=row.error,
                     version_id=row.version_id, updated_at=row.updated_at.isoformat()) for row in rows]
