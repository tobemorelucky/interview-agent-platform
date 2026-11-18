"""PublicDataResearchAgent: evidence-backed extraction and atomic public writes."""

from datetime import datetime, timezone
from hashlib import sha256
import re

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from interview_api.core.config import settings
from interview_api.infrastructure.models.router import EXTRACT_ROUTE, QUALITY_ROUTE, ModelRouter, build_model_router
from interview_api.modules.experience.models import ExperienceCollectionTask, ExperienceSourceItem, InterviewExperience, InterviewQuestion, ExperienceAgentRun
from interview_api.modules.experience.agents.repository import ExperienceAgentRepository
from interview_api.modules.public_career.dedupe import group_independent_content
from interview_api.modules.public_career.fingerprints import compute_content_fingerprint, normalize_text
from interview_api.modules.public_career.models import (
    PublicDataRefreshTask, Source, SourceSnapshot, Company, CompanyAlias, Job,
    Claim, ClaimEvidence, JobDescriptionVersion, JobRequirement,
)
from interview_api.modules.public_career.refresh_service import PublicDataRefreshService, RefreshTaskStatus
from interview_api.modules.public_career.tool_models import ToolInvocation
from interview_api.modules.public_career.research.evidence import EvidenceCatalog, EvidenceValidationError
from interview_api.modules.public_career.research.fragments import InterviewSourceFragments
from interview_api.modules.public_career.research.schemas import JobJDExtraction, ResearchQuality
from interview_api.modules.public_career.research.audit import ResearchModelCalls, create_research_run, finish_research_run
from interview_api.modules.public_career.research.prompts import ManualSourcePromptRegistry, WebSourcePromptRegistry
from interview_api.modules.public_career.research.interview import InterviewPipelineAdapter
from interview_api.modules.public_career.research.policy import job_publication_gate
from interview_api.modules.public_career.service import link_interview_experience
from interview_api.modules.public_career.manual_import import (
    manual_manifest, resolve_manual_snapshot, validate_expected_identity, ACQUISITION_MODE,
)
from interview_api.modules.public_career.web_collection import resolve_web_snapshot, web_manifest


def job_title_base(title):
    """Normalized title without a trailing campus/round suffix."""
    stripped = re.sub(r"[（(][^（()）]*[)）]\s*$", "", str(title or "")).strip()
    return normalize_text(stripped)


