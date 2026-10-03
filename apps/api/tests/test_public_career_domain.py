from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.modules.experience.models import (
    InterviewExperience,
    InterviewQuestion,
)
from interview_api.modules.public_career.fingerprints import (
    compute_content_fingerprint,
    compute_question_fingerprint,
    normalize_text,
)
from interview_api.modules.public_career.backfill import backfill_public_career_data
from interview_api.modules.public_career.models import (
    Claim,
    ClaimEvidence,
    Company,
    CompanyAlias,
    Job,
    JobDescriptionVersion,
    JobRequirement,
    SourceSnapshot,
)
from interview_api.modules.public_career.service import (
    PublicCareerLinkError,
    build_source,
    validate_company_job_alignment,
)


def test_normalization_and_fingerprints_are_deterministic() -> None:
    assert normalize_text("  ByteDance\r\n  AI  ") == "bytedance ai"
    assert compute_content_fingerprint("RAG\n rerank") == compute_content_fingerprint(
        "ＲＡＧ   rerank"
    )
    assert compute_question_fingerprint("Why RAG?") == compute_question_fingerprint(
        "  WHY   RAG?  "
    )


def test_company_alias_job_and_jd_values_are_normalized() -> None:
    company = Company(canonical_name="  ByteDance  ")
    alias = CompanyAlias(company=company, alias=" 字节跳动 ")
    job = Job(company=company, canonical_title=" AI 应用工程师 ")
    jd = JobDescriptionVersion(job=job, version_number=1, raw_text="熟悉 RAG")

    assert company.normalized_name == "bytedance"
    assert alias.normalized_alias == "字节跳动"
    assert job.company is company
    assert job.normalized_title == "ai 应用工程师"
    assert jd.content_fingerprint == compute_content_fingerprint("熟悉 RAG")


def test_requirement_traces_claim_evidence_snapshot() -> None:
    source = build_source(
        url="https://example.com/jobs/1?utm_source=test", source_type="JOB_PAGE"
    )
    snapshot = SourceSnapshot(
        source=source,
        content_hash="a" * 64,
        fetched_at=datetime.now(timezone.utc),
        content_text="Python 3.11 required",
    )
    claim = Claim(claim_key="python-311", statement="Requires Python 3.11")
    evidence = ClaimEvidence(
        claim=claim,
        source_snapshot=snapshot,
        evidence_key="requirements:1",
        excerpt="Python 3.11 required",
    )
    requirement = JobRequirement(statement="Python 3.11", claim=claim)

    assert requirement.claim is claim
    assert claim.evidence == [evidence]
    assert claim.evidence[0].source_snapshot is snapshot


def test_legacy_strings_partial_links_and_alignment_validation() -> None:
    experience = InterviewExperience(
        company="字节跳动", position="后端工程师", company_id=10
    )
    question = InterviewQuestion(
        question="解释 Redis 持久化", company="字节跳动", position="后端工程师"
    )
    matching_job = Job(id=20, company_id=10, canonical_title="后端工程师")
    other_job = Job(id=21, company_id=11, canonical_title="后端工程师")

    assert experience.company == "字节跳动"
    assert experience.position == "后端工程师"
    assert experience.company_id == 10
    assert experience.job_id is None
    assert question.company == "字节跳动"
    assert question.position == "后端工程师"
    assert question.company_id is None
    assert question.job_id is None
    validate_company_job_alignment(company_id=10, job=matching_job)
    with pytest.raises(PublicCareerLinkError):
        validate_company_job_alignment(company_id=10, job=other_job)


def test_public_models_do_not_contain_user_id() -> None:
    public_tables = {
        "companies",
        "company_aliases",
        "jobs",
        "job_description_versions",
        "job_requirements",
        "job_tags",
        "sources",
        "source_snapshots",
        "claims",
        "claim_evidence",
        "public_data_refresh_tasks",
    }
    from interview_api.infrastructure.db.base import Base
    import interview_api.modules.models  # noqa: F401

    for table_name in public_tables:
        assert "user_id" not in Base.metadata.tables[table_name].columns


