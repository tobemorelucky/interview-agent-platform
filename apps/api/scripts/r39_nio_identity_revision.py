"""Round 33: NIO identity revision, validated entirely with Replay.

Zero model calls. A fresh rehearsal database is used so the frozen FAILED task of
the real run (#2, in `interview_agent_wb_jd_r31_20261116`) is never touched.

Cases:
  1. reproduce the original failure: manifest label = display name, extraction
     quotes the page literal -> MANUAL_EXTRACTED_IDENTITY_MISMATCH
  2. explicit identity revision with the page literal -> COMPLETED
  3. same revision submitted twice -> same task, never a second copy
  4. wrong company                -> MANUAL_REVISION_COMPANY_CHANGED
  5. another posting's title      -> MANUAL_REVISION_LABEL_NOT_IN_SNAPSHOT
  6. over-short / ambiguous label -> MANUAL_REVISION_LABEL_UNRELATED
  7. unchanged label              -> MANUAL_REVISION_LABEL_UNCHANGED
  8. predecessor already completed-> MANUAL_REVISION_PREDECESSOR_NOT_FAILED
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "scripts"))

import r34_replay_import as r31  # noqa: E402  (reuses the proven replay fixture builder)

import interview_api.modules.models  # noqa: F401,E402
from interview_api.core.config import settings  # noqa: E402
from interview_api.modules.public_career.manual_import import (  # noqa: E402
    ManualDocument, ManualImportError, ManualImportService,
)
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

MANIFEST = REPO / "data/r34_target_jd/source_manifest.json"
REPORT = REPO / "data/r39_nio_revision/replay_report.json"
KEY = "NIO_AIA_025"
SOURCE_TYPE = "official"  # all three round-31 targets are official detail pages
SANDBOX = "interview_agent_wb_jd_replay3_r33_20261116"


def factory_for(name: str):
    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{name}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def task_state(factory, task_id: int) -> dict:
    from interview_api.modules.public_career.models import PublicDataRefreshTask
    async with factory() as db:
        task = await db.get(PublicDataRefreshTask, task_id)
        manifest = json.loads(task.reason or "{}")
        return {"id": task.id, "status": task.status, "error": (task.error_message or "")[:160],
                "job_title": manifest.get("job_title"),
                "revision": manifest.get("identity_revision")}


async def counts(factory) -> dict:
    from interview_api.modules.public_career.models import (
        Claim, Company, Job, JobDescriptionVersion, JobRequirement, PublicDataRefreshTask, SourceSnapshot,
    )
    from sqlalchemy import func
    async with factory() as db:
        async def count(model):
            return await db.scalar(select(func.count()).select_from(model))
        return {"tasks": await count(PublicDataRefreshTask), "snapshots": await count(SourceSnapshot),
                "companies": await count(Company), "jobs": await count(Job),
                "jd_versions": await count(JobDescriptionVersion),
                "requirements": await count(JobRequirement), "claims": await count(Claim)}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=SANDBOX)
    parser.add_argument("--confirm", default=SANDBOX)
    args = parser.parse_args()
    if args.database != args.confirm:
        raise SystemExit("REFUSED: --database and --confirm must match")

    settings.public_research_official_domains = {}
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = next(item for item in manifest["records"] if item["jd_key"] == KEY)
    text_path = REPO / record["text_path"]
    display_label = record["job_title"]                       # what was frozen in round 31
    literal_label = record["identity_in_text"] and record.get("page_literal") or None
    # The page literal is the first projection line, minus the " - 蔚来校招" suffix.
    first_line = text_path.read_text(encoding="utf-8").splitlines()[0]
    literal_label = first_line.split(" - ")[0].strip()

    engine, factory = factory_for(args.database)
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(),
                    "model_http_calls": 0, "database": args.database,
                    "labels": {"display_frozen_in_round31": display_label, "page_literal": literal_label},
                    "cases": {}, "counts_before": await counts(factory)}
    try:
        # ── 1. reproduce the original failure ───────────────────────────────
        document = ManualDocument.from_file(
            file=text_path, source_url=record["source_url"], source_type=SOURCE_TYPE,
            document_type="job_jd", company=record["company"], job_title=display_label)
        prepared = await ManualImportService(factory).prepare(document)
        old_task = prepared["refresh_task_id"]
        async with factory() as db:
            from interview_api.modules.public_career.models import SourceSnapshot
            snapshot = await db.scalar(select(SourceSnapshot).where(
                SourceSnapshot.id == prepared["source_snapshot_id"]))
            body = snapshot.content_text
        catalog = r31.catalog_for(snapshot)
        target = {"company": record["company"], "job_title": literal_label, "sections": record["sections"]}
        extraction, quality = r31.build_fixture(catalog, body, target)
        report["cases"]["fixture_identity"] = {"company": extraction["company_name"],
                                               "job_title": extraction["job_title"]}
        result = await r31.run_agent(factory, old_task, extraction, quality)
        report["cases"]["original_failure"] = {"task": old_task, "result": result,
                                               "state": await task_state(factory, old_task)}

        # ── 2. explicit revision with the page literal ──────────────────────
        service = ManualImportService(factory)
        revision_document = ManualDocument.from_file(
            file=text_path, source_url=record["source_url"], source_type=SOURCE_TYPE,
            document_type="job_jd", company=record["company"], job_title=literal_label)
        revision = await service.prepare_identity_revision(
            revision_document, predecessor_task_id=old_task,
            reason="R33: 官网页面字面标题为「校招-车端 AI Agent 与应用生态系统架构工程师」，"
                   "R31 冻结的是去前缀展示名，需显式修订后重跑，不改旧任务与正文。",
            authorized_by="user:R33-approval")
        report["cases"]["revision_created"] = revision

        # ── 3. idempotent resubmission ──────────────────────────────────────
        again = await service.prepare_identity_revision(
            revision_document, predecessor_task_id=old_task, reason="duplicate submit probe",
            authorized_by="user:R33-approval")
        report["cases"]["revision_resubmitted"] = again
        report["cases"]["revision_idempotent"] = (
            again["refresh_task_id"] == revision["refresh_task_id"] and again["reused"] is True)

        # ── 4-7. refusals ──────────────────────────────────────────────────
        def refuse(code_expected: str, *, company=None, title=None, predecessor=None):
            doc = ManualDocument.from_file(
                file=text_path, source_url=record["source_url"], source_type=SOURCE_TYPE,
                document_type="job_jd", company=company or record["company"],
                job_title=title or literal_label)
            return service.prepare_identity_revision(
                doc, predecessor_task_id=predecessor or old_task, reason="negative probe",
                authorized_by="user:R33-approval"), code_expected

        for name, kwargs, expected in (
            ("wrong_company", {"company": "沐瞳科技"}, "MANUAL_REVISION_COMPANY_CHANGED"),
            ("other_posting_title", {"title": "Agent开发工程师（平台）"},
             "MANUAL_REVISION_LABEL_NOT_IN_SNAPSHOT"),
            ("over_short_label", {"title": "AI Agent 与应用生态系统架构工程师"},
             "MANUAL_REVISION_LABEL_UNRELATED"),
            ("unchanged_label", {"title": display_label}, "MANUAL_REVISION_LABEL_UNCHANGED"),
        ):
            coroutine, _ = refuse(expected, **kwargs)
            try:
                await coroutine
                report["cases"][name] = {"refused": False, "error": None}
            except ManualImportError as exc:
                report["cases"][name] = {"refused": True, "error": exc.code,
                                         "expected": expected, "match": exc.code == expected}

        # predecessor that already completed must not be revisable
        try:
            await service.prepare_identity_revision(
                ManualDocument.from_file(file=text_path, source_url=record["source_url"],
                                         source_type=SOURCE_TYPE, document_type="job_jd",
                                         company=record["company"],
                                         job_title=literal_label + " "),
                predecessor_task_id=revision["refresh_task_id"], reason="negative probe",
                authorized_by="user:R33-approval")
            report["cases"]["predecessor_not_failed"] = {"refused": False, "error": None}
        except ManualImportError as exc:
            report["cases"]["predecessor_not_failed"] = {
                "refused": True, "error": exc.code,
                "expected": "MANUAL_REVISION_PREDECESSOR_NOT_FAILED",
                "match": exc.code == "MANUAL_REVISION_PREDECESSOR_NOT_FAILED"}

        # ── 8. run the revision through the real agent ──────────────────────
        result = await r31.run_agent(factory, revision["refresh_task_id"], extraction, quality)
        report["cases"]["revision_run"] = {"result": result,
                                           "state": await task_state(factory, revision["refresh_task_id"])}
        report["cases"]["original_task_untouched"] = await task_state(factory, old_task)
        report["counts_after"] = await counts(factory)
    finally:
        await engine.dispose()

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report["cases"].items()
                      if k in ("original_failure", "revision_created", "revision_idempotent",
                               "wrong_company", "other_posting_title", "over_short_label",
                               "unchanged_label", "predecessor_not_failed", "revision_run")},
                     ensure_ascii=False, indent=1, default=str)[:3000])
    print("counts", report["counts_before"], "->", report["counts_after"])
    print("written", REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
