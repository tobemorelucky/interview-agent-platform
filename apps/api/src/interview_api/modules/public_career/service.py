"""Application-layer operations for linking public career data."""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.experience.models import (
    InterviewExperience,
    InterviewQuestion,
)
from interview_api.modules.public_career.models import Job
from interview_api.modules.public_career.models import Source
from interview_api.modules.experience.search.url_utils import hash_url, normalize_url


class PublicCareerLinkError(ValueError):
    """Raised when public company/job links are missing or inconsistent."""


def build_source(*, url: str, source_type: str) -> Source:
    """Build a Source with the shared canonical URL and hash rules."""

    canonical_url = normalize_url(url)
    if not canonical_url:
        raise ValueError("a valid absolute source URL is required")
    return Source(
        canonical_url=canonical_url,
        url_hash=hash_url(canonical_url),
        domain=urlsplit(canonical_url).hostname or "",
        source_type=source_type,
    )


def validate_company_job_alignment(
    *, company_id: int | None, job: Job | None
) -> None:
    """Reject a job/company pair that crosses company boundaries."""

    if job is not None and company_id is not None and job.company_id != company_id:
        raise PublicCareerLinkError(
            f"job {job.id} belongs to company {job.company_id}, not {company_id}"
        )


async def _load_and_validate_job(
    session: AsyncSession, *, company_id: int | None, job_id: int | None
) -> Job | None:
    if job_id is None:
        return None
    job = await session.get(Job, job_id)
    if job is None:
        raise PublicCareerLinkError(f"job {job_id} does not exist")
    validate_company_job_alignment(company_id=company_id, job=job)
    return job


async def link_interview_experience(
    session: AsyncSession,
    experience: InterviewExperience,
    *,
    company_id: int | None,
    job_id: int | None,
) -> None:
    job = await _load_and_validate_job(
        session, company_id=company_id, job_id=job_id
    )
    experience.company_id = company_id if company_id is not None else (
        job.company_id if job is not None else None
    )
    experience.job_id = job_id


async def link_interview_question(
    session: AsyncSession,
    question: InterviewQuestion,
    *,
    company_id: int | None,
    job_id: int | None,
) -> None:
    job = await _load_and_validate_job(
        session, company_id=company_id, job_id=job_id
    )
    question.company_id = company_id if company_id is not None else (
        job.company_id if job is not None else None
    )
    question.job_id = job_id
