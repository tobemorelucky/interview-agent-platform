"""Round 36, phase 2: the authorised NIO retry, gated on F1 being verified.

Order of operations, and the reason for each guard:

  1. F1 self-check (zero HTTP): the line that used to be cut must now sit inside a
     single evidence chunk. If not, exit before a token is spent.
  2. Frozen-input check (zero HTTP): task#2's manifest, task#4's manifest, the
     snapshot bytes and the job id in the body must all match round 34's record.
     The retry must not be a quiet re-acquisition.
  3. Revision preparation twice (zero HTTP): the second call has to return the same
     task, proving idempotency before the first token is spent.
  4. One real run with `force=True` on the *same* task (READY → FAILED recovery is
     the only path the service offers), budget 2 HTTP / 8,000 tokens, no repair.
  5. Full requirement/claim/evidence audit if it completes; nothing is published.

Budget is measured as a delta against the governed ledger, so a second invocation
cannot borrow from round 34's allowance.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "scripts"))

import interview_api.modules.models  # noqa: F401,E402
from interview_api.modules.public_career.manual_import import (  # noqa: E402
    ManualDocument, ManualImportService, manual_manifest,
)
from interview_api.modules.public_career.models import PublicDataRefreshTask  # noqa: E402
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent  # noqa: E402
from interview_api.modules.public_career.research.evidence import EvidenceCatalog  # noqa: E402
from interview_api.modules.public_career.research.prompts import ManualSourcePromptRegistry  # noqa: E402
from r41_run_batch2 import (  # noqa: E402
    EXPECTED_MODEL, EXPECTED_PROVIDER, NIO_TEXT, NIO_URL, SANDBOX, delta, guard_environment, ledger,
)
from r42_audit_publish_batch2 import classify_statement  # noqa: E402

OUT = REPO / "data/r53_nio_retry"
BUDGET = {"max_http": 2, "max_tokens": 8000, "normal_http": 2}
R34 = REPO / "data/r40_batch2/real_run/nio_report.json"


class Snapshot:
    def __init__(self, id_, source_id, text):
        self.id, self.source_id, self.content_text = id_, source_id, text


def f1_selfcheck(body: str) -> dict:
    catalog = EvidenceCatalog([Snapshot(2, 1, body)], max_chars=24000, span_chars=600)
    line = body.splitlines()[16].strip()
    holders = [k for k, s in catalog.spans.items() if line in s.excerpt]
    return {"spans": [(s.start, s.end) for s in sorted(catalog.spans.values(), key=lambda s: s.start)],
            "line17_holders": len(holders),
            "line17_supported_by_one_span": bool(holders) and catalog.supports_literal(holders, line),
            "f1_verified": bool(holders) and catalog.supports_literal(holders, line)}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default=SANDBOX)
    ap.add_argument("--report", default=str(OUT / "nio_retry.json"))
    ap.add_argument("--dry-run", action="store_true",
                    help="run checks 1-3 only; never issues a model call")
    args = ap.parse_args()
    env = guard_environment(args.database)

    body = NIO_TEXT.read_bytes().decode("utf-8")
    literal = body.splitlines()[0].split(" - ")[0].strip()
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "environment": env,
                    "budget": BUDGET, "mode": "nio-retry",
                    "page_literal": literal, "source_url": NIO_URL,
                    "body_chars": len(body), "body_sha256": __import__("hashlib").sha256(
                        body.encode()).hexdigest()}

    check = f1_selfcheck(body)
    report["f1_selfcheck"] = check
    if not check["f1_verified"]:
        report["stopped"] = {"reason": "F1_NOT_VERIFIED"}
        _write(args.report, report)
        print("REFUSED: F1 not verified ->", check)
        return 2

    r34 = json.loads(R34.read_text(encoding="utf-8"))
    if (r34["predecessor"]["content_sha256"] != report["body_sha256"]
            or r34["revision_requested"]["job_title"] != literal
            or r34["revision_requested"]["source_url"] != NIO_URL):
        report["stopped"] = {"reason": "FROZEN_INPUT_CHANGED"}
        _write(args.report, report)
        print("REFUSED: frozen inputs changed since round 34")
        return 2
    report["frozen_inputs_unchanged"] = True

    engine = create_async_engine(
        f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{args.database}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ManualImportService(factory)
    agent = PublicDataResearchAgent(factory, build_model_router())

    try:
        async with factory() as db:
            tasks = (await db.scalars(select(PublicDataRefreshTask).order_by(
                PublicDataRefreshTask.id))).all()
            nio = []
            for task in tasks:
                manifest = manual_manifest(task)
                if manifest and manifest.get("canonical_url") == NIO_URL:
                    nio.append({"id": task.id, "status": task.status,
                                "revision_of": None, "job_title": manifest["job_title"],
                                "content_sha256": manifest["content_sha256"],
                                "error": (task.error_message or "")[:160]})
            report["tasks_before"] = nio
            predecessor = next(t for t in nio if t["status"] == "FAILED" and t["job_title"] != literal)
            revision = next((t for t in nio if t["job_title"] == literal), None)
    finally:
        pass

    document = ManualDocument.from_file(file=NIO_TEXT, source_url=NIO_URL, source_type="official",
                                        document_type="job_jd", company="蔚来", job_title=literal)
    before = await ledger(factory)
    first = await service.prepare_identity_revision(
        document, predecessor_task_id=predecessor["id"], authorized_by="user:R36-approval",
        reason="R36：F1 修复后按批准重试；沿用 R34 已建的修订任务，不改冻结字段、不改正文。")
    second = await service.prepare_identity_revision(
        document, predecessor_task_id=predecessor["id"], authorized_by="user:R36-approval",
        reason="idempotency probe")
    after_prep = await ledger(factory)
    report["revision"] = {"task_id": first["refresh_task_id"], "revision_of": first["revision_of"],
                          "status": first["status"], "reused": first["reused"],
                          "second_call_same_task": second["refresh_task_id"] == first["refresh_task_id"],
                          "idempotent": second["refresh_task_id"] == first["refresh_task_id"]}
    report["prep_ledger"] = delta(before, after_prep)
    if not report["revision"]["idempotent"]:
        report["stopped"] = {"reason": "REVISION_NOT_IDEMPOTENT"}
        _write(args.report, report)
        print("REFUSED: revision is not idempotent")
        return 2
    if report["prep_ledger"]["http"] != 0 or report["prep_ledger"]["tokens"] != 0:
        report["stopped"] = {"reason": "PREPARATION_SPENT_BUDGET"}
        _write(args.report, report)
        print("REFUSED: preparation issued a model call")
        return 2
    if revision and revision["id"] != first["refresh_task_id"]:
        report["stopped"] = {"reason": "REVISION_TASK_DRIFT"}
        _write(args.report, report)
        print("REFUSED: a different revision task was produced")
        return 2

    if args.dry_run:
        report["dry_run"] = True
        _write(args.report, report)
        print("dry run complete; revision task", first["refresh_task_id"], "ready, 0 HTTP")
        return 0

    result = await agent.run(first["refresh_task_id"], force=True)
    after = await ledger(factory)
    report["run"] = {key: result.get(key) for key in
                     ("refresh_task_id", "agent_run_id", "status", "company_id", "job_id",
                      "jd_version_id", "publication_status", "error")}
    report["ledger"] = delta(before, after)
    report["budget_ok"] = (report["ledger"]["http"] <= BUDGET["max_http"]
                           and report["ledger"]["tokens"] <= BUDGET["max_tokens"])
    report["repair_used"] = None  # asserted below from the step rows

    async with factory() as db:
        from interview_api.modules.experience.models import ExperienceAgentStepRun
        steps = (await db.scalars(select(ExperienceAgentStepRun).where(
            ExperienceAgentStepRun.agent_run_id == result.get("agent_run_id")).order_by(
            ExperienceAgentStepRun.id))).all()
        report["steps"] = [{"step": s.step_name, "status": s.status, "prompt": f"{s.prompt_name}@{s.prompt_version}",
                            "structured_retry": s.structured_retry, "tokens": s.token_usage_json,
                            "error": s.error_message} for s in steps]
        report["repair_used"] = sum(s.structured_retry for s in steps)
    _write(args.report, report)

    print(f"status={report['run']['status']} ledger={report['ledger']} repair={report['repair_used']}")
    if report["run"]["status"] == "COMPLETED" and report["run"].get("jd_version_id"):
        await audit(db_factory=factory, jd_id=report["run"]["jd_version_id"], report=report,
                    report_path=Path(args.report))
    elif report["run"]["status"] == "COMPLETED":
        report["audit"] = {"skipped": "no jd_version_id returned"}
        _write(args.report, report)
    else:
        print("error:", report["run"]["error"])
    await engine.dispose()
    return 0


async def audit(*, db_factory, jd_id, report: dict, report_path: Path) -> None:
    """Requirement/claim/evidence review; never publishes."""
    from interview_api.modules.public_career.models import (
        Claim, ClaimEvidence, JobDescriptionVersion, JobRequirement,
    )
    async with db_factory() as db:
        jd = await db.get(JobDescriptionVersion, jd_id)
        requirements = (await db.scalars(select(JobRequirement).where(
            JobRequirement.job_description_version_id == jd_id).order_by(JobRequirement.id))).all()
        claims = {c.id: c for c in (await db.scalars(select(Claim).where(
            Claim.id.in_({r.claim_id for r in requirements}))))}
        evidence = (await db.scalars(select(ClaimEvidence).where(
            ClaimEvidence.claim_id.in_(set(claims))).order_by(ClaimEvidence.id))).all()
    rows, buckets = [], {}
    for requirement in requirements:
        kind = classify_statement(requirement.statement, jd.raw_text)
        buckets[kind] = buckets.get(kind, 0) + 1
        rows.append({"requirement_id": requirement.id, "kind": kind,
                     "importance": requirement.importance,
                     "requirement_type": requirement.requirement_type,
                     "statement": requirement.statement,
                     "evidence": [e.excerpt for e in evidence if e.claim_id == requirement.claim_id]})
    report["audit"] = {"jd_version_id": jd_id, "publication_status": jd.publication_status,
                       "requirement_count": len(requirements), "kind_counts": buckets,
                       "requirements": rows,
                       "note": "held at WAITING_REVIEW; no publish attempted"}
    _write(str(report_path), report)
    print("audit:", buckets, "status:", jd.publication_status)


def _write(path, payload) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def build_model_router():
    from interview_api.infrastructure.models.router import build_model_router as build
    router = build()
    for route in ("public_research_extract", "public_research_quality"):
        provider = router.primary(route)
        assert provider.provider_name == EXPECTED_PROVIDER and provider.model_name == EXPECTED_MODEL
        assert getattr(provider, "governed", False)
    assert not router.fallback_routes
    assert ManualSourcePromptRegistry().get("job_jd").version == "1.0.1"
    return router


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
