"""Deterministic helpers for adopting the public career data domain."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.experience.models import (
    InterviewExperience,
    InterviewQuestion,
)
from interview_api.modules.public_career.fingerprints import (
    compute_content_fingerprint,
    compute_question_fingerprint,
    normalize_text,
)
from interview_api.modules.public_career.models import Company, CompanyAlias, Job


@dataclass
class BackfillResult:
    experience_fingerprints: int = 0
    question_fingerprints: int = 0
    experience_company_links: int = 0
    experience_job_links: int = 0
    question_company_links: int = 0
    question_job_links: int = 0
    ambiguous_company_names: int = 0
    inconsistent_existing_links: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _add_company_candidate(
    candidates: dict[str, set[int]], normalized_name: str, company_id: int
) -> None:
    if normalized_name:
        candidates.setdefault(normalized_name, set()).add(company_id)


async def backfill_public_career_data(session: AsyncSession) -> BackfillResult:
    """Backfill only deterministic fingerprints and unique exact entity matches.

    Company aliases that resolve to multiple companies are intentionally ignored.
    Job matches require a resolved company and an exact normalized title.
    """

    result = BackfillResult()

    companies = list((await session.scalars(select(Company))).all())
    aliases = list((await session.scalars(select(CompanyAlias))).all())
    jobs = list((await session.scalars(select(Job))).all())

    company_candidates: dict[str, set[int]] = {}
    for company in companies:
        _add_company_candidate(
            company_candidates, company.normalized_name, company.id
        )
    for alias in aliases:
        _add_company_candidate(
            company_candidates, alias.normalized_alias, alias.company_id
        )

    unique_companies = {
        normalized: next(iter(company_ids))
        for normalized, company_ids in company_candidates.items()
        if len(company_ids) == 1
    }
    ambiguous_names = {
        normalized
        for normalized, company_ids in company_candidates.items()
        if len(company_ids) > 1
    }
    job_by_id = {job.id: job for job in jobs}
    jobs_by_company_title = {
        (job.company_id, job.normalized_title): job.id for job in jobs
    }

    experiences = list(
        (await session.scalars(select(InterviewExperience))).all()
    )
    for experience in experiences:
        if experience.content_text and not experience.content_fingerprint:
            experience.content_fingerprint = compute_content_fingerprint(
                experience.content_text
            )
            result.experience_fingerprints += 1

        if experience.job_id is not None:
            linked_job = job_by_id.get(experience.job_id)
            if linked_job is None:
                result.inconsistent_existing_links += 1
                continue
            if experience.company_id is None:
                experience.company_id = linked_job.company_id
                result.experience_company_links += 1
            elif experience.company_id != linked_job.company_id:
                result.inconsistent_existing_links += 1
                continue

        if experience.company_id is None and experience.company:
            normalized_company = normalize_text(experience.company)
            if normalized_company in ambiguous_names:
                result.ambiguous_company_names += 1
            else:
                company_id = unique_companies.get(normalized_company)
                if company_id is not None:
                    experience.company_id = company_id
                    result.experience_company_links += 1

        if (
            experience.job_id is None
            and experience.company_id is not None
            and experience.position
        ):
            job_id = jobs_by_company_title.get(
                (experience.company_id, normalize_text(experience.position))
            )
            if job_id is not None:
                experience.job_id = job_id
                result.experience_job_links += 1

    questions = list((await session.scalars(select(InterviewQuestion))).all())
    for question in questions:
        if question.question and not question.question_fingerprint:
            question.question_fingerprint = compute_question_fingerprint(
                question.question
            )
            result.question_fingerprints += 1

        if question.job_id is not None:
            linked_job = job_by_id.get(question.job_id)
            if linked_job is None:
                result.inconsistent_existing_links += 1
                continue
            if question.company_id is None:
                question.company_id = linked_job.company_id
                result.question_company_links += 1
            elif question.company_id != linked_job.company_id:
                result.inconsistent_existing_links += 1
                continue

        if question.company_id is None and question.company:
            normalized_company = normalize_text(question.company)
            if normalized_company in ambiguous_names:
                result.ambiguous_company_names += 1
            else:
                company_id = unique_companies.get(normalized_company)
                if company_id is not None:
                    question.company_id = company_id
                    result.question_company_links += 1

        if (
            question.job_id is None
            and question.company_id is not None
            and question.position
        ):
            job_id = jobs_by_company_title.get(
                (question.company_id, normalize_text(question.position))
            )
            if job_id is not None:
                question.job_id = job_id
                result.question_job_links += 1

    await session.flush()
    return result

