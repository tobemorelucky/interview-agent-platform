"""R135 P1: real-model extraction over R132's already-collected snapshots (no Search/Fetch).

Uses the production interview_extract prompt + FragmentInterviewExtraction schema and
resolve_fragment_extraction for evidence checks. Only 2 model calls (one per snapshot).
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
if not os.environ.get("R135_DATABASE_URL"):
    raise SystemExit("set R135_DATABASE_URL")
os.environ["DATABASE_URL"] = os.environ["R135_DATABASE_URL"]

import interview_api.modules.models  # noqa: F401
import interview_api.modules.job_target.models  # noqa: F401
import interview_api.modules.purge.models  # noqa: F401
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.infrastructure.models.router import EXTRACT_ROUTE, build_model_router
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.public_career.research.audit import ResearchModelCalls
from interview_api.modules.public_career.research.fragments import InterviewSourceFragments
from interview_api.modules.public_career.research.interview import resolve_fragment_extraction
from interview_api.modules.public_career.research.schemas import FragmentInterviewExtraction

OUT = Path(__file__).resolve().parents[1] / "data/r135_audit"
OUT.mkdir(exist_ok=True)
SNAPSHOTS = (52, 53)


async def main():
    factory = async_sessionmaker(create_async_engine(os.environ["R135_DATABASE_URL"]), expire_on_commit=False)
    router = build_model_router()
    result = dict(started=datetime.now(timezone.utc).isoformat(), snapshots={})
    for sid in SNAPSHOTS:
        async with factory() as db:
            row = (await db.execute(text("select id, source_id, content_text from source_snapshots where id=:i"),
                                    {"i": sid})).first()
            # One governed run per snapshot, attributed to system:public-career via task_id.
            run = ExperienceAgentRun(agent_role="public_research", graph_name="r135_extract_accept",
                graph_version="1.0", status="RUNNING", started_at=datetime.now(timezone.utc), task_id=1)
            db.add(run)
            await db.flush()
            await db.commit()
            run_id = run.id
        snap = SimpleNamespace(id=row[0], source_id=row[1], content_text=row[2])
        catalog = InterviewSourceFragments([snap])
        kinds = dict(catalog.kinds)
        catalog_model_input = catalog.model_input()
        before = await _usage(factory)
        calls = ResearchModelCalls(factory, router, run_id)
        selected = await calls.call("interview_extract", EXTRACT_ROUTE,
            {"source_fragments": catalog_model_input}, FragmentInterviewExtraction)
        extraction, anchors = resolve_fragment_extraction(selected, catalog)
        after = await _usage(factory)
        questions = []
        for q in extraction.questions:
            span = catalog.spans.get(q.evidence_ids[0]) if q.evidence_ids else None
            questions.append(dict(question=q.question, kind=getattr(q, "kind", None),
                evidence_excerpt=span.excerpt if span else None,
                literal_in_source=(span.excerpt in snap.content_text) if span else False))
        result["snapshots"][str(sid)] = dict(chars=len(snap.content_text),
            fragment_counts=_counts(kinds), catalog_fragments=len(catalog_model_input),
            model_questions=len(extraction.questions), questions=questions,
            rejected=getattr(anchors, "rejected_questions", None) or anchors.get("rejected_questions", []),
            delta_http=after["http"] - before["http"], delta_tokens=after["tokens"] - before["tokens"])
        async with factory() as db:
            r = await db.get(ExperienceAgentRun, run_id)
            r.status, r.finished_at = "SUCCEEDED", datetime.now(timezone.utc)
            await db.commit()
        print("EXTRACT", sid, "questions", len(extraction.questions), "http",
              after["http"] - before["http"], flush=True)
    result["finished"] = datetime.now(timezone.utc).isoformat()
    (OUT / "r135_extraction.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("DONE", json.dumps({k: dict(q=v["model_questions"], http=v["delta_http"], tok=v["delta_tokens"])
                              for k, v in result["snapshots"].items()}, ensure_ascii=False), flush=True)


def _counts(kinds):
    out = {}
    for k in kinds.values():
        out[k] = out.get(k, 0) + 1
    return out


async def _usage(factory):
    async with factory() as db:
        steps = (await db.scalars(select(ExperienceAgentStepRun).where(
            ExperienceAgentStepRun.step_name == "interview_extract"))).all()
    http = sum(len((s.output_json or {}).get("model_invocations") or []) or 1 for s in steps)
    tokens = sum((s.token_usage_json or {}).get("total_tokens", 0) or 0 for s in steps)
    return dict(http=http, tokens=tokens)


asyncio.run(main())
