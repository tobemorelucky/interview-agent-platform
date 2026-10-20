"""DEMO/REPLAY-only tests. No real public source acquisition or model calls."""
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.core.config import settings
from interview_api.infrastructure.models.provider import CompatibleModelProvider, ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter, EXTRACT_ROUTE, QUALITY_ROUTE
from interview_api.modules.public_career.manual_import import (
    ManualDocument, ManualImportService, ManualImportError, manual_manifest, require_live_router,
)
from interview_api.modules.public_career.models import (
    Source, SourceSnapshot, PublicDataRefreshTask, ClaimEvidence, JobRequirement, JobDescriptionVersion, Company, Job,
)
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.research.evidence import EvidenceCatalog
from interview_api.modules.public_career.tool_models import ToolInvocation
from interview_api.modules.experience.models import InterviewExperience, InterviewQuestion, ExperienceSourceItem, ExperienceAgentRun
from test_m2b_research_agent import job_response, interview_responses, router


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Manual importer tests must not access real network")
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def document(*, source_type="community", document_type="job_jd", suffix="", url=None):
    company = "DEMO Manual " + uuid4().hex[:12]
    body = f"{company} Backend Engineer. Python experience required. " + "DEMO interview content. " * 25 + suffix
    if document_type == "interview_experience":
        body += "请解释缓存穿透的处理方案？数据库索引为什么使用B加树？"
    return ManualDocument(url or f"https://manual.example/{company.replace(' ', '-')}", source_type,
        document_type, company, "Backend Engineer", body, "DEMO_fixture.txt", datetime.now(timezone.utc))


@pytest.mark.parametrize("url", ["file:///local.txt", "http://localhost/x", "https://user:pass@public.example/x", "https://public.example/x?api_key=secret"])
def test_manual_url_validation_without_dns_or_network(url):
    with pytest.raises(ValueError):
        document(url=url)


@pytest.mark.parametrize("body", ["", "  ", "test\x00invalid"])
def test_empty_or_binary_body_rejected(body):
    with pytest.raises(ManualImportError, match="INVALID_MANUAL_BODY"):
        ManualDocument("https://example.org/jd", "official", "job_jd", "DEMO", "DEMO Role", body, "fixture.txt", datetime.now(timezone.utc))


def test_file_utf8_body_preserved_and_local_save_time(tmp_path):
    file = tmp_path / "DEMO_fixture.txt"
    file.write_bytes("DEMO 岗位正文\nPython\n".encode("utf-8"))
    saved = ManualDocument.from_file(file=file, source_url="https://example.org/jd?utm_source=x#part",
        source_type="official", document_type="job_jd", company="DEMO", job_title="DEMO Role")
    assert saved.content == "DEMO 岗位正文\nPython\n" and saved.canonical_url == "https://example.org/jd"
    assert saved.acquired_at.timestamp() == pytest.approx(file.stat().st_mtime)


def test_cli_arguments_help_and_live_model_guard():
    path = Path(__file__).resolve().parents[1] / "scripts/import_public_career_document.py"
    spec = importlib.util.spec_from_file_location("manual_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.parser().parse_args(["--source-url", "https://example.org/jd", "--source-type", "official",
        "--document-type", "job_jd", "--file", "fixture.txt", "--company", "DEMO", "--job-title", "DEMO Role"])
    assert args.document_type == "job_jd" and args.file == "fixture.txt"
    with pytest.raises(ManualImportError, match="MANUAL_CLI_REQUIRES_LIVE_MODEL"):
        require_live_router(router([]))
    provider = CompatibleModelProvider(base_url="https://fixture.invalid", api_key="fixture-only", model="fixture-only", timeout_seconds=10, max_output_tokens=10)
    require_live_router(ModelRouter({EXTRACT_ROUTE: provider, QUALITY_ROUTE: provider}))


async def scenario(database_url, action):
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await action(factory)
    finally:
        await engine.dispose()