class PublicDataResearchAgent:
    role = "PublicDataResearchAgent"

    def __init__(self, factory: async_sessionmaker[AsyncSession], router: ModelRouter | None = None):
        self.factory = factory
        self.router = router

    async def run(self, refresh_task_id: int, *, force: bool = False) -> dict:
        run_id = None
        async with self.factory() as db:
            task = await db.scalar(select(PublicDataRefreshTask).where(
                PublicDataRefreshTask.id == refresh_task_id
            ).with_for_update())
            if task is None:
                raise LookupError("Public refresh task not found")
            if task.target_type not in {"JOB_JD", "INTERVIEW_DATA", "EXPERIENCE_COLLECTION_TASK"}:
                raise ValueError("Unsupported public research target")
            target = "INTERVIEW_DATA" if task.target_type == "EXPERIENCE_COLLECTION_TASK" else task.target_type
            rerun = force and target == "INTERVIEW_DATA" and task.status in {"COMPLETED", "FAILED"}
            # Explicit maintainer recovery after a failed manual JD attempt;
            # successful generations are still immutable/idempotent. No crawl.
            rerun = rerun or (force and target == "JOB_JD" and task.status == "FAILED"
                             and (manual_manifest(task) is not None or web_manifest(task) is not None))
            if task.status != "READY_FOR_EXTRACTION" and not rerun:
                raise ValueError("Research requires READY_FOR_EXTRACTION (or explicit interview rerun)")
            controller = PublicDataRefreshService(db, None)  # transitions do not use network tools
            await controller.transition(task, RefreshTaskStatus.EXTRACTING)
            await db.commit()
            try:
                run_id = await create_research_run(self.factory, task.id)
                manual = manual_manifest(task)
                web = web_manifest(task)
                snapshots = await self._snapshots(db, task.id)
                sources = {s.id: s for s in (await db.scalars(select(Source).where(
                    Source.id.in_({snapshot.source_id for snapshot in snapshots})
                ))).all()}
                catalog = EvidenceCatalog(snapshots, max_chars=settings.public_research_max_evidence_chars)
                if not catalog.spans:
                    raise EvidenceValidationError("Task has no fetched body evidence")
                calls = ResearchModelCalls(self.factory, self.router or build_model_router(), run_id,
                                           prompts=ManualSourcePromptRegistry() if manual else WebSourcePromptRegistry() if web else None)
                if target == "JOB_JD":
                    extraction_input = {"evidence": catalog.model_input()}
                    if manual:
                        # Scope hint only. Both exact source grounding and
                        # expected-identity validation below remain mandatory.
                        extraction_input["requested_identity"] = {
                            "company_name": manual["company"], "job_title": manual["job_title"],
                            "requires_literal_evidence": True,
                        }
                    extracted = await calls.call("job_jd", EXTRACT_ROUTE, extraction_input, JobJDExtraction)
                    quality = await calls.call("quality", QUALITY_ROUTE, {
                        "extraction": extracted.model_dump(), "evidence": catalog.model_input(),
                    }, ResearchQuality)
                    await controller.transition(task, RefreshTaskStatus.VALIDATING)
                    await db.commit()
                    catalog.resolve(extracted.identity_evidence_ids)
                    catalog.resolve(extracted.jd_evidence_ids)
                    self._validate_identity(catalog, extracted.identity_evidence_ids, extracted.company_name, extracted.job_title)
                    validate_expected_identity(manual, extracted.company_name, extracted.job_title)
                    for requirement in extracted.requirements:
                        catalog.resolve(requirement.evidence_ids)
                        if web and not catalog.supports_literal(requirement.evidence_ids, requirement.statement):
                            raise EvidenceValidationError("Web requirement must be extractive from cited source text")
                        if not normalize_text(requirement.statement):
                            raise EvidenceValidationError("Empty normalized requirement statement")
                    for attribute in extracted.attributes:
                        if not catalog.supports_literal(attribute.evidence_ids, attribute.statement):
                            raise EvidenceValidationError("Job metadata must be literal and cite exact evidence")
                    await controller.transition(task, RefreshTaskStatus.PUBLISHING)
                    await db.commit()
                    output = await self._publish_job(db, extracted, quality, catalog, sources, snapshots, manual=manual, web=web)
                else:
                    prepared = []
                    by_id = {snapshot.id: snapshot for snapshot in snapshots}
                    for group in group_independent_content(snapshots).groups:
                        members = [by_id[sid] for sid in group.snapshot_ids]
                        first = members[0]
                        group_catalog = (InterviewSourceFragments if web else EvidenceCatalog)(
                            members, max_chars=settings.public_research_max_evidence_chars)
                        extracted, state = await InterviewPipelineAdapter(calls, require_literal_questions=bool(web), requested_identity=(
                            {"company": manual["company"], "position": manual["job_title"],
                             "requires_literal_evidence": True} if manual else None)).run(
                            group_catalog, first.content_text or "",
                            sources[first.source_id].canonical_url, first.title,
                        )
                        if web and not state.get('source_anchors'):
                            for question in extracted.questions:
                                if not group_catalog.supports_literal(question.evidence_ids, question.question):
                                    raise EvidenceValidationError("Web question must be extractive from cited source text")
                        prepared.append((extracted, state, group_catalog, members))
                    await controller.transition(task, RefreshTaskStatus.VALIDATING)
                    await db.commit()
                    for extracted, state, group_catalog, members in prepared:
                        if extracted.company or extracted.position:
                            self._validate_identity(group_catalog, extracted.identity_evidence_ids, extracted.company, extracted.position)
                        if extracted.is_interview_experience:
                            validate_expected_identity(manual, extracted.company, extracted.position)
                    await controller.transition(task, RefreshTaskStatus.PUBLISHING)
                    await db.commit()
                    entries = [await self._publish_interview(db, task, extracted, state, group_catalog, sources, members, force=force)
                               for extracted, state, group_catalog, members in prepared]
                    output = {**entries[0], "experiences": entries, "collection_metrics": self._metrics(snapshots)}
                await controller.transition(task, RefreshTaskStatus.COMPLETED)
                output = {"refresh_task_id": refresh_task_id, "agent_run_id": run_id, "status": "COMPLETED", **output}
                if manual:
                    output["acquisition_mode"] = ACQUISITION_MODE
                    output["manual_source"] = manual
                audit_run = await db.get(ExperienceAgentRun, run_id)
                audit_run.status, audit_run.output_json = "SUCCEEDED", output
                audit_run.finished_at = datetime.now(timezone.utc)
                await db.commit()
                return output
            except Exception as exc:
                await db.rollback()
                task = await db.get(PublicDataRefreshTask, refresh_task_id)
                error = getattr(exc, "code", type(exc).__name__) + ": " + str(exc)[:1000]
                if task.status != "COMPLETED":
                    await controller.transition(task, RefreshTaskStatus.FAILED, error_message=error)
                    await db.commit()
                if run_id:
                    await finish_research_run(self.factory, run_id, status="FAILED", error=error)
                return {"refresh_task_id": refresh_task_id, "agent_run_id": run_id, "status": task.status, "error": error}

    async def _snapshots(self, db, task_id):
        task = await db.get(PublicDataRefreshTask, task_id)
        web = await resolve_web_snapshot(db, task) if task else None
        if web is not None:
            return web
        manual = await resolve_manual_snapshot(db, task) if task else None
        if manual is not None:
            return manual
        calls = (await db.scalars(select(ToolInvocation).where(
            ToolInvocation.refresh_task_id == task_id,
            ToolInvocation.tool_name == "public.fetch",
            ToolInvocation.status == "SUCCEEDED",
        ))).all()
        ids = {ref["source_snapshot_id"] for call in calls
               for ref in call.response_json.get("source_snapshot_refs", [])}
        return list((await db.scalars(select(SourceSnapshot).where(
            SourceSnapshot.id.in_(ids)
        ).order_by(SourceSnapshot.id))).all())

    @staticmethod
    def _validate_identity(catalog, ids, *names):
        catalog.validate_identity(ids, *names)

    @staticmethod
    def _metrics(snapshots):
        groups = group_independent_content(snapshots)
        return {"independent_content_count": groups.independent_content_count,
                "occurrence_count": groups.occurrence_count, "multi_source": groups.multi_source,
                "source_count": groups.independent_content_count}

    async def _claim(self, db, statement, ids, catalog, *, context, status):
        spans = catalog.resolve(ids)
        claim_key = sha256(f"{context}:{normalize_text(statement)}".encode()).hexdigest()
        claim_id = await db.scalar(insert(Claim).values(
            claim_key=claim_key, category=context.split(":")[0], statement=statement, status=status,
        ).on_conflict_do_nothing(constraint="uq_claims_claim_key").returning(Claim.id))
        if claim_id is None:
            claim_id = await db.scalar(select(Claim.id).where(Claim.claim_key == claim_key))
        for span in spans:
            await db.execute(insert(ClaimEvidence).values(
                claim_id=claim_id, source_snapshot_id=span.snapshot_id,
                evidence_key=span.evidence_id, excerpt=span.excerpt,
                source_location_json=span.location, support_type="DIRECT",
            ).on_conflict_do_nothing(constraint="uq_claim_evidence_claim_key"))
        return claim_id

    async def _publish_job(self, db, extracted, quality, catalog, sources, snapshots, *, manual=None, web=None):
        company_ids = await self._company_candidates(db, extracted.company_name)
        if len(company_ids) > 1:
            raise EvidenceValidationError("Ambiguous public company identity; automatic association refused")
        existing = await db.get(Company, next(iter(company_ids))) if company_ids else None
        evidence_ids = list(extracted.identity_evidence_ids) + list(extracted.jd_evidence_ids)
        for req in extracted.requirements:
            evidence_ids += req.evidence_ids
        for attr in extracted.attributes:
            evidence_ids += attr.evidence_ids
        spans = catalog.resolve(evidence_ids)
        used_sources = {span.source_id for span in spans}
        decision = job_publication_gate(
            company_name=existing.canonical_name if existing else extracted.company_name,
            urls=[sources[sid].canonical_url for sid in sorted(used_sources)],
            evidence_complete=True, suitable=quality.suitable, risk_flags=quality.risk_flags,
            trusted_domains=settings.public_research_official_domains,
            official_domain=existing.official_domain if existing else None,
        )
        if ((manual and manual["source_type"] == "community") or (web and web["source_type"] == "community")) and decision.status == "APPROVED":
            from interview_api.modules.public_career.research.policy import PublicationDecision
            decision = PublicationDecision("WAITING_REVIEW", False, ("manual_community_source_requires_review",))
        entity_status = "ACTIVE" if decision.status == "APPROVED" else "WAITING_REVIEW"
        company_id = existing.id if existing else await db.scalar(insert(Company).values(
            canonical_name=extracted.company_name, normalized_name=normalize_text(extracted.company_name),
            status=entity_status,
        ).on_conflict_do_nothing(constraint="uq_companies_normalized_name").returning(Company.id))
        if company_id is None:
            company_id = await db.scalar(select(Company.id).where(Company.normalized_name == normalize_text(extracted.company_name)))
        job_id = await db.scalar(insert(Job).values(
            company_id=company_id, canonical_title=extracted.job_title,
            normalized_title=normalize_text(extracted.job_title), status=entity_status,
        ).on_conflict_do_nothing(constraint="uq_jobs_company_title").returning(Job.id))
        if job_id is None:
            job_id = await db.scalar(select(Job.id).where(Job.company_id == company_id, Job.normalized_title == normalize_text(extracted.job_title)))
        # Serialize version numbering for this job across concurrent refresh tasks.
        await db.scalar(select(Job.id).where(Job.id == job_id).with_for_update())
        jd_spans = sorted(catalog.resolve(extracted.jd_evidence_ids), key=lambda s: (s.snapshot_id, s.start))
        # JD versions fingerprint fetched bodies, not model-selected excerpts.
        # Deterministic content ordering removes URL/order/mirror differences.
        jd_snapshot_ids = {span.snapshot_id for span in jd_spans}
        jd_bodies = {compute_content_fingerprint(s.content_text or ""): s.content_text or ""
                     for s in snapshots if s.id in jd_snapshot_ids}
        raw_text = "\n".join(jd_bodies[key] for key in sorted(jd_bodies))
        fingerprint = compute_content_fingerprint(raw_text)
        jd = await db.scalar(select(JobDescriptionVersion).where(
            JobDescriptionVersion.job_id == job_id,
            JobDescriptionVersion.content_fingerprint == fingerprint,
        ))
        context = f"JOB_JD:{company_id}:{job_id}"
        identity_claim = await self._claim(db, f"{extracted.company_name}: {extracted.job_title}",
                                         extracted.identity_evidence_ids, catalog,
                                         context=context, status=entity_status)
        if jd is None:
            attribute_claims = [dict(field=attr.field, claim_id=await self._claim(db, attr.statement,
                attr.evidence_ids, catalog, context=context, status=entity_status)) for attr in extracted.attributes]
            version = (await db.scalar(select(func.max(JobDescriptionVersion.version_number)).where(JobDescriptionVersion.job_id == job_id)) or 0) + 1
            jd = JobDescriptionVersion(
                job_id=job_id, version_number=version, raw_text=raw_text,
                source_snapshot_id=jd_spans[0].snapshot_id,
                publication_status=decision.status,
                structured_data_json=extracted.model_dump(),
                metadata_json={"identity_claim_id": identity_claim, "quality_gate": decision.reasons,
                               "quality_assessment": quality.model_dump(),
                               **({"manual_source": manual} if manual else {}),
                               **({"web_source": web} if web else {}),
                               "attribute_claims": attribute_claims,
                               "credibility": "source_support_signals", **self._metrics(snapshots)},
            )
            db.add(jd)
            await db.flush()
            for req in extracted.requirements:
                claim_id = await self._claim(db, req.statement, req.evidence_ids, catalog,
                                             context=context, status=entity_status)
                db.add(JobRequirement(job_description_version_id=jd.id,
                                      statement=req.statement, normalized_value=normalize_text(req.statement)[:255],
                                      requirement_type=req.requirement_type, importance=req.importance,
                                      claim_id=claim_id))
        return {"target": "JOB_JD", "company_id": company_id, "job_id": job_id,
                **({"manual_publication_decision": decision.status, "manual_source_type": manual["source_type"]} if manual else {}),
                "jd_version_id": jd.id, "publication_status": jd.publication_status,
                **self._metrics(snapshots)}

    async def _company_candidates(self, db, company_name):
        normalized = normalize_text(company_name or "")
        company_ids = set((await db.scalars(select(Company.id).where(Company.normalized_name == normalized))).all())
        company_ids.update((await db.scalars(select(CompanyAlias.company_id).where(CompanyAlias.normalized_alias == normalized))).all())
        return company_ids

    async def _match_company_job(self, db, company_name, title):
        company_ids = await self._company_candidates(db, company_name)
        if len(company_ids) != 1:
            return None, None
        company_id = next(iter(company_ids))
        normalized = normalize_text(title or "")
        if not normalized:
            return company_id, None
        job_id = await db.scalar(select(Job.id).where(Job.company_id == company_id,
            Job.normalized_title == normalized))
        if job_id is not None:
            return company_id, job_id
        # A campus suffix ("（2027届秋招）") names the same posted role. Accept it
        # only when exactly one job in this company has that base title; anything
        # ambiguous stays unlinked instead of guessing.
        rows = (await db.execute(select(Job.id, Job.canonical_title).where(
            Job.company_id == company_id))).all()
        base = job_title_base(title)
        candidates = [row[0] for row in rows if job_title_base(row[1]) == base]
        if not candidates:
            # Spacing between Latin and Chinese ("Harness 研发" vs "Harness研发")
            # is typography, not a different role.
            compact = re.sub(r"\s+", "", base)
            candidates = [row[0] for row in rows if re.sub(r"\s+", "", job_title_base(row[1])) == compact]
        return company_id, candidates[0] if len(candidates) == 1 else None

    async def _publish_interview(self, db, task, extracted, state, catalog, sources, snapshots, *, force=False):
        gate = state["quality_gate_result"]
        if web_manifest(task):
            # Collected community experiences are always candidates for human
            # review, regardless of provider authority or model confidence.
            if gate['review_status'] != 'REJECTED':
                gate = {**gate, "review_status": "WAITING_REVIEW"}
        first = snapshots[0]
        source = sources[first.source_id]
        legacy = await db.scalar(select(ExperienceCollectionTask).where(ExperienceCollectionTask.public_refresh_task_id == task.id))
        if legacy is None:
            legacy = ExperienceCollectionTask(public_refresh_task_id=task.id)
            db.add(legacy)
            await db.flush()
        item = await db.scalar(select(ExperienceSourceItem).where(
            ExperienceSourceItem.task_id == legacy.id, ExperienceSourceItem.normalized_url_hash == source.url_hash,
        ))
        if item is None:
            item = ExperienceSourceItem(task_id=legacy.id, source_url=source.canonical_url,
                                        normalized_url_hash=source.url_hash, title=first.title)
            db.add(item)
        item.raw_text = first.content_text
        manual = manual_manifest(task)
        item.fetch_status = "MANUALLY_IMPORTED" if manual else "FETCHED"
        item.content_hash = first.content_hash
        item.fetched_at = None if manual else first.fetched_at
        await db.flush()
        repo = ExperienceAgentRepository(db)
        metrics = self._metrics(snapshots)
        audit_run = await db.scalar(select(ExperienceAgentRun).where(
            ExperienceAgentRun.refresh_task_id == task.id, ExperienceAgentRun.status == "RUNNING",
        ))
        if audit_run is not None and audit_run.source_item_id is None:
            audit_run.source_item_id, audit_run.task_id = item.id, legacy.id
        if not extracted.is_interview_experience:
            item.extract_status = "NOT_EXPERIENCE"
            await repo.refresh_task_extraction_counts(legacy.id)
            return {"target": "INTERVIEW_DATA", "review_status": "REJECTED",
                    "experience_id": None, "source_item_id": item.id, **metrics}
        summary_claim = await self._claim(db, extracted.experience_summary, extracted.evidence_ids, catalog,
                                          context=f"INTERVIEW_DATA:{compute_content_fingerprint(first.content_text or '')}", status=gate["review_status"])
        existing_query = select(InterviewExperience).where(
            InterviewExperience.source_snapshot_id == first.id,
            InterviewExperience.task_id == legacy.id,
        )
        if not force:
            existing_query = existing_query.where(InterviewExperience.claim_id == summary_claim)
        existing = await db.scalar(existing_query.order_by(InterviewExperience.id.desc()).limit(1))
        if existing is not None:
            if not force:
                return {"target": "INTERVIEW_DATA", "experience_id": existing.id,
                        "review_status": existing.review_status, "source_item_id": item.id, **metrics}
            approved_question = await db.scalar(select(InterviewQuestion.id).where(
                InterviewQuestion.experience_id == existing.id, InterviewQuestion.review_status == "APPROVED",
            ).limit(1))
            if existing.review_status == "APPROVED" or approved_question is not None:
                raise ValueError("Explicit rerun must not replace approved interview data")
            await db.execute(delete(InterviewExperience).where(InterviewExperience.id == existing.id))
        experience = await repo.create_experience_draft(
            source=item, company=extracted.company, position=extracted.position,
            round_name=extracted.round_name, summary=extracted.experience_summary,
            job_direction=(state.get("routing_result") or {}).get("overall_job_direction"),
            extraction_confidence=extracted.extraction_confidence,
            extraction_output_json={**extracted.model_dump(), **({'source_anchors': state['source_anchors']} if state.get('source_anchors') else {})}, routing_json=state.get("routing_result"),
            reliability_json={**(state.get("reliability_result") or {}), **metrics,
                              **({"manual_source": manual} if manual else {}),
                              **({"web_source": web_manifest(task)} if web_manifest(task) else {}),
                              "credibility": "source_support_signals"},
            quality_gate_json=gate, reliability_score=gate["reliability_score"],
            quality_flags_json=(state.get("reliability_result") or {}).get("quality_flags", []),
            review_status=gate["review_status"],
        )
        experience.source_snapshot_id = first.id
        experience.claim_id = summary_claim
        company_id, job_id = await self._match_company_job(db, extracted.company, extracted.position)
        await link_interview_experience(db, experience, company_id=company_id, job_id=job_id)
        questions = await repo.create_question_drafts(
            experience=experience, questions=state["extraction_result"]["questions"],
            routing_result=state.get("routing_result"), review_status=gate["review_status"],
            reliability_score=gate["reliability_score"],
        )
        for index, (model_question, question) in enumerate(zip(extracted.questions, questions, strict=True)):
            anchor = (state.get('source_anchors') or {}).get('questions', [])[index] if state.get('source_anchors') else None
            question.claim_id = await self._claim(db, anchor['raw_excerpt'] if anchor else question.question, model_question.evidence_ids, catalog,
                                                  context=f"INTERVIEW_DATA:{experience.content_fingerprint}", status=gate["review_status"])
            # Legacy evidence is supplied by the exact resolver, never model text.
            question.evidence = catalog.resolve(model_question.evidence_ids)[0].excerpt[:500]
            supported_ids = {span.snapshot_id for span in catalog.resolve(model_question.evidence_ids)}
            question.routing_json = {**(question.routing_json or {}), **self._metrics(
                [snapshot for snapshot in snapshots if snapshot.id in supported_ids]),
                **({'source_anchor': anchor} if anchor else {})}
        item.extract_status = "EXTRACTED"
        await repo.refresh_task_extraction_counts(legacy.id)
        return {"target": "INTERVIEW_DATA", "experience_id": experience.id,
                "source_item_id": item.id, "review_status": gate["review_status"], **metrics}
