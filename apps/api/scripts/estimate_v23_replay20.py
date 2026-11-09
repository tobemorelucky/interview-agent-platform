"""Guarded PostgreSQL Replay + historical usage calibration. No external calls."""
import asyncio
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import socket
from statistics import median, quantiles
import sys

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
HISTORY = ROOT / ".runtime/acceptance"
TEST_DB = "interview_agent_v232_reliability_20261029_a"
sys.path[:0] = [str(API / "src"), str(API / "tests")]


def allow(host, port):
    if host not in ("localhost", "127.0.0.1", "::1") or int(port) != 5432:
        raise PermissionError("OFFLINE_REPLAY_EGRESS_BLOCKED")


def install_guard():
    # Windows Selector avoids Proactor ConnectEx outside Python socket.connect.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    def audit(event, args):
        if event == "socket.getaddrinfo":
            allow(*args[:2])
        elif event in ("socket.connect", "socket.sendto"):
            addr = args[1]
            if not isinstance(addr, tuple):
                raise PermissionError("OFFLINE_REPLAY_EGRESS_BLOCKED")
            allow(*addr[:2])
        elif event in ("subprocess.Popen", "os.system"):
            raise PermissionError("OFFLINE_REPLAY_SUBPROCESS_BLOCKED")
    sys.addaudithook(audit)
    for attempt in (lambda: socket.getaddrinfo("external.invalid", 443),
                    lambda: socket.create_connection(("192.0.2.1", 443))):
        try:
            attempt()
        except PermissionError:
            continue
        raise AssertionError("Outbound guard self-check failed")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def measures(messages):
    from compare_v231_context import measures as measure
    return measure("\n".join(m["content"] for m in messages))


MAIN, EVAL, COACH = "interview_main", "answer_evaluation", "interview_coach"


def historical():
    files = (
        "v23_live_20261026_a.json", "v23_short_live_20261026_a.json",
        "v231_ab_live_20261027_b.json", "v232_live_20261028_a.json",
        "v232_e07_live_20261029_a.json", "v23_final_live_20261029_a.json",
    )
    rows = []
    for f in files:
        for r in read(HISTORY / f)["http"]:
            messages = r["request_messages"]
            body = json.loads(messages[-1]["content"])
            p = body.get("input", {})
            sc = p.get("scoring_context") or {}
            ver = r.get("prompt_version")
            if not ver and f.startswith("v23_final"):
                ver = "2.3.1" if r["route"] == MAIN else "2.3.2"
            if not ver and f in files[:2]:
                ver = ("2.2.0" if f == files[0] else "2.2.1") if r["route"] == MAIN else "2.1.0"
            final_error = f.startswith("v23_final") and r["number"] == 4
            eligible = (not r["repair"] and (r["route"], ver) in ((MAIN, "2.3.1"), (EVAL, "2.3.2"))
                        and (r["route"] != EVAL or sc.get("version") == "FOLLOWUP_SCORING_V1"))
            size, lex = measures(messages)
            rows.append(dict(source=f, number=r["number"], route=r["route"], prompt_version=ver,
                phase=r.get("phase"), repair=r["repair"], http_status=r["http_status"],
                input_tokens=r["input_tokens"], output_tokens=r["output_tokens"], utf8=size, lexical=lex,
                history_questions=len(p["history"]) if "history" in p else None,
                question_id=(p.get("current_question") or {}).get("question_id"),
                parent=sc.get("parent_status"), wire_sha256=hashlib.sha256(dumps(messages).encode()).hexdigest(),
                business_status="UNRESOLVED_KNOWLEDGE_GAP" if final_error else
                    r.get("business_validation", r.get("initial_validation", "SEE_SOURCE")),
                eligible=eligible, output_eligible=eligible and not final_error and
                    not (f == "v232_live_20261028_a.json" and r["number"] == 8)))
    return rows


def predictor(rows):
    def predict(b, l):
        return (b * median(r["input_tokens"] / r["utf8"] for r in rows)
                + l * median(r["input_tokens"] / r["lexical"] for r in rows)) / 2
    return predict


