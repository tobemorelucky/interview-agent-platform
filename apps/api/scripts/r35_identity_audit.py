"""R32: zero-HTTP identity audit for the NIO posting that failed in R31.

Compares the official DOM literals against the manifest expectation, proves the
strict guards with the real functions, and checks task #2 retry safety. No model.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.modules.public_career.fingerprints import normalize_text
from interview_api.modules.public_career.manual_import import (
    ManualImportError, manual_manifest, validate_expected_identity,
)
from interview_api.modules.public_career.models import (
    Claim, ClaimEvidence, Company, Job, JobDescriptionVersion, JobRequirement,
    PublicDataRefreshTask, SourceSnapshot,
)
from interview_api.modules.public_career.research.evidence import EvidenceCatalog, EvidenceValidationError

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "r34_target_jd"

CANDIDATES = {
    "manifest_r31": "车端AI Agent与应用生态系统架构工程师",
    "page_jobtitle_literal": "校招-车端 AI Agent 与应用生态系统架构工程师",
    "channel_prefix_stripped": "车端 AI Agent 与应用生态系统架构工程师",
}
CHANNEL_MARKERS = ("校招-", "社招-", "实习-", "「2027校招」", "【2027届秋招】", "【2027届校招】")


def covering_ids(catalog: EvidenceCatalog, start: int, end: int) -> list[str]:
    """Adjacent-span coverage, the same rule supports_literal uses."""
    spans = sorted(catalog.spans.values(), key=lambda s: s.start)
    hits = [span for span in spans if span.start < end and span.end > start]
    if not hits or hits[0].start > start or hits[-1].end < end:
        return []
    for left, right in zip(hits, hits[1:]):
        if left.end != right.start:
            return []
    return [span.evidence_id for span in hits]


def strip_markers(value: str) -> str:
    out = value
    for marker in CHANNEL_MARKERS:
        out = out.replace(marker, "")
    return normalize_text(out)


def main() -> None:
    manifest = json.loads((DATA / "source_manifest.json").read_text(encoding="utf-8"))
    record = next(item for item in manifest["records"] if item["jd_key"] == "NIO_AIA_025")
    html = (REPO / record["dom_path"]).read_text(encoding="utf-8", errors="ignore")

    title_tag = re.search(r"<title>(.*?)</title>", html, re.S)
    job_title_span = re.search(r'data-test="jobTitle"[^>]*>(.*?)</span>', html, re.S)
    job_id_in_body = re.search(r"职位\s*ID[：:]\s*([A-Z0-9]+)", html)
    company_mentions = len(re.findall("蔚来", html))

    literals = {
        "document_title": title_tag.group(1).strip() if title_tag else None,
        "job_title_element": job_title_span.group(1).strip() if job_title_span else None,
        "job_id_in_body": job_id_in_body.group(1) if job_id_in_body else None,
        "company_mentions_in_dom": company_mentions,
    }

    snapshot_text = (REPO / record["text_path"]).read_text(encoding="utf-8")
    rows = []
    for name, value in CANDIDATES.items():
        rows.append({
            "candidate": name, "job_title": value,
            "normalize_text": normalize_text(value),
            "in_snapshot_literal": value in snapshot_text,
            "equals_page_element": value == literals["job_title_element"],
            "equals_manifest": value == record["job_title"],
            "stripped_core": strip_markers(value),
            "substantive_equal_to_page": strip_markers(value) == strip_markers(literals["job_title_element"] or ""),
            "whitespace_only_difference_vs_page": (
                normalize_text(value).replace(" ", "") == normalize_text(literals["job_title_element"] or "").replace(" ", "")),
        })

    # The strict guards the agent actually runs, on the real snapshot text.
    class _Snapshot:
        def __init__(self, text):
            self.id, self.source_id, self.content_text = 1, 1, text

    catalog = EvidenceCatalog([_Snapshot(snapshot_text)])
    guard_results = []
    for candidate in CANDIDATES.values():
        try:
            start = snapshot_text.find(candidate)
            if start < 0:
                # whitespace-tolerant locate, same rule as validate_identity
                compact = [(i, ch) for i, ch in enumerate(snapshot_text) if not ch.isspace()]
                target = "".join(normalize_text(candidate).split())
                joined = "".join(ch for _, ch in compact)
                pos = joined.find(target)
                start = compact[pos][0] if pos >= 0 else -1
                end = compact[pos + len(target) - 1][0] + 1 if pos >= 0 else -1
            else:
                end = start + len(candidate)
            ids = covering_ids(catalog, start, end)
            catalog.validate_identity(ids, "蔚来", candidate)
            identity_ok, identity_error = True, None
        except (EvidenceValidationError, IndexError) as exc:
            identity_ok, identity_error = False, str(exc)[:120]
        try:
            validate_expected_identity({"company": "蔚来", "job_title": record["job_title"]}, "蔚来", candidate)
            expected_ok, expected_error = True, None
        except ManualImportError as exc:
            expected_ok, expected_error = False, exc.code
        guard_results.append({"candidate": candidate, "evidence_identity_ok": identity_ok,
                              "evidence_error": identity_error,
                              "expected_identity_ok": expected_ok, "expected_error": expected_error})

    async def task_state() -> dict:
        engine = create_async_engine(
            "postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_wb_jd_r31_20261116")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            task = await db.get(PublicDataRefreshTask, 2)
            manifest_now = manual_manifest(task) if task else None
            snap = await db.get(SourceSnapshot, manifest_now["source_snapshot_id"]) if manifest_now else None
            residual = {
                "companies": await db.scalar(select(func.count()).select_from(Company)),
                "jobs": await db.scalar(select(func.count()).select_from(Job)),
                "jd_versions": await db.scalar(select(func.count()).select_from(JobDescriptionVersion)),
                "claims": await db.scalar(select(func.count()).select_from(Claim)),
                "requirements": await db.scalar(select(func.count()).select_from(JobRequirement)),
                "claim_evidence": await db.scalar(select(func.count()).select_from(ClaimEvidence)),
                "nio_entities": await db.scalar(select(func.count()).select_from(Company).where(
                    Company.canonical_name == "蔚来")),
            }
            tasks = [{"id": t.id, "status": t.status} for t in
                     (await db.scalars(select(PublicDataRefreshTask).order_by(PublicDataRefreshTask.id))).all()]
        await engine.dispose()
        return {
            "task_id": task.id if task else None,
            "task_status": task.status if task else None,
            "task_error": (task.error_message or "")[:120] if task else None,
            "manifest": {k: manifest_now[k] for k in ("company", "job_title", "content_sha256")} if manifest_now else None,
            "snapshot_id": snap.id if snap else None,
            "snapshot_chars": len(snap.content_text or "") if snap else None,
            "snapshot_hash_ok": (snap is not None
                                 and __import__("hashlib").sha256((snap.content_text or "").encode()).hexdigest()
                                 == snap.content_hash),
            "residual": residual, "tasks": tasks,
            "retry_legal": bool(task and task.status == "FAILED" and manifest_now),
        }

    # Would the corrected mapping satisfy both guards *if* the manifest carried it?
    corrected = "车端 AI Agent 与应用生态系统架构工程师"
    corrected_expected = None
    try:
        validate_expected_identity({"company": "蔚来", "job_title": corrected}, "蔚来", corrected)
        corrected_expected = "PASS"
    except ManualImportError as exc:
        corrected_expected = exc.code

    state = asyncio.run(task_state())

    # Is a corrected-label retry legally possible with the frozen task manifest?
    conflict = None
    try:
        from interview_api.modules.public_career.manual_import import ManualDocument, ManualImportService
        from sqlalchemy.ext.asyncio import create_async_engine as _create, async_sessionmaker as _maker

        async def probe():
            engine = _create("postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent_wb_jd_r31_20261116")
            factory = _maker(engine, expire_on_commit=False)
            doc = ManualDocument.from_file(file=REPO / record["text_path"], source_url=record["source_url"],
                                           source_type="official", document_type="job_jd",
                                           company="蔚来", job_title=corrected)
            try:
                return await ManualImportService(factory).prepare(doc)
            finally:
                await engine.dispose()

        conflict = {"prepare_ok": True, "result": {k: v for k, v in (asyncio.run(probe())).items()
                                                   if k in {"refresh_task_id", "reused", "status"}}}
    except Exception as exc:  # expected: identity conflict
        conflict = {"prepare_ok": False, "error": f"{type(exc).__name__}: {exc}"}

    out = {"literals": literals, "manifest_expectation": {"company": record["company"],
                                                          "job_title": record["job_title"]},
           "candidates": rows, "guards": guard_results, "task_2": state,
           "corrected_mapping": {"job_title": corrected, "expected_identity": corrected_expected},
           "corrected_label_retry": conflict}
    (DATA / "identity_audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:2600])


if __name__ == "__main__":
    main()