def test_prepare_atomic_dedupe_concurrency_no_fetch_no_final_entities(database_url):
    async def action(factory):
        service = ManualImportService(factory)
        doc = document()
        a, b = await asyncio.gather(service.prepare(doc), service.prepare(doc))
        assert a["refresh_task_id"] == b["refresh_task_id"] and a["source_snapshot_id"] == b["source_snapshot_id"]
        assert sorted([a["reused"], b["reused"]]) == [False, True]
        async with factory() as db:
            task = await db.get(PublicDataRefreshTask, a["refresh_task_id"])
            snapshot = await db.get(SourceSnapshot, a["source_snapshot_id"])
            source = await db.get(Source, a["source_id"])
            assert source.source_type == "community" and source.canonical_url == doc.canonical_url
            assert snapshot.content_text == doc.content and snapshot.content_hash == doc.content_hash
            assert snapshot.created_by_tool_invocation_id is None
            assert snapshot.metadata_json["fetched_at_semantics"] == "LOCAL_ACQUIRED_AT_NOT_NETWORK_FETCH"
            assert task.status == "READY_FOR_EXTRACTION" and manual_manifest(task)["acquisition_mode"] == "MANUAL_SOURCE_IMPORT"
            selected = await PublicDataResearchAgent(factory)._snapshots(db, task.id)
            assert [s.id for s in selected] == [snapshot.id]
            assert await db.scalar(select(func.count(ToolInvocation.id)).where(ToolInvocation.refresh_task_id == task.id)) == 0
            assert await db.scalar(select(Company.id).where(Company.canonical_name == doc.company)) is None
    asyncio.run(scenario(database_url, action))


def test_manual_identity_conflict_rolls_back_and_preserves_metadata(database_url):
    async def action(factory):
        service = ManualImportService(factory)
        doc = document()
        first = await service.prepare(doc)
        conflicting = ManualDocument(doc.source_url, "official", doc.document_type, doc.company, doc.job_title,
            doc.content, doc.file_name, doc.acquired_at)
        with pytest.raises(ManualImportError, match="MANUAL_IMPORT_IDENTITY_CONFLICT"):
            await service.prepare(conflicting)
        async with factory() as db:
            source = await db.get(Source, first["source_id"])
            task = await db.get(PublicDataRefreshTask, first["refresh_task_id"])
            assert source.source_type == manual_manifest(task)["source_type"] == "community"
            assert await db.scalar(select(func.count(PublicDataRefreshTask.id)).where(PublicDataRefreshTask.idempotency_key == doc.key)) == 1
    asyncio.run(scenario(database_url, action))


@pytest.mark.parametrize("source_type,trusted,expected", [
    ("official", True, "APPROVED"), ("official", False, "WAITING_REVIEW"),
    ("community", True, "WAITING_REVIEW"), ("community", False, "WAITING_REVIEW"),
])
def test_manual_jd_real_pipeline_gate_claims_and_completed_idempotency(database_url, monkeypatch, source_type, trusted, expected):
    async def action(factory):
        doc = document(source_type=source_type)
        monkeypatch.setattr(settings, "public_research_official_domains", {doc.company.lower(): ["manual.example"]} if trusted else {})
        service = ManualImportService(factory)
        prepared = await service.prepare(doc)
        async with factory() as db:
            snapshot = await db.get(SourceSnapshot, prepared["source_snapshot_id"])
            ids = [next(iter(EvidenceCatalog([snapshot]).spans))]
        model = router([job_response(doc.company, ids), {"suitable": True, "risk_flags": []}])
        result = await service.execute(prepared, router=model)
        assert result["status"] == "COMPLETED" and result["publication_status"] == expected
        again = await service.execute(await service.prepare(doc), router=model)
        assert again["status"] == "COMPLETED" and again["reused"]
        assert model.primary(EXTRACT_ROUTE).calls == 2
        async with factory() as db:
            jd = await db.get(JobDescriptionVersion, result["jd_version_id"])
            assert jd.raw_text == doc.content and jd.metadata_json["manual_source"]["source_type"] == source_type
            requirements = (await db.scalars(select(JobRequirement).where(JobRequirement.job_description_version_id == jd.id))).all()
            assert requirements and all(r.claim_id for r in requirements)
            for req in requirements:
                evidence = (await db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == req.claim_id))).all()
                assert evidence and all(e.source_snapshot_id == snapshot.id for e in evidence)
                assert all(snapshot.content_text[e.source_location_json["start_char"]:e.source_location_json["end_char"]] == e.excerpt for e in evidence)
            assert await db.scalar(select(func.count(ExperienceAgentRun.id)).where(ExperienceAgentRun.refresh_task_id == prepared["refresh_task_id"])) == 1
    asyncio.run(scenario(database_url, action))