def fit(rows):
    models, metrics = {}, {}
    for route in (MAIN, EVAL):
        train = [r for r in rows if r["route"] == route and r["eligible"]]
        assert len(train) >= 3, (route, len(train))
        outputs = [r["output_tokens"] for r in train if r["output_eligible"]]
        q = quantiles(outputs, n=4, method="inclusive")
        errs = []
        for i, r in enumerate(train):
            pred = predictor(train[:i]+train[i+1:])(r["utf8"], r["lexical"])
            e = pred / r["input_tokens"] - 1
            errs.append(e)
            r.update(loo_prediction=round(pred), loo_signed_error=e)
        m = dict(n=len(train), output_n=len(outputs), output_quartiles=q,
                 loo_mape=sum(map(abs, errs))/len(errs), loo_max=max(map(abs, errs)))
        metrics[route] = m
        models[route] = predictor(train)
    return models, metrics


async def replay_failure():
    from interview_api.core.exceptions import AppError
    from interview_api.infrastructure.models.provider import ReplayModelProvider
    from interview_api.infrastructure.models.router import ModelRouter
    from interview_api.infrastructure.models.structured import generate_structured
    from interview_api.modules.interview.adaptive import AdaptiveEvaluation, resolve_observations
    r = read(HISTORY / "v23_final_live_20261029_a.json")["http"][3]
    msgs = r["request_messages"]
    p = json.loads(msgs[-1]["content"])["input"]
    result = await generate_structured(ModelRouter({EVAL: ReplayModelProvider([r["business_output"]])}),
        EVAL, system=msgs[0]["content"], payload=p, response_model=AdaptiveEvaluation)
    try:
        resolve_observations(result.parsed, p)
    except AppError as exc:
        assert exc.code == "UNRESOLVED_KNOWLEDGE_GAP"
        return exc.code
    raise AssertionError("Original failure not reproduced")


