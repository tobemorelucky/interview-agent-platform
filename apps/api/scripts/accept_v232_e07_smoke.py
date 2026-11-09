"""One E07 Evaluation only. Explicit Replay parent, LIVE child, no migration.

This is a bounded acceptance wrapper, not a change to prompts/providers/domain.
Prepare requires an empty schema-only isolated DB; live refuses any prior attempt.
"""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import msvcrt
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accept_v23_live import API, ROOT, LiveBatch, BudgetStop, CALL, TARGET
from accept_v232_calibration import FixtureReplay, isolate
from prepare_v22_live import configured_routes
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from interview_api.core.config import settings
from interview_api.infrastructure.models.router import ModelRouter, build_interview_router
from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.adaptive import AdaptiveEvaluation, KnowledgeObservation, planning_payload, resolve_observations
from interview_api.modules.interview.agents import configured_prompt_registry
from interview_api.modules.interview.evaluation_repair import EvaluationQuotesRepair
from interview_api.modules.interview.skill_engine import agent_payload
from interview_api.modules.interview.context_projection import model_payload
from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation
from interview_api.modules.experience.models import ExperienceAgentStepRun
from interview_api.modules.users.models import User
from interview_api.modules.candidate_profile.evidence import digest
from v22_support import kafka_seed

LIMITS = dict(logical=1, http=2, tokens=30000, seconds=180)
HISTORY_DB = 'interview_agent_v232_calibration_20261028_a'


async def fingerprints():
    result = {}
    for name in ('interview_agent', HISTORY_DB):
        engine = create_async_engine(make_url(settings.database_url).set(database=name))
        try:
            async with engine.connect() as db:
                await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
                names = list((await db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))).all())
                fingerprint, count = hashlib.sha256(), 0
                for table in names:
                    rows = list((await db.scalars(text('SELECT row_to_json(t)::text FROM "' + table.replace('"','""') + '" t ORDER BY row_to_json(t)::text'))).all())
                    fingerprint.update(json.dumps([table, rows], ensure_ascii=False).encode())
                    count += len(rows)
                result[name] = dict(revision=await db.scalar(text('SELECT version_num FROM alembic_version')),
                    tables=len(names), rows=count, sha256=fingerprint.hexdigest())
        finally:
            await engine.dispose()
    assert result['interview_agent']['revision'] == '0025'
    return result


