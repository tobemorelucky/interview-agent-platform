"""Non-billable V2.2 preparation. This script has NO Live execution mode.

Creates Replay history through normal services, then READY sessions and exact
Structured Output message previews. Network model generation is prohibited.
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
sys.path[:0] = [str(API / "src"), str(API / "tests")]

from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.core.config import settings
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.infrastructure.models.provider import CompatibleModelProvider, ModelProvider
from interview_api.infrastructure.models.router import (ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE,
    build_interview_router, build_model_router, build_resume_profile_router, build_job_matching_router, build_interview_coach_router)
from interview_api.infrastructure.models.structured import generate_structured
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.adaptive import planning_payload, AdaptiveSelection
from interview_api.modules.interview.skill_engine import agent_payload
from interview_api.modules.memory.capability_service import CapabilityMemoryService
from interview_api.modules.interview.models import InterviewAgentCall, InterviewSession
from interview_api.modules.memory.models import UserMemoryItem
from interview_api.modules.users.models import User
from v22_support import kafka_seed, MemoryReplayProvider

TARGET = "deepseek-v4-pro-ga-260813"


class CapturedRequest(RuntimeError):
    pass


class CaptureOnlyProvider(ModelProvider):
    provider_name, model_name = "CAPTURE_ONLY", "NO_MODEL_REQUEST"

    async def generate(self, messages, *, schema):
        self.captured = dict(messages=messages, schema=schema)
        raise CapturedRequest("No model was called")


async def preview(payload):
    """Mirror InterviewAgent.call's existing model-facing projection, not a new prompt."""
    value = agent_payload(payload, INTERVIEW_MAIN_ROUTE)
    value = deepcopy(value)
    policy = value["snapshot"]["adaptive_policy"]
    value["snapshot"]["adaptive_policy"] = dict(version=policy["version"],
        prompt_fingerprints={k: v["fingerprint"] for k, v in policy["prompts"].items()})
    value["snapshot"].pop("adaptive_blueprint", None)
    value["adaptive"].pop("choices", None)
    provider = CaptureOnlyProvider()
    try:
        await generate_structured(ModelRouter({INTERVIEW_MAIN_ROUTE: provider}), INTERVIEW_MAIN_ROUTE,
            system=policy["prompts"][INTERVIEW_MAIN_ROUTE]["system"], payload=value,
            response_model=AdaptiveSelection, timeout_seconds=5)
    except CapturedRequest:
        return provider.captured
    raise AssertionError("Capture-only provider unexpectedly returned a generated result")


def configured_routes():
    rows = []
    for router in (build_model_router(), build_resume_profile_router(), build_interview_router(),
                   build_job_matching_router(), build_interview_coach_router()):
        for name, provider in router.routes.items():
            rows.append(dict(route=name, provider=provider.provider_name, model=provider.model_name,
                thinking=provider.thinking_type, timeout_seconds=provider.timeout,
                max_output_tokens=provider.max_output_tokens))
        if router.fallback_routes:
            raise RuntimeError("Explicitly review fallback routes before preparing a no-fallback Live batch")
    if any(row["model"] != TARGET for row in rows):
        raise RuntimeError("Configured route model mismatch; configuration has NOT been changed")
    return rows