async def run_session(factory, horizon, capture):
    from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
    from interview_api.infrastructure.models.router import ModelRouter
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview.schemas import CreateSessionRequest
    from interview_api.modules.interview_report.service import InterviewReportService
    from interview_api.modules.memory.capability_service import CapabilityMemoryService
    from interview_api.modules.public_career.models import Job, JobDescriptionVersion, JobRequirement
    from test_m4_interview import seed
    scenarios = read(Path(__file__).with_name("replay20_scenarios.json"))
    phase = "interview"
    used_rows = []

    class Fixture(ModelProvider):
        provider_name, model_name = "replay", "OFFLINE_COST_REPLAY_FIXTURE"

        async def generate(self, messages, *, schema):
            body = json.loads(messages[-1]["content"])
            p = body["input"]
            a = p.get("adaptive", {})
            sc = p.get("scoring_context") or {}
            state = a.get("knowledge_state", {})
            target = a.get("active_knowledge_id")
            gaps = state.get("items", {}).get(target, {}).get("open_gaps", [])
            title = schema["title"]
            if title == "AdaptiveSelection":
                route, n = MAIN, len(p["history"]) + 1
            elif title == "AdaptiveEvaluation":
                route, n = EVAL, len([r for r in used_rows if r["route"] == EVAL]) + 1
            else:
                assert title == "CoachArtifact", title
                route, n = COACH, horizon
            b, l = measures(messages)
            # Counts not present in the role projection remain null here. The
            # authoritative pre-answer state is joined separately below.
            row = dict(mode="REPLAY", horizon=horizon, phase=phase, round=n, route=route,
                version={MAIN:"2.3.1", EVAL:"2.3.2", COACH:"1.0.3"}[route],
                projection="ROLE_CONTEXT_V1" if route != COACH else None, scoring_context=sc.get("version"),
                wire_sha256=hashlib.sha256(dumps(messages).encode()).hexdigest(), utf8=b, lexical=l,
                candidate_count=len(a["candidates"]) if "candidates" in a else None,
                observation_count=len(state["observations"]) if "observations" in state else None,
                target_gap_count=len(gaps) if route == EVAL else None,
                parent_chars=sum(len(v["content"]) for v in (sc.get("parent") or {}).values()),
                blocks={k: len(dumps(v).encode()) for k, v in p.items() if k != "adaptive"},
                adaptive_blocks={k: len(dumps(v).encode()) for k,v in a.items()},
                messages=messages)
            capture.append(row)
            used_rows.append(row)
            if route == MAIN:
                topic, q, _, _, _, _, rubric = scenarios[n-1] if phase == "interview" else scenarios[0]
                action = "FOLLOW_UP" if phase == "interview" and n in (2,4,6,8,10,13,15,18) else "NEXT"
                matches = [c for c in a["candidates"] if c["target_knowledge_id"].endswith(":"+topic)
                           and action in c["strategies"] and c["source"]["ref"].startswith("skill:")]
                if not matches and phase != "interview":
                    matches = [c for c in a["candidates"] if c["source"]["ref"].startswith("skill:") and "NEXT" in c["strategies"]]
                    topic = matches[0]["target_knowledge_id"].rsplit(":", 1)[-1]
                    q = f"复测 {a['targets'][matches[0]['target_knowledge_id']]['label']}：请解释关键机制。"
                assert matches, ("fixture strategy unavailable", n, topic, action, a["candidates"])
                pair = matches[0]
                refs = [pair["source"]["ref"]]
                if a.get("current_answer_ref"):
                    refs.append(a["current_answer_ref"])
                value = dict(candidate_id=pair["candidate_id"], strategy=action, supporting_refs=refs,
                    reason_tags=["MISSING_MECHANISM" if action == "FOLLOW_UP" else "COVERAGE_SWITCH" if n > 1 else "OPENING"],
                    proposed_question=dict(question=q, expected_points=rubric, dimension=topic, difficulty=p["desired_difficulty"]))
            elif route == EVAL:
                topic, _, answer, missing, status, closes, rubric = scenarios[n-1]
                assert answer == p["answer"] and target.endswith(":"+topic) and p["rubric"] == rubric
                new = missing.split("；") if missing else []
                ids = [g["id"] for g in gaps if g["text"] in closes]
                assert len(ids) == len(closes), (n, gaps, closes)
                scores = {"NEEDS_CLARIFICATION": (2,3,2,5,4),
                          "PARTIALLY_DEMONSTRATED": (4,3,3,5,4),
                          "DEMONSTRATED_IN_SESSION": (5,5,4,5,5)}[status]
                value = dict(zip(("technical_correctness","coverage","depth","relevance","communication"),scores),
                    evaluation="固定回放：已核对本题所述机制，保留尚未验证的边界。",
                    covered_points=[rubric[0]], missing_points=new, factual_or_reasoning_issues=[],
                    follow_up_focus=new, next_action_recommendation="NEXT",
                    knowledge_observations=[dict(knowledge_id=target, state=status, answer_quotes=[answer],
                        missing_mechanisms=new, resolved_gap_ids=ids)])
            else:
                evidence = f"evaluation:{p['evaluations'][0]['id']}"
                value = dict(overall_summary="固定回放：候选人解释了检索与工具执行边界，仍可复盘故障窗口。",
                    strengths=[], weaknesses=[dict(statement="初答的原始分数融合没有考虑尺度差异。",
                        supporting_evaluation_ids=[evidence])], recurring_issues=[], job_alignment=[],
                    recommended_actions=["复盘检索融合与异常边界"], practice_plan=["补做故障恢复演练"], memory_proposals=[])
            row["fixture_output"] = value
            # Nonzero synthetic usage exercises settled audit accumulation. No
            # ModelAdmission/HTTP ledger exists for a Replay provider.
            return ModelResponse(dumps(value), dict(input_tokens=90, output_tokens=10, total_tokens=100))

    _,_,_,uid,seeded = await seed(factory, public=False)
    ids = {k: seeded["context_snapshot"][k] for k in ("company_id","job_id")}
    async with factory() as db:
        job = await db.get(Job, ids["job_id"])
        job.canonical_title = "DEMO AI Agent 应用开发工程师"
        jd = JobDescriptionVersion(job_id=job.id, version_number=2, raw_text="合成岗位：RAG、工具调用、Python 异步与故障恢复。",
            publication_status="PUBLISHED")
        db.add(jd)
        await db.flush()
        for req in ("RAG检索与重排", "Agent工具调用及状态", "大模型结构化输出", "Python异步",
                    "系统设计：Kafka重投递和Redis缓存故障恢复"):
            db.add(JobRequirement(job_description_version_id=jd.id,statement=req,requirement_type="SKILL"))
        await db.commit()
    router = ModelRouter({r:Fixture() for r in (MAIN,EVAL,COACH)})
    core = MockInterviewService(factory, router)
    req = dict(**ids, interviewer_skill_id="ai_agent_application", adaptive_interview=True)
    created = await core.create(uid, CreateSessionRequest(**req, question_count=horizon))
    sid = created["id"]
    outcome = dict(mode="REPLAY", horizon=horizon, session_id=sid, rounds=[])
    async def detail():
        return await core.detail(sid, uid)
    try:
        await core.start(sid, uid)
        for n, scenario in enumerate(scenarios[:horizon], 1):
            d = await detail()
            if d["status"] == "COMPLETED":
                break
            q = d["current_question"]
            prior = d["interview_plan_json"].get("adaptive_knowledge", {}).get("observations", [])
            key = f"offline-replay-{n}"
            events = await core.answer(sid,uid,q["question_id"],key,scenario[2])
            size = len(used_rows)
            assert events == await core.answer(sid,uid,q["question_id"],key,scenario[2])
            assert len(used_rows) == size
            evaluation_wire = next(r for r in reversed(used_rows) if r["route"] == EVAL)
            evaluation_wire["server_prior_observations"] = len(prior)
            d = await detail()
            assert d["context_snapshot"] == created["context_snapshot"]
            ledger = d["interview_plan_json"]["adaptive_knowledge"]
            assert len(ledger["observations"]) == n
            assert d["state_version"] == 1+2*n
            outcome["rounds"].append(dict(round=n, state_version=d["state_version"], status=d["status"],
                knowledge_id=ledger["observations"][-1]["knowledge_id"], gaps=ledger["observations"][-1]["open_gaps"]))
        d = await detail()
        outcome["status"] = d["status"]
        _, _, _, _, tokens = await core.read_state(sid,uid)
        plan = d["interview_plan_json"]
        assert not plan["model_call_reservations"] and not plan["unreported_model_tokens"]
        assert tokens == 100 * len(used_rows)
        outcome["synthetic_settled_usage"] = tokens
        outcome["terminal"] = (await core.read_state(sid,uid))[0].completion_json
        if d["status"] == "COMPLETED":
            reports = InterviewReportService(factory,router)
            result = await reports.generate(sid,uid)
            assert result["generation_status"] == "COMPLETED", result
            assert (await reports.generate(sid,uid))["reused"]
            assert result["producer"] == "REPLAY_FIXTURE"
            findings = result["coach_analysis"]["weaknesses"]
            assert len(findings) == 1 and len(findings[0]["supporting_evaluations"]) == 1
            assert outcome["terminal"]["evaluated_count"] == len(outcome["rounds"])
            outcome["coach"] = result
            async with factory() as db:
                outcome["memory"] = await CapabilityMemoryService(db).list(uid)
            next_session = await core.create(uid,CreateSessionRequest(**req,question_count=horizon))
            outcome["next_memory_guided_plan"] = next_session["context_snapshot"]["memory_guided_plan"]
            assert outcome["memory"] and outcome["next_memory_guided_plan"]["items"]
            # Do not start this next session; its model usage is not required.
    except Exception as exc:
        import traceback
        outcome["failure"] = dict(code=getattr(exc,"code",type(exc).__name__),detail=str(exc),trace=traceback.format_exc())
        outcome["status"] = (await detail())["status"]
    from sqlalchemy import select
    from interview_api.modules.interview.models import InterviewAgentCall
    from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
    async with factory() as db:
        audited = (await db.execute(select(ExperienceAgentRun,ExperienceAgentStepRun)
            .join(InterviewAgentCall,InterviewAgentCall.agent_run_id==ExperienceAgentRun.id)
            .join(ExperienceAgentStepRun,ExperienceAgentStepRun.agent_run_id==ExperienceAgentRun.id)
            .where(InterviewAgentCall.session_id==sid).order_by(ExperienceAgentRun.id))).all()
    for row,(run,step) in zip((r for r in used_rows if r["route"] != COACH),audited):
        row.update(server_input_hash=run.input_hash,version=step.prompt_version,
                   run_status=run.status,synthetic_usage=step.token_usage_json)
    for row in (r for r in used_rows if r["route"]==COACH and outcome.get("coach")):
        row.update(server_input_hash=json.loads(row["messages"][-1]["content"])["input"]["evaluation_fingerprint"],
                   version=outcome["coach"]["prompt_version"])
    return outcome


