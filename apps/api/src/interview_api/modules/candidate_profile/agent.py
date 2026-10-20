"""ResumeProfileAgent uses the existing structured model and audit infrastructure."""
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from interview_api.core.config import settings
from interview_api.infrastructure.models.router import RESUME_PROFILE_ROUTE, build_resume_profile_router
from interview_api.modules.experience.models import ExperienceAgentRun
from interview_api.modules.public_career.research.audit import ResearchModelCalls, finish_research_run
from interview_api.modules.resume.models import Resume, ResumeReport
from interview_api.modules.candidate_profile.evidence import digest, evidence_rows, ensure_resume_evidence, resolve_and_validate, normalize_skill_evidence_states
from interview_api.modules.candidate_profile.models import ProfileGeneration, CandidateProfileVersion, ProfileCorrection
from interview_api.modules.candidate_profile.prompts import CandidatePromptRegistry, PROFILE_PROMPT
from interview_api.modules.candidate_profile.schemas import CandidateEvidenceSelection
from interview_api.modules.candidate_profile.service import CandidateProfileService, evidence_catalog, iter_facts
from interview_api.modules.candidate_profile.context import build_profile_context


class ResumeProfileAgent:
    def __init__(self, factory, router=None):
        self.factory = factory
        self.router = router or build_resume_profile_router()

    async def run(self, resume_id: int, *, regeneration_token: str | None = None):
        generation_id = run_id = None
        prepared = False
        state_diagnostics = []
        context_audit = None
        try:
            async with self.factory() as db:
                resume = await db.scalar(select(Resume).where(Resume.id == resume_id).with_for_update())
                report = await db.scalar(select(ResumeReport).where(ResumeReport.resume_id == resume_id))
                if not resume or resume.status != "COMPLETED" or not report or not resume.raw_text:
                    raise ValueError("RESUME_REPORT_NOT_READY")
                user_id = resume.user_id
                key = digest(["resume_profile", resume_id, resume.raw_text, report.summary_json,
                              PROFILE_PROMPT.version, PROFILE_PROMPT.content_hash, regeneration_token])
                existing = await db.scalar(select(CandidateProfileVersion).where(CandidateProfileVersion.generation_key == key))
                if existing:
                    return dict(status="COMPLETED", version_id=existing.id, reused=True)
                await ensure_resume_evidence(db, resume_id, resume.raw_text)
                catalog = await evidence_catalog(db, user_id, resume_id=resume_id)
                service = CandidateProfileService(db)
                current = await service.current(user_id)
                base_id = current.id if current else None
                current_snapshot = deepcopy(current.snapshot_json) if current else None
                active_keys = {row["evidence_key"] for row in evidence_rows(resume_id, resume.raw_text)}
                catalog = {key: value for key, value in catalog.items() if key in active_keys}
                context = build_profile_context(catalog, report.summary_json, current_snapshot)
                payload, catalog = context.payload, context.catalog
                context_audit = context.audit()
                await db.execute(insert(ProfileGeneration).values(resume_id=resume_id, generation_key=key, status="RUNNING")
                                 .on_conflict_do_nothing(index_elements=["generation_key"]))
                generation = await db.scalar(select(ProfileGeneration).where(ProfileGeneration.generation_key == key))
                generation_id = generation.id
                generation.status, generation.error = "RUNNING", None
                run = ExperienceAgentRun(agent_role="ResumeProfileAgent", graph_name="resume_profile", graph_version="1.0.0",
                    input_hash=key, status="RUNNING", started_at=datetime.now(timezone.utc))
                db.add(run)
                await db.flush()
                run_id = run.id
                generation.agent_run_id = run_id
                await db.commit()
                prepared = True
            calls = ResearchModelCalls(self.factory, self.router, run_id,
                prompts=CandidatePromptRegistry(), timeout_seconds=settings.resume_profile_timeout_seconds)
            selection = await calls.call("resume_profile", RESUME_PROFILE_ROUTE, payload, CandidateEvidenceSelection)
            delta = context.resolve_selection(selection)
            state_diagnostics = normalize_skill_evidence_states(delta, catalog,
                evidence_aliases={key: alias for alias, key in context.alias_to_key.items()})
            resolve_and_validate(delta, catalog, stable_only=True,
                evidence_aliases={key: alias for alias, key in context.alias_to_key.items()})
            snapshot = delta.model_dump()
            async with self.factory() as db:
                service = CandidateProfileService(db)
                if current_snapshot:
                    keys = {key for _, fact in iter_facts(current_snapshot, stable_only=True) for key in fact["evidence_ids"]}
                    previous_evidence = await evidence_catalog(db, user_id, keys=keys)
                    corrections = (await db.scalars(select(ProfileCorrection)
                        .join(CandidateProfileVersion, CandidateProfileVersion.id == ProfileCorrection.new_version_id)
                        .where(CandidateProfileVersion.candidate_profile_id == current.candidate_profile_id))).all()
                    for path, fact in iter_facts(current_snapshot, stable_only=True):
                        if not any(previous_evidence[key]["kind"] == "USER_CONFIRMED" for key in fact["evidence_ids"]):
                            continue
                        category = path.split(".")[0]
                        if category == "candidate_summary":
                            snapshot[category] = deepcopy(fact)
                        else:
                            items = snapshot[category]
                            identity = fact.get("label", fact["statement"])
                            corrected_aliases = {identity}
                            # Exact user-confirmed renames only; never fuzzy matching.
                            for correction in corrections:
                                if correction.field_path.startswith(category + ".") and correction.field_path.endswith(".label") and correction.new_value == identity:
                                    corrected_aliases.add(correction.old_value)
                            items[:] = [item for item in items if item.get("label", item["statement"]) not in corrected_aliases]
                            items.append(deepcopy(fact))
                provider = self.router.resolve(RESUME_PROFILE_ROUTE)
                version = await service.persist(user_id=user_id, resume_id=resume_id, snapshot=snapshot,
                    generation_key=key, base_version_id=base_id,
                    producer="REPLAY_FIXTURE" if provider.provider_name == "replay" else "RESUME_PROFILE_AGENT",
                    provider=provider.provider_name, model=provider.model_name, prompt_version=PROFILE_PROMPT.version, agent_run_id=run_id,
                    evidence_sections=context_audit["section_map"])
                generation = await db.get(ProfileGeneration, generation_id)
                generation.status, generation.version_id, generation.error = "COMPLETED", version.id, None
                run = await db.get(ExperienceAgentRun, run_id)
                run.status, run.output_json = "SUCCEEDED", {"version_id": version.id, "schema": "CandidateEvidenceSelection",
                    "snapshot_schema": "CandidateProfileDelta", "statement_policy": "LONGEST_CITED_EXCERPT_V1",
                    "thinking": getattr(provider, "thinking_type", None),
                    "skill_state_resolution": state_diagnostics, "profile_context": context_audit}
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()
                version_id = version.id
            return dict(status="COMPLETED", version_id=version_id, reused=False)
        except Exception as exc:
            code = getattr(exc, "code", str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
            if generation_id and prepared:
                async with self.factory() as db:
                    generation = await db.get(ProfileGeneration, generation_id)
                    existing = await db.scalar(select(CandidateProfileVersion).where(CandidateProfileVersion.generation_key == generation.generation_key))
                    if existing:
                        generation.status, generation.version_id, generation.error = "COMPLETED", existing.id, None
                    else:
                        generation.status, generation.error = "FAILED", code[:500]
                    await db.commit()
            if run_id and prepared:
                diagnostics = getattr(exc, "diagnostics", None)
                await finish_research_run(self.factory, run_id, status="FAILED", error=code[:500],
                    output={"skill_state_resolution": state_diagnostics, "validation_failure": diagnostics,
                            "profile_context": context_audit,
                            "thinking": getattr(self.router.resolve(RESUME_PROFILE_ROUTE), "thinking_type", None)})
            return dict(status="FAILED", error=code[:500], generation_id=generation_id)


async def generate_profile_best_effort(resume_id):
    """Private profile failure must not erase a successfully saved ResumeReport."""
    import logging
    from interview_api.infrastructure.db.session import async_session_factory
    try:
        result = await ResumeProfileAgent(async_session_factory).run(resume_id)
        if result["status"] != "COMPLETED":
            logging.getLogger(__name__).warning("Profile generation failed resume=%s code=%s", resume_id, result.get("error"))
    except Exception:
        logging.getLogger(__name__).exception("Profile generation failed after saved report resume=%s", resume_id)