async def prepare(database):
    if not database.startswith("interview_agent_v21_v22_live_prepare_") or not database.replace("_", "").isalnum():
        raise ValueError("A fresh V2.2 Live preparation database is required")
    routes = configured_routes()  # Configuration inspection only; never generate().
    engine = create_async_engine(make_url(settings.database_url).set(database=database))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    artifact = dict(database=database, created_at=datetime.now(timezone.utc).isoformat(),
        status="PREPARED_AWAITING_EXPLICIT_LIVE_AUTHORIZATION", routes=routes,
        live_logical_calls=0, live_http_calls=0, live_tokens=0,
        proposed_limits=dict(logical=6, http=10, tokens=60000, seconds=480, batch_repair_limit=1),
        data_mode="DEMO_REPLAY_HISTORY", future_mode="REPLAY_HISTORY_PLUS_LIVE_DECISION_NOT_FULL_LIVE",
        scenarios=[])
    try:
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version")) == "0026"
            assert await db.scalar(select(func.count()).select_from(User)) == 0, "Do not append to an earlier attempt"
        for name, expected, count in (("A_VERIFIED", "VERIFIED", 2), ("B_CONFLICTED", "CONFLICTED", 3)):
            uid, ctx = await kafka_seed(factory)
            async with factory() as db:
                user = await db.get(User, uid)
                user.username = "DEMO_REPLAY_HISTORY_" + name
                await db.commit()
            provider = MemoryReplayProvider()
            replay = MockInterviewService(factory, ModelRouter({INTERVIEW_MAIN_ROUTE: provider, ANSWER_EVALUATION_ROUTE: provider}))
            histories = []
            # Freeze identical histories BEFORE the answers: no retrospective
            # mutation of questions, snapshots, evaluations or a VERIFIED state.
            for index in range(count):
                histories.append(await replay.create(uid, CreateSessionRequest(company_id=ctx["company_id"],
                    job_id=ctx["job_id"], interviewer_skill_id="java_backend", adaptive_interview=True,
                    question_count=1, title=f"DEMO REPLAY HISTORY {name} #{index+1}")))
            fixed = "[DEMO/REPLAY] Kafka 业务唯一键和业务更新在同一事务提交，提交成功再 ACK；重投读取已有结果而不重复执行。"
            bad = "[DEMO/REPLAY] Kafka 消费失败重试，成功以后 ACK。"
            for index, history in enumerate(histories):
                q = await replay.start(history["id"], uid)
                await replay.answer(history["id"], uid, q["question_id"], uuid4().hex,
                    bad if name == "B_CONFLICTED" and index == 2 else fixed)
                assert (await replay.detail(history["id"], uid))["status"] == "COMPLETED"
            async with factory() as db:
                root = next(r for r in await CapabilityMemoryService(db).list(uid) if r["topic"] == "kafka_reliability")
                assert root["state"] == expected, (name, root["state"])
                assert len(root["observations"]) == count
                assert all(not o["assisted"] for o in root["observations"])
                assert await db.scalar(select(func.count()).select_from(UserMemoryItem).where(UserMemoryItem.user_id == uid)) == 0
            # Normal Session creation freezes the current memory and full Skill
            # topics. Eight slots retain Kafka as a LEGAL alternative for A;
            # success cannot be manufactured merely by removing it from choices.
            ready = await replay.create(uid, CreateSessionRequest(company_id=ctx["company_id"], job_id=ctx["job_id"],
                interviewer_skill_id="java_backend", adaptive_interview=True, question_count=8,
                title="DEMO LIVE PENDING AUTHORIZATION " + name))
            session, context, questions, active, _ = await replay.read_state(ready["id"], uid)
            payload = planning_payload(context, questions, active, session.interview_plan_json)
            kafka = [c for c in payload["adaptive"]["candidates"] if c["target_knowledge_id"].endswith(":kafka_reliability")]
            assert kafka and len({c["target_knowledge_id"] for c in payload["adaptive"]["candidates"]}) >= 2
            plan = context["memory_guided_plan"]
            if name == "A_VERIFIED":
                assert not plan["items"]
                assert any(t["knowledge_id"].endswith(":kafka_reliability") for t in plan["deprioritized_targets"])
            else:
                assert plan["items"][0]["knowledge_id"].endswith(":kafka_reliability") and plan["items"][0]["needs_recheck"]
            assert session.status == "READY" and not questions and active is None
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(InterviewAgentCall).where(InterviewAgentCall.session_id == session.id)) == 0
            captured = await preview(payload)
            artifact["scenarios"].append(dict(name=name, user_id=uid, live_session_id=session.id,
                history_session_ids=[h["id"] for h in histories], history_mode="REPLAY", history_frozen=True,
                capability=root, memory_guided_plan=plan, snapshot_fingerprint=digest(context),
                prompt=context["adaptive_policy"]["prompts"][INTERVIEW_MAIN_ROUTE],
                controller_payload=payload, request_preview=captured,
                legal_kafka_candidates=kafka, live_session_status="READY", live_decision=None,
                controller_verdict="NOT_EXECUTED_NO_LIVE_AUTHORIZATION", knowledge_change="NOT_YET_OBSERVED"))
        return artifact
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if not output.is_relative_to((ROOT / ".runtime/acceptance").resolve()) or output.exists():
        raise SystemExit("Use a NEW ignored acceptance artifact; never overwrite a prior attempt")
    async def forbidden(*a, **k): raise AssertionError("LIVE/HTTP is prohibited during preparation")
    import httpx
    with patch.object(CompatibleModelProvider, "generate", forbidden), patch.object(httpx.AsyncClient, "send", forbidden):
        result = asyncio.run(asyncio.wait_for(prepare(args.database), 90))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(status=result["status"], live_calls=0,
        scenarios=[{k: s[k] for k in ("name", "user_id", "live_session_id", "live_session_status")} for s in result["scenarios"]]), ensure_ascii=False))