async def execute(out):
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from interview_api.core.config import settings
    from interview_api.infrastructure.models.provider import CompatibleModelProvider
    async def forbidden(*a, **kw):
        raise PermissionError("OFFLINE_REPLAY_LIVE_PROVIDER_BLOCKED")
    CompatibleModelProvider.generate = forbidden
    url = make_url(settings.database_url).set(database=TEST_DB)
    allow(url.host or "localhost", url.port or 5432)
    url = url.set(host="127.0.0.1", port=5432)
    os.environ["DATABASE_URL"] = settings.database_url = url.render_as_string(hide_password=False)
    failure = await replay_failure()
    rows = historical()
    _, metrics = fit(rows)
    engine = create_async_engine(url)
    capture, sessions = [], []
    try:
        async with engine.connect() as db:
            assert await db.scalar(text("SELECT current_database()")) == TEST_DB
            assert await db.scalar(text("SELECT version_num FROM alembic_version")) == "0026"
        factory = async_sessionmaker(engine,expire_on_commit=False)
        for horizon in (20,15):
            result = await run_session(factory,horizon,capture)
            sessions.append(result)
            print(dumps(dict(horizon=horizon, rounds=len(result["rounds"]),status=result["status"],
                failure=result.get("failure"),coach=bool(result.get("coach")))),flush=True)
    finally:
        await engine.dispose()
        (out/"capture.json").write_text(dumps(dict(mode="REPLAY", failed_original=failure,
            database=TEST_DB, calibrations=metrics, historical=rows, sessions=sessions, calls=capture)),encoding="utf-8")