async def prepare(database, output):
    configured_routes()  # Read configuration only; not a model request.
    before = await fingerprints()
    retained = json.loads((ROOT/'.runtime/acceptance/v232_preparation_20261028_a.json').read_text(encoding='utf-8'))
    old = next(r for r in retained['cases'] if r['case']['id'] == 'E07')
    case = deepcopy(old['case'])
    factory = isolate(database)
    try:
        async with factory() as db:
            assert await db.scalar(text('SELECT version_num FROM alembic_version')) == '0026'
            assert await db.scalar(select(func.count()).select_from(User)) == 0, 'Dedicated EMPTY test DB required'
        uid, ctx = await kafka_seed(factory)
        replay = FixtureReplay(case)
        core = MockInterviewService(factory, ModelRouter({r:replay for r in ('interview_main','answer_evaluation')}))
        session = await core.create(uid, CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
            question_count=2, interviewer_skill_id='java_backend', adaptive_interview=True,
            title='DEMO E07 Replay parent + single LIVE Evaluation smoke'))
        opening = await core.start(session['id'], uid)
        events = await core.answer(session['id'], uid, opening['question_id'], 'EXPLICIT-REPLAY-E07-parent', case['parent_answer'])
        parent_aid = next(v['answer_id'] for k,v in events if k == 'evaluation')
        detail = await core.detail(session['id'], uid)
        question = detail['current_question']
        assert question['parent_question_id'] == opening['question_id'] and question['question'] == case['question']
        key = 'single-LIVE-E07-smoke'
        async with factory() as db:
            answer, expected, cached = await SessionController(db).prepare_answer(session['id'], uid, question['question_id'], key, case['answer'])
            assert not cached
            await db.commit()
            aid = answer.id
        state, context, questions, active, tokens = await core.read_state(session['id'], uid)
        assert tokens == 0 and context['budgets']['max_model_tokens'] == 200000
        packet = planning_payload(context, questions, active, state.interview_plan_json, answer=case['answer'], answer_id=aid)
        packet = await core.evaluation_context(session['id'], uid, packet, answer_id=aid)
        sc = packet['scoring_context']
        assert sc['parent_status'] == 'AVAILABLE' and sc['parent']['answer']['id'] == parent_aid
        assert sc['parent']['question']['content'] == case['parent_question'] and sc['parent']['answer']['content'] == case['parent_answer']
        assert sc['assistance_status'] == 'UNKNOWN'
        cleaned = agent_payload(packet, 'answer_evaluation')
        dto = model_payload(cleaned, 'answer_evaluation')
        prompt = configured_prompt_registry(cleaned).get('answer_evaluation')
        assert prompt.version == '2.3.2' and dto['answer'] == case['answer'] and dto['rubric'] == case['rubric']
        assert KnowledgeObservation.model_json_schema()['properties']['answer_quotes']['maxItems'] == 3
        # Compare the actual retained wire, not a newly authored replacement.
        old_dto = model_payload(agent_payload(old['new_packet'], 'answer_evaluation'), 'answer_evaluation')
        for field in ('answer', 'rubric'):
            assert dto[field] == old_dto[field]
        assert dto['current_question']['question'] == old_dto['current_question']['question']
        wire = [dict(role='system',content=prompt.system), dict(role='user',content=json.dumps(
            dict(input=dto,schema=AdaptiveEvaluation.model_json_schema()),ensure_ascii=False))]
        result = dict(database=database,user_id=uid,session_id=session['id'],answer_id=aid,question_id=question['question_id'],
            parent_question_id=opening['question_id'],parent_answer_id=parent_aid,expected=expected,key=key,
            original_session=dict(database=HISTORY_DB,session_id=old['session_id']),
            packet=packet,wire_fingerprint=digest(wire),wire_bytes=len(json.dumps(wire,ensure_ascii=False).encode()),
            snapshot=detail['context_snapshot'],snapshot_hash=detail['snapshot_fingerprint'],
            protected_before=before,semantics='Explicit Replay parent/Main/history; ONLY child Evaluation is LIVE',limits=LIMITS)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:result[k] for k in ('database','session_id','answer_id','parent_question_id','parent_answer_id','wire_bytes','wire_fingerprint')},ensure_ascii=False),flush=True)
    finally:
        await factory.kw['bind'].dispose()


class SmokeBatch(LiveBatch):
    limits = LIMITS

    def preflight(self, messages):
        """Run BEFORE provider/user admission so a stopped batch sends no HTTP."""
        self.check()
        if not self.records:
            if self.logical or digest(messages) != self.prep['wire_fingerprint']:
                raise BudgetStop('FIRST_CALL_OR_FROZEN_INPUT_MISMATCH')
            return False
        if len(self.records) != 1 or self.logical != 1 or not getattr(self,'repair_plan',None):
            raise BudgetStop('ONLY_ONE_QUOTE_COUNT_REPAIR_ALLOWED')
        repair = json.loads(messages[-1]['content'])
        if repair.get('reference_subset_repair') != self.repair_plan or json.loads(repair.get('invalid_output','null')) != self.records[0]['business_output']:
            raise BudgetStop('REPAIR_SCOPE_CHANGED')
        return True

    async def on_request(self, request):
        body = json.loads(request.content)
        if body['model'] != TARGET or digest(body['messages']) != self.authorized_wire:
            raise BudgetStop('HTTP_MODEL_OR_WIRE_MISMATCH')
        await super().on_request(request)
        self.records[-1].update(prompt_version='2.3.2',input_fingerprint=digest(body['messages']),
            system_fingerprint=digest(body['messages'][0]['content']))

    async def on_response(self, response):
        await super().on_response(response)
        record = self.records[-1]
        if response.status_code >= 400:
            return
        value = record.get('business_output')
        try:
            parsed = AdaptiveEvaluation.model_validate(value)
            resolve_observations(parsed, self.prep['packet'])
            record['initial_validation'] = 'PASSED'
        except Exception as exc:
            plan = EvaluationQuotesRepair(self.prep['packet']).prepare(value, exc)
            record['initial_validation'] = 'QUOTE_COUNT_ONLY' if plan and not record['repair'] else type(exc).__name__
            record['validation_errors'] = exc.errors(include_input=False, include_url=False, include_context=False) if hasattr(exc,'errors') else [dict(type=type(exc).__name__,code=getattr(exc,'code',None))]
            if plan and not record['repair']:
                self.repair_plan = plan
            else:
                self.stop_reason = 'INVALID_OUTPUT_NO_FURTHER_RETRY'
        self.save(status='HTTP_RETURNED')