@pytest.mark.parametrize("damage", ["identity", "evidence", "snapshot"])
def test_failed_manual_research_no_final_write_no_automatic_retry(database_url, damage):
    async def action(factory):
        doc = document()
        service = ManualImportService(factory)
        prepared = await service.prepare(doc)
        async with factory() as db:
            snapshot = await db.get(SourceSnapshot, prepared["source_snapshot_id"])
            ids = [next(iter(EvidenceCatalog([snapshot]).spans))]
            if damage == "snapshot":
                snapshot.content_text = "tampered body"
                await db.commit()
        output = job_response("DEMO wrong company" if damage == "identity" else doc.company,
            ["ev_" + "0" * 24] if damage == "evidence" else ids)
        model = router([output, {"suitable": True, "risk_flags": []}])
        result = await service.execute(prepared, router=model)
        assert result["status"] == "FAILED"
        calls = model.primary(EXTRACT_ROUTE).calls
        if damage != "snapshot":
            retry = await service.execute(await service.prepare(doc), router=model)
            assert retry["status"] == "FAILED" and model.primary(EXTRACT_ROUTE).calls == calls
        else:
            assert calls == 0
        async with factory() as db:
            assert await db.scalar(select(Company.id).where(Company.canonical_name == doc.company)) is None
            assert await db.scalar(select(func.count(ToolInvocation.id)).where(ToolInvocation.refresh_task_id == prepared["refresh_task_id"])) == 0
    asyncio.run(scenario(database_url, action))


def test_manual_interview_uses_existing_pipeline_and_never_publishes(database_url):
    async def action(factory):
        doc = document(source_type="official", document_type="interview_experience")
        service = ManualImportService(factory)
        prepared = await service.prepare(doc)
        async with factory() as db:
            snapshot = await db.get(SourceSnapshot, prepared["source_snapshot_id"])
            ids = [next(iter(EvidenceCatalog([snapshot]).spans))]
        model = router(interview_responses(doc.company, ids))
        result = await service.execute(prepared, router=model)
        assert result["status"] == "COMPLETED" and result["review_status"] in {"WAITING_REVIEW", "NEEDS_MANUAL_CHECK"}
        async with factory() as db:
            exp = await db.get(InterviewExperience, result["experience_id"])
            item = await db.get(ExperienceSourceItem, exp.source_item_id)
            assert exp.claim_id and exp.source_snapshot_id == snapshot.id
            assert exp.reliability_json["manual_source"]["acquisition_mode"] == "MANUAL_SOURCE_IMPORT"
            assert item.fetch_status == "MANUALLY_IMPORTED" and item.fetched_at is None
            questions = (await db.scalars(select(InterviewQuestion).where(InterviewQuestion.experience_id == exp.id))).all()
            assert questions and all(q.claim_id and q.review_status != "APPROVED" and q.index_status == "NOT_INDEXED" for q in questions)
    asyncio.run(scenario(database_url, action))


def test_changed_body_new_snapshot_and_task_mirrors_allowed(database_url):
    async def action(factory):
        service = ManualImportService(factory)
        doc = document()
        a = await service.prepare(doc)
        changed = ManualDocument(doc.source_url, doc.source_type, doc.document_type, doc.company, doc.job_title,
            doc.content + " New actual content.", doc.file_name, doc.acquired_at)
        mirror = ManualDocument(doc.source_url + "/mirror", doc.source_type, doc.document_type, doc.company, doc.job_title,
            doc.content, doc.file_name, doc.acquired_at)
        b, c = await service.prepare(changed), await service.prepare(mirror)
        assert a["source_id"] == b["source_id"] != c["source_id"]
        assert len({a["source_snapshot_id"], b["source_snapshot_id"], c["source_snapshot_id"]}) == 3
        assert a["content_sha256"] == c["content_sha256"]
    asyncio.run(scenario(database_url, action))