async def _commit_expect_integrity_error(session_factory, *entities) -> None:
    async with session_factory() as session:
        session.add_all(list(entities))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def _test_database_constraints(database_url: str) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    try:
        async with session_factory() as session:
            company = Company(canonical_name=f"Company {suffix}")
            session.add(company)
            await session.flush()
            company_id = company.id

            alias = CompanyAlias(company_id=company_id, alias="Shared Alias")
            job = Job(company_id=company_id, canonical_title="AI Engineer")
            session.add_all([alias, job])
            await session.commit()
            job_id = job.id

        await _commit_expect_integrity_error(
            session_factory,
            Company(canonical_name=f"  COMPANY {suffix}  "),
        )
        await _commit_expect_integrity_error(
            session_factory,
            CompanyAlias(company_id=company_id, alias=" shared   alias "),
        )
        await _commit_expect_integrity_error(
            session_factory,
            Job(company_id=company_id, canonical_title=" ai engineer "),
        )

        async with session_factory() as session:
            jd = JobDescriptionVersion(
                job_id=job_id, version_number=1, raw_text="Original JD"
            )
            session.add(jd)
            await session.commit()

        await _commit_expect_integrity_error(
            session_factory,
            JobDescriptionVersion(
                job_id=job_id, version_number=1, raw_text="Different JD"
            ),
        )
        await _commit_expect_integrity_error(
            session_factory,
            JobDescriptionVersion(
                job_id=job_id, version_number=2, raw_text=" original   jd "
            ),
        )

        source_one = build_source(
            url=f"https://example.com/jobs/{suffix}?utm_source=one",
            source_type="JOB_PAGE",
        )
        source_duplicate = build_source(
            url=f"https://example.com/jobs/{suffix}?utm_campaign=two",
            source_type="JOB_PAGE",
        )
        async with session_factory() as session:
            session.add(source_one)
            await session.commit()
            source_one_id = source_one.id

        await _commit_expect_integrity_error(session_factory, source_duplicate)

        content_hash = hashlib.sha256(suffix.encode()).hexdigest()
        async with session_factory() as session:
            snapshot = SourceSnapshot(
                source_id=source_one_id,
                content_hash=content_hash,
                fetched_at=datetime.now(timezone.utc),
            )
            session.add(snapshot)
            await session.commit()

        await _commit_expect_integrity_error(
            session_factory,
            SourceSnapshot(
                source_id=source_one_id,
                content_hash=content_hash,
                fetched_at=datetime.now(timezone.utc),
            ),
        )

        source_two = build_source(
            url=f"https://mirror.example.com/jobs/{suffix}",
            source_type="JOB_PAGE",
        )
        async with session_factory() as session:
            session.add(source_two)
            await session.flush()
            session.add(
                SourceSnapshot(
                    source_id=source_two.id,
                    content_hash=content_hash,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def test_postgresql_uniqueness_constraints(database_url: str) -> None:
    asyncio.run(_test_database_constraints(database_url))


async def _test_deterministic_backfill(database_url: str) -> None:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    try:
        async with session_factory() as session:
            company = Company(canonical_name=f"Unique Company {suffix}")
            other_one = Company(canonical_name=f"Other One {suffix}")
            other_two = Company(canonical_name=f"Other Two {suffix}")
            session.add_all([company, other_one, other_two])
            await session.flush()
            session.add_all(
                [
                    CompanyAlias(company_id=company.id, alias=f"Unique Alias {suffix}"),
                    CompanyAlias(company_id=other_one.id, alias=f"Ambiguous {suffix}"),
                    CompanyAlias(company_id=other_two.id, alias=f"Ambiguous {suffix}"),
                ]
            )
            job = Job(company_id=company.id, canonical_title=f"Backend {suffix}")
            session.add(job)
            await session.flush()
            linked_experience = InterviewExperience(
                company=f" unique alias {suffix} ",
                position=f" BACKEND {suffix} ",
                content_text="Interview body",
            )
            ambiguous_experience = InterviewExperience(
                company=f"ambiguous {suffix}",
                position=f"Backend {suffix}",
            )
            linked_question = InterviewQuestion(
                question="What is RAG?",
                company=f"Unique Alias {suffix}",
                position=f"Backend {suffix}",
            )
            session.add_all(
                [linked_experience, ambiguous_experience, linked_question]
            )
            await session.commit()

            result = await backfill_public_career_data(session)
            await session.commit()

            assert linked_experience.company_id == company.id
            assert linked_experience.job_id == job.id
            assert linked_experience.content_fingerprint == compute_content_fingerprint(
                "Interview body"
            )
            assert linked_question.company_id == company.id
            assert linked_question.job_id == job.id
            assert linked_question.question_fingerprint == compute_question_fingerprint(
                "What is RAG?"
            )
            assert ambiguous_experience.company_id is None
            assert ambiguous_experience.job_id is None
            assert result.ambiguous_company_names >= 1
    finally:
        await engine.dispose()


def test_backfill_uses_only_unique_exact_matches(database_url: str) -> None:
    asyncio.run(_test_deterministic_backfill(database_url))