class SmokeMeter:
    """Reuse the configured provider unchanged; meter normal and quote repair."""
    def __init__(self, inner, batch, client):
        self.inner, self.batch = inner, batch
        inner.client = client
        for key in ('provider_name','model_name','base_url','timeout','max_output_tokens','thinking_type','governed'):
            setattr(self,key,getattr(inner,key))

    async def generate(self, messages, *, schema):
        import httpx
        try:
            repair = self.batch.preflight(messages)
            body = dict(model=self.model_name,messages=messages,temperature=0,max_tokens=self.max_output_tokens,response_format={'type':'json_object'})
            if self.thinking_type is not None: body['thinking']={'type':self.thinking_type}
            estimate = len(httpx.Request('POST',self.base_url+'/chat/completions',json=body).content) + self.max_output_tokens + 1024
            if len(self.batch.records) >= 2 or self.batch.charged + self.batch.reserved + estimate > 30000:
                raise BudgetStop('HTTP_OR_TOKEN_BUDGET_PRE_SEND')
            remaining = self.batch.limits['seconds'] - (time.monotonic()-self.batch.start)
            if remaining <= 0: raise BudgetStop('BATCH_DEADLINE')
        except BaseException as exc:
            exc.request_sent = False
            raise
        if not repair: self.batch.logical += 1
        self.batch.authorized_wire = digest(messages)
        token = CALL.set(dict(logical=1,repair=repair,route='answer_evaluation',schema=schema['title']))
        try:
            return await asyncio.wait_for(self.inner.generate(messages,schema=schema),remaining)
        except BaseException:
            self.batch.finalize_unknown()
            self.batch.save(status='REQUEST_FAILED')
            raise
        finally:
            CALL.reset(token)


