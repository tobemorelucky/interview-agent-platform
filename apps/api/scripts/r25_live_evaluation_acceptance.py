"""Round 25: live acceptance of the *official* Evaluation chain.

Three paired samples (six answers) go through the product's own path:

``create -> start -> prepare_answer -> Evaluation Agent -> commit_answer``

The Main question is Replay (no HTTP); the Evaluation reply comes from the real
ModelScope model behind the normal admission/settlement gateway. A hard budget
guard sits in front of the provider so a runaway repair loop cannot exceed the
approved HTTP/Token/time caps.

Two subcommands:

``--mode freeze``  build ``r25_frozen_pairs.json`` from the V2 dataset (no model)
``--mode run``     execute the frozen pairs against a sandbox database; the
                   evaluation route is Replay (``--evaluation replay``) or the
                   real model (``--evaluation live``)
"""
import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

API_ROOT = Path(__file__).resolve().parents[1]
for extra in (API_ROOT / "src", API_ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_api.core.config import settings
from interview_api.infrastructure.models.scope import current_scope, expected_ledger_database
from interview_api.infrastructure.models.provider import (
    CompatibleModelProvider, ModelProviderError, ModelProvider, ModelScopeModelProvider,
    ModelResponse, modelscope_connection,
)
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from eval_pair_support import DIMENSIONS, FixedQuestionReplay, ScriptedEvaluation, seed_user

MAIN_ROUTE = "interview_main"
EVAL_ROUTE = "answer_evaluation"
AUTHORIZED_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash-1017"

PACKAGE = API_ROOT.parents[1] / "data" / "sandbox_test_pkg_20261114"
DATASET = PACKAGE / "2026校招JD_真实面经_模拟回答测试包"
FREEZE = PACKAGE / "r25_frozen_pairs.json"

SUBJECTS = {
    "Q_RAG_01": dict(pair_id="R25-RAG-P1", skill_id="ai_agent_application",
                     jd_statements=["理解 RAG 检索与融合（混合召回、排序融合、RRF）",
                                    "能说明召回与排序的参数取舍及验证方式"],
                     jd_title="AI Agent 应用开发工程师",
                     contrast="简短且正确 vs 更长且含明确技术错误（POOR/GOOD=3.6x）",
                     expected_relation="HIGHER_FIRST"),
    "Q_LG_01": dict(pair_id="R25-LG-P1", skill_id="ai_agent_application",
                    jd_statements=["了解 LangGraph 等有状态 Agent 编排与断点恢复",
                                   "能说明 state、边与持久化之间的关系"],
                    jd_title="AI Agent 应用开发工程师",
                    contrast="基本正确但不完整 vs 大体合理但含一处关键机制错误",
                    expected_relation="HIGHER_SECOND_MODEST"),
    "Q_KF_01": dict(pair_id="R25-KF-P1", skill_id="java_backend",
                    jd_statements=["熟悉 Kafka 消费者可靠性（至少一次语义、offset 提交）",
                                   "能在业务侧实现消息幂等，并说清 exactly-once 的边界"],
                    jd_title="Java 后端开发工程师",
                    contrast="技术正确 vs 更长但误解消息幂等与 exactly-once 边界（POOR/GOOD=1.1x）",
                    expected_relation="HIGHER_FIRST"),
}

# (question_id, first sample, second sample). A pair always shares one question_id.
SELECTION = {
    "Q_RAG_01": ("RAG-B1", "RAG-A2"),
    "Q_LG_01": ("LG-B2", "LG-C1"),
    "Q_KF_01": ("KF-A1", "KF-A2"),
}
FORBIDDEN_IN_PAYLOAD = ("quality_label", "expected_band", "human_check_points",
                        "SYNTHETIC_R24_V2", "RAG-A2", "RAG-B1", "LG-B2", "LG-C1", "KF-A1", "KF-A2")


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ---------------------------------------------------------------- freeze


def freeze() -> dict:
    questions = {row["question_id"]: row for row in _read_jsonl(DATASET / "08_V2_题目与评分要点.jsonl")}
    samples = {row["sample_id"]: row for row in _read_jsonl(DATASET / "09_V2_评测样本.jsonl")}
    pairs = []
    for question_id, (first, second) in SELECTION.items():
        subject = SUBJECTS[question_id]
        question = questions[question_id]
        assert samples[first]["question_id"] == question_id == samples[second]["question_id"]
        entries = []
        for role, sample_id in (("FIRST", first), ("SECOND", second)):
            row = samples[sample_id]
            entries.append(dict(
                role=role, sample_id=sample_id, dataset_pair_id=row["pair_id"],
                quality_label=row["quality_label"], char_count=row["char_count"],
                answer_text=row["answer_text"],
                expected_band=row["expected_band"], expected_relation=row["expected_relation"],
                human_check_points=row["human_check_points"], human_technical_errors=row["errors"],
                answer_sha256=_sha(row["answer_text"])))
        pairs.append(dict(
            pair_id=subject["pair_id"], question_id=question_id, topic=question["topic"],
            dimension=question["dimension"], skill_id=subject["skill_id"],
            jd_title=subject["jd_title"], jd_statements=subject["jd_statements"],
            question=question["question"], rubric=question["rubric"],
            question_sha256=_sha(question["question"]), rubric_sha256=_sha(question["rubric"]),
            contrast=subject["contrast"], expected_relation=subject["expected_relation"],
            samples=entries))
    payload = dict(
        generated_at=datetime.now(timezone.utc).isoformat(),
        dataset=str(DATASET.relative_to(API_ROOT.parents[1])),
        selection_note=("V2 数据集按配对设计；本轮按「质量对比类型」重组为 3 个评分对，"
                        "同一对共享同一 question_id / rubric（已验证），仅回答与用户不同。"),
        pairs=pairs)
    FREEZE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_frozen() -> dict:
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    for pair in frozen["pairs"]:
        assert _sha(pair["question"]) == pair["question_sha256"], pair["pair_id"]
        assert _sha(pair["rubric"]) == pair["rubric_sha256"], pair["pair_id"]
        for sample in pair["samples"]:
            assert _sha(sample["answer_text"]) == sample["answer_sha256"], sample["sample_id"]
    return frozen


# ---------------------------------------------------------------- fixtures


async def seed_case(factory, statements, title):
    """A published JD for one topic; no user, so it can serve both owners."""
    import interview_api.modules.models  # noqa: F401  (register every mapper)
    from interview_api.modules.experience.models import InterviewExperience, InterviewQuestion
    from interview_api.modules.public_career.models import (
        Company, Job, JobDescriptionVersion, JobRequirement,
    )
    async with factory() as db:
        company = Company(canonical_name="R25 评测夹具 " + uuid4().hex)
        db.add(company)
        await db.flush()
        job = Job(company_id=company.id, canonical_title=title)
        db.add(job)
        await db.flush()
        jd = JobDescriptionVersion(job_id=job.id, version_number=1,
                                   raw_text="R25 评测夹具 JD：" + "；".join(statements),
                                   publication_status="PUBLISHED")
        db.add(jd)
        await db.flush()
        for statement in statements:
            db.add(JobRequirement(job_description_version_id=jd.id, statement=statement,
                                  requirement_type="SKILL"))
        experience = InterviewExperience(company_id=company.id, job_id=job.id,
                                         review_status="APPROVED", summary="R25 fixture",
                                         content_text="R25 fixture content")
        db.add(experience)
        await db.flush()
        db.add(InterviewQuestion(company_id=company.id, job_id=job.id, experience_id=experience.id,
                                 question="R25 公共题占位：请说明一个技术决策的取舍",
                                 standard_answer="取舍\n边界", review_status="APPROVED",
                                 category="缓存", difficulty="MEDIUM"))
        await db.commit()
        return dict(company_id=company.id, job_id=job.id)


async def open_case(factory, case, *, user_id, skill_id, question, expected_points, dimension, topic,
                    evaluator):
    main = FixedQuestionReplay(question, expected_points, dimension, topic)
    core = MockInterviewService(factory, ModelRouter({MAIN_ROUTE: main, EVAL_ROUTE: evaluator}))
    session = await core.create(user_id, CreateSessionRequest(
        company_id=case["company_id"], job_id=case["job_id"], question_count=1,
        interviewer_skill_id=skill_id, adaptive_interview=True))
    created = await core.start(session["id"], user_id)
    assert created["question"] == question, created["question"]
    return core, session["id"], created["question_id"], main


# ---------------------------------------------------------------- budget guard


class Budget:
    def __init__(self, http_cap: int, token_cap: int, seconds: float):
        self.http_cap, self.token_cap, self.seconds = http_cap, token_cap, seconds
        self.started = time.monotonic()
        self.calls = 0
        self.tokens = 0
        self.records: list[dict] = []
        self.stopped: str | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def gate(self, schema_name: str) -> None:
        if self.calls >= self.http_cap:
            self.stopped = "HTTP_CAP"
        elif self.tokens >= self.token_cap:
            self.stopped = "TOKEN_CAP"
        elif self.elapsed() >= self.seconds:
            self.stopped = "TIME_CAP"
        if self.stopped:
            error = ModelProviderError("harness_budget_stop", f"预算守卫拒绝发送（{self.stopped}）")
            error.request_sent, error.usage = False, {"total_tokens": 0}
            raise error


class GuardedEvaluation(ModelProvider):
    """Count, cap and record every real Evaluation request; nothing is retried here."""

    provider_name = "modelscope"

    def __init__(self, inner, budget: Budget):
        self.inner, self.budget = inner, budget
        self.model_name = inner.model_name
        self.max_output_tokens = inner.max_output_tokens
        self.timeout = inner.timeout
        self.base_url = inner.base_url
        self.payloads: list[dict] = []

    async def generate(self, messages, *, schema):
        self.payloads.append(_audit_payload(messages, schema))
        self.budget.gate(schema.get("title") or "")
        started = time.perf_counter()
        self.budget.calls += 1
        try:
            result = await self.inner.generate(messages, schema=schema)
        except ModelProviderError as exc:
            usage = getattr(exc, "usage", {}) or {}
            self.budget.records.append(dict(
                seq=self.budget.calls, schema=schema.get("title"), status="FAILED",
                code=exc.code, request_sent=getattr(exc, "request_sent", None),
                tokens=usage.get("total_tokens"), latency_ms=round((time.perf_counter() - started) * 1000)))
            total = usage.get("total_tokens")
            if type(total) is int:
                self.budget.tokens += total
            raise
        total = result.usage.get("total_tokens")
        self.budget.tokens += total if type(total) is int else 0
        self.budget.records.append(dict(
            seq=self.budget.calls, schema=schema.get("title"), status="OK", code=None,
            tokens=total, input_tokens=result.usage.get("input_tokens"),
            output_tokens=result.usage.get("output_tokens"),
            latency_ms=round((time.perf_counter() - started) * 1000)))
        return result


def _scope_owner() -> str | None:
    """The owner the ledger will book this call to, as the gateway sees it."""
    scope = current_scope()
    return scope.owner if scope else None


def _audit_payload(messages, schema) -> dict:
    """What the Evaluation model actually received, without keeping its body."""
    raw = messages[-1]["content"]
    audit = dict(schema=schema.get("title"), chars=len(raw), owner=_scope_owner(),
                 forbidden=[token for token in FORBIDDEN_IN_PAYLOAD if token in raw])
    try:
        body = json.loads(raw)["input"]
        audit.update(input_keys=sorted(body), rubric_lines=len(body.get("rubric") or []),
                     answer_chars=len(body.get("answer") or ""))
    except (ValueError, KeyError, TypeError):
        audit.update(input_keys=[], rubric_lines=None, answer_chars=None)
    return audit


def _call_count(budget, evaluator) -> int:
    if budget is not None:
        return budget.calls
    return len(getattr(evaluator, "calls", getattr(evaluator, "payloads", [])))


class LedgerProbe(CompatibleModelProvider):
    """The real gateway with a canned reply and no HTTP.

    Proves that a call made *for this sample* is reserved and settled under that
    sample's own owner and the evaluation route, without spending any budget.
    """

    def __init__(self):
        super().__init__(base_url="http://ledger-probe.invalid", api_key="probe",
                         model=AUTHORIZED_MODEL, timeout_seconds=30, max_output_tokens=3000,
                         governed=True)
        self.provider_name = "modelscope"
        self.payloads: list[dict] = []

    async def generate(self, messages, *, schema):
        self.payloads.append(_audit_payload(messages, schema))
        return await super().generate(messages, schema=schema)

    async def _generate(self, messages, *, schema):
        payload = json.loads(messages[-1]["content"])["input"]
        knowledge_id = payload["adaptive"]["active_knowledge_id"]
        value = dict(
            technical_correctness=4, coverage=4, depth=4, relevance=4, communication=4,
            evaluation="LedgerProbe 占位评价（非模型输出，仅用于账本接线验证）",
            covered_points=[], missing_points=[], factual_or_reasoning_issues=[],
            follow_up_focus=[], next_action_recommendation="NEXT", reference_answer=None,
            knowledge_observations=[dict(
                knowledge_id=knowledge_id, state="DEMONSTRATED_IN_SESSION",
                answer_quotes=[payload["answer"][:60]], missing_mechanisms=[], resolved_gap_ids=[])])
        return ModelResponse(json.dumps(value, ensure_ascii=False),
                             {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500})


class ReplayEvaluation(ScriptedEvaluation):
    """Replay that keeps the payload audit list of the live provider."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.payloads: list[dict] = []

    async def generate(self, messages, *, schema):
        self.payloads.append(_audit_payload(messages, schema))
        return await super().generate(messages, schema=schema)


# ---------------------------------------------------------------- evidence


async def read_submission(factory, session_id: int) -> dict:
    async with factory() as db:
        session = (await db.execute(text(
            "SELECT status, state_version, context_snapshot_id FROM interview_sessions WHERE id = :s"),
            {"s": session_id})).first()
        question = (await db.execute(text(
            "SELECT id, status, question, standard_answer FROM interview_session_questions "
            "WHERE session_id = :s ORDER BY question_index"), {"s": session_id})).first()
        answer = (await db.execute(text(
            "SELECT id, status, error, message_id FROM interview_answers WHERE session_id = :s"),
            {"s": session_id})).first()
        message = await db.scalar(text(
            "SELECT content FROM interview_messages WHERE id = :m"), {"m": answer[3]}) if answer else None
        rows = (await db.execute(text(
            "SELECT id, " + ", ".join(DIMENSIONS) + ", provider, model, prompt_version, structured_retry, "
            "token_usage_json, artifact_json, agent_run_id, answer_id "
            "FROM interview_evaluations WHERE session_id = :s ORDER BY id"), {"s": session_id})).fetchall()
        run = (await db.execute(text(
            "SELECT status, graph_name FROM experience_agent_runs WHERE id = :r"), {"r": rows[0][12]})) \
            .first() if rows else None
    evaluations = []
    for row in rows:
        evaluations.append(dict(
            evaluation_id=row[0],
            dims={key: row[1 + index] for index, key in enumerate(DIMENSIONS)},
            total=sum(row[1 + index] for index in range(5)),
            provider=row[6], model=row[7], prompt_version=row[8], structured_retry=row[9],
            token_usage=row[10], artifact=row[11], agent_run_id=row[12], answer_id=row[13]))
    return dict(
        session=dict(id=session_id, status=session[0], state_version=session[1],
                     context_snapshot_id=session[2]),
        question=dict(id=question[0], status=question[1], text=question[2],
                      rubric=question[3].splitlines()),
        answer=dict(id=answer[0], status=answer[1], error=answer[2], text=message) if answer else None,
        agent_run=dict(status=run[0], graph=run[1]) if run else None,
        evaluations=evaluations)


async def read_context(factory, session_id: int) -> dict:
    """Only the parts that must be identical for two answers to be comparable."""
    async with factory() as db:
        row = (await db.execute(text(
            "SELECT s.context_json FROM interview_context_snapshots s JOIN interview_sessions ss "
            "ON ss.context_snapshot_id = s.id WHERE ss.id = :s"), {"s": session_id})).first()
    context = row[0]
    blueprint = context.get("adaptive_blueprint") or {}
    config = context.get("interviewer_config") or {}
    skill = config.get("skill") or {}
    return dict(
        company_name=context.get("company_name"),
        job_title=context.get("job_title"),
        requirements=[[item.get("statement"), item.get("requirement_type")]
                      for item in context.get("requirements") or []],
        skill=[skill.get("id"), skill.get("version"), skill.get("content_fingerprint")],
        anchors=skill.get("evaluation_anchors"),
        round_id=(config.get("round") or {}).get("id"),
        style_id=(config.get("style") or {}).get("id"),
        targets=[[target.get("topic"), target.get("label"), target.get("category"),
                  target.get("priority"),
                  sorted(f"{source.get('source_type')}:{str(source.get('ref')).split(':')[0]}"
                         for source in target.get("sources") or [])]
                 for target in blueprint.get("targets") or []],
        blueprint_fingerprint=blueprint.get("fingerprint"))


async def read_ledger(factory, since: datetime) -> list[dict]:
    async with factory() as db:
        rows = (await db.execute(text(
            "SELECT call_key, owner_key, route, model, status, reserved_tokens, actual_tokens, "
            "charged_tokens, created_at, finished_at FROM model_admissions "
            "WHERE created_at >= :since ORDER BY created_at"), {"since": since})).fetchall()
    return [dict(call_key=row[0], owner_key=row[1], route=row[2], model=row[3], status=row[4],
                 reserved_tokens=row[5], actual_tokens=row[6], charged_tokens=row[7],
                 created_at=row[8].isoformat(), finished_at=row[9].isoformat() if row[9] else None,
                 latency_ms=round((row[9] - row[8]).total_seconds() * 1000) if row[9] else None)
            for row in rows]


# ---------------------------------------------------------------- run


async def run(args) -> dict:
    frozen = load_frozen()
    url = args.database_url
    name = make_url(url).database or ""
    if not name.startswith("interview_agent_wb_r25_"):
        raise SystemExit(f"拒绝执行：{name} 不是本轮 sandbox 数据库")
    # The gateway refuses to book an admission anywhere but the database this
    # process declares. Declare the sandbox explicitly; a run that forgets now
    # stops before the first request instead of billing DATABASE_URL.
    settings.model_ledger_database = name
    if expected_ledger_database() != name:
        raise SystemExit(f"拒绝执行：账本目标 {expected_ledger_database()!r} 与沙箱 {name!r} 不一致")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    started = datetime.now(timezone.utc)
    budget = Budget(args.http_cap, args.token_cap, args.seconds) if args.evaluation == "live" else None
    inner = None
    if args.evaluation == "live":
        connection = modelscope_connection()
        if connection is None:
            raise SystemExit("LLM_PROVIDER 不是 modelscope，无法执行真实评价")
        assert connection["model"] == AUTHORIZED_MODEL, connection["model"]
        inner = ModelScopeModelProvider(
            **connection, timeout_seconds=settings.interview_model_timeout_seconds,
            max_output_tokens=settings.interview_model_max_output_tokens,
            thinking_type=settings.answer_evaluation_thinking)
        inner.governed = True
    result = dict(
        mode=args.evaluation, database=name, started_at=started.isoformat(),
        model=AUTHORIZED_MODEL if args.evaluation != "replay" else "replay",
        provider="replay" if args.evaluation == "replay" else "modelscope",
        caps=dict(http=args.http_cap, tokens=args.token_cap, seconds=args.seconds) if budget else dict(http=0),
        pairs=[dict(pair_id=p["pair_id"], question_id=p["question_id"], topic=p["topic"],
                    contrast=p["contrast"], expected_relation=p["expected_relation"],
                    samples=[dict(sample_id=s["sample_id"], role=s["role"],
                                  quality_label=s["quality_label"], char_count=s["char_count"],
                                  expected_band=s["expected_band"]) for s in p["samples"]])
               for p in frozen["pairs"]])
    try:
        cases = {}
        for pair in frozen["pairs"]:
            cases[pair["pair_id"]] = await seed_case(factory, pair["jd_statements"], pair["jd_title"])
        submissions, runtimes, pair_checks = [], {}, []
        consecutive = 0
        stop_reason = None

        def caps_hit():
            if budget is None:
                return None
            if budget.stopped:
                return budget.stopped
            if budget.calls >= budget.http_cap:
                return "HTTP_CAP"
            if budget.tokens >= budget.token_cap:
                return "TOKEN_CAP"
            if budget.elapsed() >= budget.seconds:
                return "TIME_CAP"
            return None

        async def open_one(pair, sample):
            user_id = await seed_user(factory)
            if budget is None and args.evaluation == "ledger":
                evaluator = LedgerProbe()
            elif budget is None:
                evaluator = ReplayEvaluation(
                    dict(technical_correctness=4, coverage=4, depth=4, relevance=4, communication=4),
                    evaluation="Replay 评价（彩排，非模型输出）", covered=[], missing=[], issues=[])
            else:
                evaluator = GuardedEvaluation(inner, budget)
            core, session_id, question_id, main = await open_case(
                factory, cases[pair["pair_id"]], user_id=user_id, skill_id=pair["skill_id"],
                question=pair["question"], expected_points=pair["rubric"],
                dimension=pair["dimension"], topic=pair["topic"], evaluator=evaluator)
            return dict(sample=sample, user_id=user_id, evaluator=evaluator, core=core,
                        session_id=session_id, question_id=question_id, main=main,
                        answer_key=uuid4().hex,
                        db=await read_submission(factory, session_id),
                        context=await read_context(factory, session_id))

        async def submit_one(pair, runtime):
            nonlocal consecutive, stop_reason
            sample = runtime["sample"]
            # The service binds the scope per call (owner, route, ledger database),
            # so the harness no longer sets it. It only declares which database this
            # process is allowed to bill, which is checked before every request.
            calls_before = budget.calls if budget else 0
            tokens_before = budget.tokens if budget else 0
            clock = time.perf_counter()
            entry = dict(pair_id=pair["pair_id"], sample_id=sample["sample_id"], role=sample["role"],
                         quality_label=sample["quality_label"], char_count=sample["char_count"],
                         user_id=runtime["user_id"], session_id=runtime["session_id"],
                         question_id=runtime["question_id"],
                         answer_key=runtime["answer_key"], answer_text=sample["answer_text"],
                         expected_band=sample["expected_band"],
                         question_matches=runtime["db"]["question"]["text"] == pair["question"],
                         rubric_matches=runtime["db"]["question"]["rubric"] == pair["rubric"])
            try:
                events = await runtime["core"].answer(
                    runtime["session_id"], runtime["user_id"], runtime["question_id"],
                    runtime["answer_key"], sample["answer_text"], allow_unknown_retry=False)
            except Exception as exc:  # noqa: BLE001 - the run must record, not hide, a failure
                entry.update(status="SUBMIT_FAILED", error=type(exc).__name__,
                             error_code=getattr(exc, "code", None), error_message=str(exc)[:500],
                             http_calls=(budget.calls - calls_before) if budget else 0,
                             tokens=(budget.tokens - tokens_before) if budget else 0,
                             latency_ms=round((time.perf_counter() - clock) * 1000),
                             db=await read_submission(factory, runtime["session_id"]))
                submissions.append(entry)
                consecutive += 1
                if consecutive >= args.max_consecutive_failures:
                    stop_reason = "TWO_CONSECUTIVE_FAILURES"
                return
            consecutive = 0
            entry.update(status="SUBMITTED", event_names=[name for name, _ in events],
                         events_digest=_sha([[name, str(payload)] for name, payload in events]),
                         http_calls=(budget.calls - calls_before) if budget else 0,
                         tokens=(budget.tokens - tokens_before) if budget else 0,
                         latency_ms=round((time.perf_counter() - clock) * 1000),
                         main_calls=list(runtime["main"].calls),
                         payload_audit=list(runtime["evaluator"].payloads),
                         scope_owner_ok=all(audit.get("owner") == f"user:{runtime['user_id']}"
                                            for audit in runtime["evaluator"].payloads),
                         db=await read_submission(factory, runtime["session_id"]))
            submissions.append(entry)

        for pair in frozen["pairs"]:
            hit = stop_reason or caps_hit()
            if hit:
                stop_reason = hit
                break
            # Both sessions of a pair are opened *before* either answer is scored,
            # so an incomparable context can stop the comparison instead of being
            # papered over by two numbers that look like a valid pair.
            first, second = await open_one(pair, pair["samples"][0]), await open_one(pair, pair["samples"][1])
            same = first["context"] == second["context"]
            check = dict(
                pair_id=pair["pair_id"], comparable=same,
                diff_fields=[key for key in first["context"] if first["context"][key] != second["context"][key]],
                question_shared=first["db"]["question"]["text"] == second["db"]["question"]["text"],
                rubric_shared=first["db"]["question"]["rubric"] == second["db"]["question"]["rubric"],
                fingerprint=first["context"]["blueprint_fingerprint"])
            pair_checks.append(check)
            if not same:
                for runtime in (first, second):
                    submissions.append(dict(
                        pair_id=pair["pair_id"], sample_id=runtime["sample"]["sample_id"],
                        status="SKIPPED_CONTEXT_MISMATCH", user_id=runtime["user_id"],
                        session_id=runtime["session_id"], db=runtime["db"]))
                continue
            for runtime in (first, second):
                hit = stop_reason or caps_hit()
                if hit:
                    stop_reason = hit
                    submissions.append(dict(
                        pair_id=pair["pair_id"], sample_id=runtime["sample"]["sample_id"],
                        status="NOT_RUN", user_id=runtime["user_id"],
                        session_id=runtime["session_id"], db=runtime["db"]))
                    break
                await submit_one(pair, runtime)
                runtimes[runtime["sample"]["sample_id"]] = runtime
        result["submissions"] = submissions
        result["pair_checks"] = pair_checks
        result["stop_reason"] = stop_reason

        # Duplicate submission of the first successful answer must reuse the artifact.
        done = [entry for entry in submissions if entry["status"] == "SUBMITTED"]
        if done:
            first = done[0]
            runtime = runtimes[first["sample_id"]]
            before_calls = _call_count(budget, runtime["evaluator"])
            before = await read_submission(factory, runtime["session_id"])
            events = await runtime["core"].answer(
                runtime["session_id"], runtime["user_id"], runtime["question_id"],
                runtime["answer_key"], first["answer_text"], allow_unknown_retry=False)
            after_calls = _call_count(budget, runtime["evaluator"])
            after = await read_submission(factory, runtime["session_id"])
            result["idempotency"] = dict(
                sample_id=first["sample_id"], extra_http_calls=after_calls - before_calls,
                evaluations_before=len(before["evaluations"]),
                evaluations_after=len(after["evaluations"]),
                same_result=_sha([[name, str(payload)] for name, payload in events]) == first["events_digest"],
                event_names=[name for name, _ in events],
                answer_status=after["answer"]["status"], session_status=after["session"]["status"])

        result["ledger"] = await read_ledger(factory, started)
        result["totals"] = dict(
            http_calls=budget.calls if budget else 0,
            tokens=budget.tokens if budget else 0,
            elapsed_seconds=round(budget.elapsed() if budget else 0, 1),
            ledger_statuses={status: sum(1 for row in result["ledger"] if row["status"] == status)
                             for status in ("RESERVED", "SETTLED", "UNKNOWN")},
            ledger_tokens=sum((row["actual_tokens"] or 0) for row in result["ledger"]))
        result["provider_records"] = budget.records if budget else []
    finally:
        await engine.dispose()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("freeze", "run"), default="run")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--evaluation", choices=("replay", "ledger", "live"), default="replay")
    parser.add_argument("--out", default="")
    parser.add_argument("--http-cap", type=int, default=8)
    parser.add_argument("--token-cap", type=int, default=40000)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--max-consecutive-failures", type=int, default=2)
    args = parser.parse_args()
    if args.mode == "freeze":
        payload = freeze()
        print(json.dumps(dict(pairs=[dict(pair_id=p["pair_id"], question=p["question"][:40],
                                          samples=[(s["sample_id"], s["quality_label"], s["char_count"])
                                                   for s in p["samples"]])
                                    for p in payload["pairs"]], file=str(FREEZE)),
                         ensure_ascii=False, indent=2))
        return 0
    if not args.database_url:
        raise SystemExit("--database-url is required in run mode")
    result = asyncio.run(run(args))
    text_out = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
    print(json.dumps(dict(database=result["database"], mode=result["mode"],
                          http_calls=result["totals"]["http_calls"],
                          tokens=result["totals"]["tokens"],
                          stop_reason=result["stop_reason"],
                          submissions=[(e["sample_id"], e["status"],
                                        e["db"]["evaluations"][0]["dims"] if e["status"] == "SUBMITTED"
                                        and e["db"]["evaluations"] else None)
                                       for e in result["submissions"]]),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