async def repair_wire(row):
    """Exercise the actual bounded repair envelope, not the historical full input."""
    from copy import deepcopy
    from interview_api.infrastructure.models.provider import ModelResponse, ReplayModelProvider
    from interview_api.infrastructure.models.router import ModelRouter
    from interview_api.infrastructure.models.structured import generate_structured
    from interview_api.modules.interview.adaptive import AdaptiveSelection, AdaptiveEvaluation
    from interview_api.modules.interview.director_repair import SupportingRefsRepair
    from interview_api.modules.interview.evaluation_repair import EvaluationQuotesRepair
    route = row["route"]
    messages = row["messages"]
    p = json.loads(messages[-1]["content"])["input"]
    good = row["fixture_output"]
    bad = deepcopy(good)
    if route == MAIN:
        refs = p["adaptive"]["allowed_refs"]
        bad["supporting_refs"] = list(dict.fromkeys(good["supporting_refs"]+refs))[:13]
        assert len(bad["supporting_refs"]) == 13
        p["snapshot"]["adaptive_policy"] = {"decision_contract":"TARGET_SOURCE_PAIR_V1"}
        schema, policy = AdaptiveSelection, SupportingRefsRepair(p)
    else:
        quotes = bad["knowledge_observations"][0]["answer_quotes"]
        answer = p["answer"]
        quotes.extend([answer[0:10],answer[10:20],answer[20:30]])
        schema, policy = AdaptiveEvaluation, EvaluationQuotesRepair(p)
    seen = []
    class CaptureRepair(ReplayModelProvider):
        async def generate(self, messages, *, schema):
            seen.append(messages)
            return await super().generate(messages,schema=schema)
    result = await generate_structured(ModelRouter({route:CaptureRepair([bad,good])}),route,
        system=messages[0]["content"], payload=p,response_model=schema,repair_policy=policy)
    assert result.structured_retry == 1 and len(seen)==2
    return seen[1]