async def live(database, preparation, output):
    import httpx
    prep = json.loads(preparation.read_text(encoding='utf-8'))
    assert prep['database'] == database and prep['limits'] == LIMITS
    factory = isolate(database)
    batch = SmokeBatch(output)
    batch.prep, batch.phase = prep, 'E07_SINGLE_EVALUATION'
    sid, uid, aid, expected = (prep[k] for k in ('session_id','user_id','answer_id','expected'))
    try:
        async with asyncio.timeout(180):
            routes = configured_routes()
            config = build_interview_router()
            assert not config.fallback_routes and all(p.model_name == TARGET for p in config.routes.values())
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(ExperienceAgentStepRun).where(ExperienceAgentStepRun.model_name==TARGET)) == 0
                assert await db.scalar(select(func.count()).select_from(ModelAdmission)) == 0
            async with httpx.AsyncClient(event_hooks={'request':[batch.on_request],'response':[batch.on_response]}) as client:
                provider = SmokeMeter(config.primary('answer_evaluation'),batch,client)
                core = MockInterviewService(factory,ModelRouter({'answer_evaluation':provider},concurrency=config.concurrency))
                initial = await core.detail(sid,uid)
                assert initial['state_version'] == expected and initial['context_snapshot'] == prep['snapshot']
                state, context, _, _, tokens = await core.read_state(sid,uid)
                assert context['budgets']['max_model_tokens'] == 200000 and tokens == 0
                assert time.time() - state.started_at.timestamp() < context['budgets']['max_duration_seconds']
                packet = await core.evaluation_context(sid,uid,prep['packet'],answer_id=aid)
                assert packet == prep['packet']
                batch.save(status='READY_SINGLE_AUTHORIZED_BATCH',routes=routes)
                result = await core.call_agent(core.evaluator.adaptive(),sid,uid,expected,packet,answer_id=aid)
                assert result and result.prompt_version == '2.3.2' and result.usage_complete and not batch.stop_reason
                quotes = result.artifact.knowledge_observations[0].answer_quotes
                assert len(quotes) <= 3 and all(q in packet['answer'] for q in quotes)
                if len(batch.records) == 2:
                    audit = EvaluationQuotesRepair(packet).validate(batch.records[0]['business_output'],batch.records[1]['business_output'])
                    assert result.retry == 1
                else:
                    audit = dict(policy='NORMAL_PATH',live_repair_covered=False)
                    assert result.retry == 0
                async with factory() as db:
                    # Two-question original budget: parent + this child. The real
                    # Controller determines terminality; no LIVE Main is required.
                    events = await SessionController(db).commit_answer(sid,uid,aid,expected,result,None,'COMPLETE',{})
                    await db.commit()
                assert any(k == 'complete' for k,v in events)
                count = len(batch.records)
                reused = await core.call_agent(core.evaluator.adaptive(),sid,uid,expected,packet,answer_id=aid)
                assert reused.run_id == result.run_id and len(batch.records) == count
                assert await core.answer(sid,uid,prep['question_id'],prep['key'],packet['answer']) == events
                assert len(batch.records) == count
                final = await core.detail(sid,uid)
                _, _, _, _, session_tokens = await core.read_state(sid,uid)
                async with factory() as db:
                    admissions = list((await db.scalars(select(ModelAdmission))).all())
                    answer = await db.get(InterviewAnswer,aid)
                    evaluations = list((await db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.answer_id==aid))).all())
                    steps = list((await db.scalars(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.model_name==TARGET))).all())
                    assert len(admissions) == len(batch.records) and all(r.status=='SETTLED' and r.actual_tokens==r.charged_tokens for r in admissions)
                    assert sum(r.actual_tokens for r in admissions) == batch.charged == result.usage['total_tokens'] == session_tokens
                    assert len(evaluations)==len(steps)==1 and answer.status=='COMPLETED'
                    assert steps[0].token_usage_json == result.usage and steps[0].output_json['usage_complete']
                    assert final['state_version']==expected+1 and final['status']=='COMPLETED'
                    plan=final['interview_plan_json']
                    assert not plan['model_call_reservations'] and plan['reserved_model_tokens']==0 and plan.get('unreported_model_tokens',0)==0
                    assert not plan.get('answer_processing') and final['context_snapshot']==prep['snapshot']
                    batch.results.append(dict(session_id=sid,answer_id=aid,evaluation_id=evaluations[0].id,agent_run_id=result.run_id,
                        artifact=result.artifact.model_dump(),usage=result.usage,repair=result.retry,repair_audit=audit,
                        state_version=final['state_version'],answer_status=answer.status,session_status=final['status'],
                        session_tokens=session_tokens,admissions=len(admissions),idempotency=True,evaluation_reuse=True,
                        snapshot_unchanged=True,unknown_tokens=0))
            after = await fingerprints()
            assert after == prep['protected_before'], 'Protected formal/history DB changed; stop'
            batch.save(status='COMPLETED',protected_after=after)
            print(json.dumps(dict(status='COMPLETED',logical=batch.logical,http=len(batch.records),tokens=batch.charged,
                elapsed_seconds=round(time.monotonic()-batch.start,3),result=batch.results[-1]),ensure_ascii=False),flush=True)
    except BaseException as exc:
        batch.finalize_unknown()
        batch.stop_reason = batch.stop_reason or getattr(exc,'code',type(exc).__name__)
        async with factory() as db:
            answer = await db.get(InterviewAnswer,aid)
            if answer and answer.status=='RUNNING':
                await SessionController(db).failed_answer(sid,uid,aid,expected,batch.stop_reason)
                await db.commit()
        batch.save(status='STOPPED',failure_type=type(exc).__name__,known_usage=getattr(exc,'usage',{}),
                   usage_complete=getattr(exc,'usage_complete',False),protected_after=await fingerprints())
        print(json.dumps(dict(status='STOPPED',logical=batch.logical,http=len(batch.records),tokens=batch.charged,error=batch.stop_reason)),flush=True)
        raise
    finally:
        await factory.kw['bind'].dispose()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['prepare','live'])
    parser.add_argument('--database',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--preparation')
    args=parser.parse_args()
    assert args.database.startswith('interview_agent_v232_e07_smoke_') and args.database.replace('_','').isalnum()
    output=Path(args.output).resolve()
    assert output.is_relative_to((ROOT/'.runtime/acceptance').resolve()) and not output.exists()
    output.parent.mkdir(parents=True,exist_ok=True)
    if args.mode=='prepare':
        asyncio.run(prepare(args.database,output))
    else:
        with (output.parent/'v23_live.lock').open('a+b') as lock:
            if lock.tell()==0:lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            try:asyncio.run(live(args.database,Path(args.preparation),output))
            finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
