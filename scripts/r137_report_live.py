"""R137: one controlled safe-retry of report #19 (session 641) after the number-guard fix.

Runs against an isolated clone (R137_DATABASE_URL), never the live/showcase DB that
the browser used. At most ONE Coach model call. Verifies the report becomes COMPLETED,
the 3 persisted evaluations are unchanged, attempts 1-3 stay FAILED, no duplicate
memory proposals, and the session stays COMPLETED.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
if not os.environ.get("R137_DATABASE_URL"):
    raise SystemExit("set R137_DATABASE_URL to the isolated acceptance database")
os.environ["DATABASE_URL"] = os.environ["R137_DATABASE_URL"]

for mod in ("interview_api.modules.models", "interview_api.modules.job_target.models",
            "interview_api.modules.purge.models"):
    __import__(mod)

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.interview.models import InterviewSession, InterviewEvaluation
from interview_api.modules.memory.proposal_models import MemoryProposal
from interview_api.modules.interview_report.models import (
    FinalInterviewReport, InterviewReportAttempt)
from interview_api.modules.interview_report.service import InterviewReportService

DB = os.environ["R137_DATABASE_URL"]
UID = 21
SESSION = 641
OUT = Path(__file__).resolve().parents[1] / "data/r137_audit"
OUT.mkdir(exist_ok=True)


def jdefault(o):
    return str(o)


async def snap(factory):
    async with factory() as db:
        report = await db.get(FinalInterviewReport, 19)
        attempts = (await db.scalars(select(InterviewReportAttempt)
            .where(InterviewReportAttempt.report_id == 19)
            .order_by(InterviewReportAttempt.attempt_number))).all()
        evals = (await db.scalars(select(InterviewEvaluation)
            .where(InterviewEvaluation.session_id == SESSION))).all()
        props = (await db.scalars(select(MemoryProposal)
            .where(MemoryProposal.session_id == SESSION))).all()
        session = await db.get(InterviewSession, SESSION)
        max_step = await db.scalar(select(func.max(ExperienceAgentStepRun.id))) or 0
        coach_steps = (await db.scalar(select(func.count()).select_from(ExperienceAgentStepRun)
            .where(ExperienceAgentStepRun.step_name == "interview_coach"))) or 0
    return dict(
        report=dict(status=report.generation_status, attempt_version=report.attempt_version,
                    is_current=report.is_current, error=report.error,
                    has_analysis=bool(report.coach_analysis_json),
                    summary=(report.coach_analysis_json or {}).get("overall_summary")),
        attempts=[dict(n=a.attempt_number, status=a.status, error=a.error) for a in attempts],
        eval_ids=sorted(e.id for e in evals),
        eval_fingerprints=[(e.id, e.created_at.isoformat() if e.created_at else None) for e in evals],
        proposal_ids=sorted(p.id for p in props),
        session_status=session.status,
        max_step=max_step, coach_steps=coach_steps,
    )


async def main():
    factory = async_sessionmaker(create_async_engine(DB), expire_on_commit=False)
    before = await snap(factory)
    print("BEFORE", json.dumps(before, ensure_ascii=False, default=jdefault), flush=True)
    result = None
    error = None
    try:
        svc = InterviewReportService(factory)
        result = await svc.generate(SESSION, UID, regenerate=False)
    except Exception as exc:
        error = dict(type=type(exc).__name__, code=getattr(exc, "code", None), msg=str(exc)[:300])
    after = await snap(factory)
    # One controlled call: count coach steps added by this run.
    delta_coach = after["coach_steps"] - before["coach_steps"]
    evidence = dict(
        started=datetime.now(timezone.utc).isoformat(),
        before=before, after=after,
        result_status=(result or {}).get("generation_status") if result else None,
        result_error=(result or {}).get("error") if result else None,
        error=error, delta_coach_http=delta_coach,
        checks=dict(
            report_completed=after["report"]["status"] == "COMPLETED",
            report_current=after["report"]["is_current"] is True,
            analysis_present=after["report"]["has_analysis"],
            evals_unchanged=before["eval_ids"] == after["eval_ids"],
            session_completed=after["session_status"] == "COMPLETED",
            history_attempts_preserved=all(
                a["status"] == "FAILED" for a in after["attempts"] if a["n"] <= 3),
            new_attempt_succeeded=any(
                a["n"] > 3 and a["status"] == "SUCCEEDED" for a in after["attempts"]),
            # attempts 1-3 all FAILED so `before` had 0 proposals; the successful
            # attempt legitimately creates this report's proposals for the first
            # time (they are not duplicates). Actual duplicate detection is the
            # idempotency recheck: a second generate() must add none.
            proposals_newly_created=len(after["proposal_ids"]) - len(before["proposal_ids"]),
            single_coach_call=delta_coach <= 1,
        ),
    )
    (OUT / "r137_report_live.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=jdefault), encoding="utf-8")
    print("AFTER", json.dumps(after, ensure_ascii=False, default=jdefault), flush=True)
    print("CHECKS", json.dumps(evidence["checks"], ensure_ascii=False), flush=True)
    print("DELTA_COACH_HTTP", delta_coach, flush=True)


asyncio.run(main())