def csv_rows(path, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w",encoding="utf-8-sig",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


async def estimate(out):
    from interview_api.core.config import settings
    data=read(out/"capture.json")
    models, metrics=fit(data["historical"])
    csv_rows(out/"historical_live_usage.csv",data["historical"])
    estimates, repairs, summary, checkpoints = [],[],[],[]
    repair_schedule = {(MAIN,5):"neutral",(MAIN,10):"high",(EVAL,15):"neutral",(EVAL,20):"high"}
    for row in data["calls"]:
        if row["phase"]!="interview":
            continue
        r = {k:v for k,v in row.items() if k not in ("messages","fixture_output","blocks","adaptive_blocks","synthetic_usage")}
        role = r["route"]
        if role in models:
            estimate = models[role](r["utf8"],r["lexical"])
            low = max(.15,metrics[role]["loo_max"])
            high = max(.25,metrics[role]["loo_max"])
            outputs = metrics[role]["output_quartiles"]
            body = json.loads(row["messages"][-1]["content"])
            r["live_style_admission_reserve"] = (len(json.dumps(body["input"],ensure_ascii=False).encode())
                + len(row["messages"][0]["content"].encode()) + len(json.dumps(body["schema"]).encode())
                +2*(settings.interview_model_max_output_tokens+2048))
        else:
            estimate = sum(model(r["utf8"],r["lexical"]) for model in models.values())/2
            low,high = .25,.35
            outputs = (600,1500,settings.interview_coach_max_output_tokens)
        r["mode"]="ESTIMATED"
        for level,mult,output in zip(("low","neutral","high"),(1-low,1,1+high),outputs):
            r[level+"_input"]=round(estimate*mult)
            r[level+"_output"]=round(output)
        estimates.append(r)
        case = repair_schedule.get((role,r["round"]))
        if case:
            msgs = await repair_wire(row)
            b,l = measures(msgs)
            cost = models[role](b,l)
            rep = dict(mode="ESTIMATED",horizon=r["horizon"],round=r["round"],route=role,
                minimum_scenario=case,utf8=b,lexical=l,neutral_input=round(cost),high_input=round(cost*1.5),
                neutral_output=r["neutral_output"],high_output=min(settings.interview_model_max_output_tokens,
                    2*r["high_output"]), messages=msgs)
            repairs.append(rep)
    csv_rows(out/"per_request_estimated.csv",estimates)
    (out/"repair_envelopes.json").write_text(dumps(repairs),encoding="utf-8")
    for horizon in (15,20):
        base=[r for r in estimates if r["horizon"]==horizon]
        for level in ("low","neutral","high"):
            accum=0
            first_usage, first_admission=None,None
            rep_count=rep_tokens=0
            totals = {route:[0,0] for route in (MAIN,EVAL,COACH)}
            for row in base:
                role,n=row["route"],row["round"]
                inp,output=row[level+"_input"],row[level+"_output"]
                totals[role][0]+=inp
                totals[role][1]+=output
                if role==COACH:
                    continue
                if first_admission is None and accum+row["live_style_admission_reserve"]>200000:
                    first_admission=f"{n}:{role}"
                accum+=inp+output
                for rep in repairs:
                    if (rep["horizon"],rep["round"],rep["route"])!=(horizon,n,role):
                        continue
                    if level=="low" or (level=="neutral" and rep["minimum_scenario"]=="high"):
                        continue
                    delta=rep[level+"_input"]+rep[level+"_output"]
                    accum+=delta
                    rep_count+=1
                    rep_tokens+=delta
                if first_usage is None and accum>=200000:
                    first_usage=f"{n}:{role}"
                if role==EVAL and n%5==0:
                    checkpoints.append(dict(mode="ESTIMATED",horizon=horizon,scenario=level,round=n,
                        session_cumulative=accum,after_terminal_coach=accum+sum(
                            t[level+"_input"]+t[level+"_output"] for t in base if t["route"]==COACH) if n==horizon else None))
            summary.append(dict(mode="ESTIMATED",horizon=horizon,scenario=level,
                main_input=totals[MAIN][0],main_output=totals[MAIN][1],
                evaluation_input=totals[EVAL][0],evaluation_output=totals[EVAL][1],
                coach_input=totals[COACH][0],coach_output=totals[COACH][1],
                repair_requests=rep_count,repair_tokens=rep_tokens,session=accum,batch=accum+sum(totals[COACH]),
                first_usage_200k=first_usage,first_admission_200k=first_admission))
    csv_rows(out/"estimated_budget.csv",summary)
    csv_rows(out/"estimated_checkpoints.csv",checkpoints)
    print(dumps(summary))


if __name__ == "__main__":
    # Construct the Windows loop's private loopback wakeup socket before
    # lockdown, before loading app configuration or any provider.
    loop = asyncio.SelectorEventLoop()
    install_guard()
    out = HISTORY / sys.argv[1]
    assert out.parent == HISTORY and out.name.startswith("replay20_")
    with asyncio.Runner(loop_factory=lambda: loop) as runner:
        if "--estimate" in sys.argv[2:]:
            runner.run(estimate(out))
        else:
            out.mkdir(exist_ok=False)
            runner.run(execute(out))
