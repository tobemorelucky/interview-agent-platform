"""Single final LIVE acceptance, existing product/API, no production changes.

Only this isolated server process freezes a 550000-token Session budget. Batch
600000/50/60/5400 protects Main, Evaluation, repairs, Coach and a next opening.
"""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
from accept_v23_live import API,ROOT,LiveBatch,BudgetStop,CALL,TARGET,prepare as base_prepare
from prepare_v22_live import configured_routes
from sqlalchemy import select,func,text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from interview_api.core.config import settings
from interview_api.infrastructure.models.router import ModelRouter,build_interview_router,build_interview_coach_router
from interview_api.infrastructure.models.provider import ModelResponse
from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.models import InterviewSession,InterviewAnswer,InterviewEvaluation,InterviewContextSnapshot
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.modules.memory.capability_service import CapabilityMemoryService
from interview_api.modules.public_career.models import JobDescriptionVersion,JobRequirement
from interview_api.modules.experience.models import ExperienceAgentStepRun
from interview_api.modules.candidate_profile.evidence import digest
from v21_support import AdaptiveReplayProvider

LIMITS=dict(logical=50,http=60,tokens=600000,seconds=5400)
SESSION_TOKENS=550000


async def protected():
    """All old Live/calibration DBs plus formal DB; read-only business fingerprints."""
    admin=create_async_engine(make_url(settings.database_url).set(database='postgres'))
    async with admin.connect() as db:
        names=list((await db.scalars(text("SELECT datname FROM pg_database WHERE datname='interview_agent' OR (datname LIKE 'interview_agent_%' AND (datname LIKE '%live%' OR datname LIKE '%calibration%')) ORDER BY datname"))).all())
    await admin.dispose()
    result={}
    current=make_url(settings.database_url).database
    for name in names:
        if name==current and name.startswith('interview_agent_v21_v23_live_final_'): continue
        engine=create_async_engine(make_url(settings.database_url).set(database=name))
        try:
            async with engine.connect() as db:
                await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
                tables=list((await db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))).all())
                fingerprint=hashlib.sha256();count=0
                for table in tables:
                    rows=list((await db.scalars(text('SELECT row_to_json(t)::text FROM "'+table.replace('"','""')+'" t ORDER BY row_to_json(t)::text'))).all())
                    fingerprint.update(json.dumps([table,rows],ensure_ascii=False).encode());count+=len(rows)
                result[name]=dict(tables=len(tables),rows=count,sha256=fingerprint.hexdigest())
        finally: await engine.dispose()
    return result


def bad_basis(value):
    """Acceptance safety check for the SPECIFIC confirmed Outbox misconception.

    Never scans candidate answers as grading truth; never rewrites model output.
    No claim of general semantic validation.
    """
    if isinstance(value,dict):
        return [p for v in value.values() for p in bad_basis(v)]
    if isinstance(value,list):
        return [p for v in value for p in bad_basis(v)]
    if not isinstance(value,str): return []
    bad=[]
    for line in re.split(r'[\n。；]',value):
        if re.search(r'不能|不自动|无法|并不|不等于|不保证|错误|误区',line): continue
        if re.search(r'(?:[Oo]utbox|发件箱).{0,90}(?:保证|实现|确保).{0,50}(?:位点|offset).{0,30}(?:原子|atomic)',line):
            bad.append(line[:300])
    return bad


class ConflictHistory(AdaptiveReplayProvider):
    model_name='EXPLICIT_REPLAY_HISTORY_NOT_LIVE_OR_GOLD'
    good=True
    async def generate(self,messages,*,schema):
        p=json.loads(messages[-1]['content'])['input'];a=p['adaptive']
        if schema['title']=='AdaptiveSelection':
            c=next(c for c in a['candidates'] if c['target_knowledge_id']=='ai_agent_application:rag_retrieval' and c['source']['ref'].startswith('skill:') and 'NEXT' in c['strategies'])
            value=dict(candidate_id=c['candidate_id'],strategy='NEXT',supporting_refs=[c['source']['ref']],reason_tags=['OPENING'],
                proposed_question=dict(question='DEMO Replay历史：RAG混合检索如何按排名融合，并在无依据时处理回答？',
                    expected_points=['两路召回按排名融合','真实依据与无证据拒答'],dimension='rag_retrieval',difficulty=p['desired_difficulty']))
        else:
            value=dict(**{k:4 if self.good else 2 for k in ('technical_correctness','coverage','depth','relevance','communication')},
                evaluation='EXPLICIT Replay history, not Live or gold scoring.',covered_points=['排名融合'] if self.good else [],
                missing_points=[] if self.good else ['排名融合机制与无证据处理'],factual_or_reasoning_issues=[],
                follow_up_focus=[] if self.good else ['解释机制'],next_action_recommendation='NEXT',
                knowledge_observations=[dict(knowledge_id=a['active_knowledge_id'],state='DEMONSTRATED_IN_SESSION' if self.good else 'NEEDS_CLARIFICATION',
                    answer_quotes=[p['answer']],missing_mechanisms=[] if self.good else ['排名融合机制与无证据处理'],resolved_gap_ids=[])])
        return ModelResponse(json.dumps(value,ensure_ascii=False),dict(input_tokens=0,output_tokens=0,total_tokens=0))


async def prepare(database,output):
    routes=configured_routes()
    before=await protected()
    await base_prepare(database)  # Refuses existing DB; upgrades ONLY its fresh isolation DB.
    settings.database_url=make_url(settings.database_url).set(database=database).render_as_string(hide_password=False)
    os.environ.update(DATABASE_URL=settings.database_url,APP_ENV='test')
    engine=create_async_engine(settings.database_url);factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with factory() as db:
            jd=JobDescriptionVersion(job_id=1,version_number=2,raw_text='DEMO合成岗位：AI/Agent应用开发，RAG混合检索、工具Schema和权限、状态恢复、模型调用成本、Python异步服务、Kafka异步任务系统设计。不是实际公司JD。',publication_status='PUBLISHED',metadata_json={'fixture':True})
            db.add(jd);await db.flush()
            for statement in ('RAG检索融合与引用验证','Agent工具Schema、权限和状态管理','LLM结构化输出、调用预算和失败处理','Python异步服务工程','Kafka异步任务系统设计：业务事务和消费位点分别提交，使用幂等处理重复投递'):
                db.add(JobRequirement(job_description_version_id=jd.id,statement=statement,requirement_type='SKILL',importance='CORE'))
            await db.commit()
        replay=ConflictHistory();core=MockInterviewService(factory,ModelRouter({r:replay for r in ('interview_main','answer_evaluation')}))
        request=CreateSessionRequest(company_id=1,job_id=1,question_count=1,interviewer_skill_id='ai_agent_application',adaptive_interview=True,title='EXPLICIT REPLAY conflict history')
        history=[await core.create(1,request) for _ in range(3)]
        for i,s in enumerate(history):
            replay.good=i<2
            q=await core.start(s['id'],1)
            answer='两路按chunk ID保留排名，用RRF求和融合；只有真实检索依据才引用，无依据拒答。' if i<2 else '我直接相加两路原始分数，没有资料时让模型补全引用。'
            await core.answer(s['id'],1,q['question_id'],f'EXPLICIT-REPLAY-history-{i}',answer)
        async with factory() as db:
            memories=await CapabilityMemoryService(db).list(1)
            assert any(r['topic']=='rag_retrieval' and r['state']=='CONFLICTED' for r in memories)
            from interview_api.modules.interview.context import build_context
            preview,_,_=await build_context(db,1,CreateSessionRequest(company_id=1,job_id=1,question_count=20,interviewer_skill_id='ai_agent_application',adaptive_interview=True))
            assert not bad_basis([preview['requirements'],preview['public_questions'],preview['interviewer_config']])
            assert preview['adaptive_policy']['prompts']['interview_main']['version']=='2.3.1'
            assert preview['adaptive_policy']['prompts']['answer_evaluation']['version']=='2.3.2'
        data=dict(database=database,user_id=1,company_id=1,job_id=1,profile_version_id=1,routes=routes,
            replay_history_sessions=[s['id'] for s in history],memory_before=memories,protected_before=before,
            initial_source_audit='No confirmed Outbox/offset atomicity error found in selected fixed sources/rubric anchors',
            ai_skill_topics=preview['interviewer_config']['skill']['priority_topics'],limits=LIMITS,
            production_default_session_tokens=settings.interview_max_model_tokens,isolated_session_tokens=SESSION_TOKENS,
            semantics='Synthetic DEMO resume/JD + explicit Replay conflict history; upcoming interview Main/Evaluation/Coach are LIVE')
        output.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(json.dumps(dict(status='PREPARED_NO_LIVE',database=database,history=data['replay_history_sessions'],memory=[dict(topic=r['topic'],state=r['state']) for r in memories],source_audit=data['initial_source_audit']),ensure_ascii=False),flush=True)
    finally: await engine.dispose()


class FinalBatch(LiveBatch):
    limits=LIMITS
    primary=None
    secondary=None
    def save(self,**extra):
        super().save(primary_session_id=self.primary,secondary_session_id=self.secondary,rounds=self.results,**extra)


class FinalMeter:
    def __init__(self,inner,batch,route,client):
        self.inner,self.batch,self.route=inner,batch,route;inner.client=client
        for name in ('provider_name','model_name','base_url','timeout','max_output_tokens','thinking_type','governed'):
            setattr(self,name,getattr(inner,name))
    async def generate(self,messages,*,schema):
        import httpx
        try:
            self.batch.check();body=json.loads(messages[-1]['content']);repair='invalid_output' in body
            if not repair and self.batch.logical>=50: raise BudgetStop('LOGICAL_BUDGET')
            packet=body.get('input',{})
            # A candidate's wrong quoted answer is not a grading reference.
            state=packet.get('adaptive',{}).get('knowledge_state',{})
            gaps=[v.get('open_gaps',[]) for v in state.get('items',{}).values()]
            evaluation=packet.get('evaluation') or {}
            basis=[packet.get('rubric'),gaps,evaluation.get('missing_points'),evaluation.get('reference_answer'),packet.get('snapshot',{}).get('requirements')]
            if bad_basis(basis): raise BudgetStop('CONFIRMED_INCORRECT_SCORING_BASIS')
            request_body=dict(model=self.model_name,messages=messages,temperature=0,max_tokens=self.max_output_tokens,response_format={'type':'json_object'})
            if self.thinking_type is not None: request_body['thinking']={'type':self.thinking_type}
            reserve=len(httpx.Request('POST',self.base_url+'/chat/completions',json=request_body).content)+self.max_output_tokens+1024
            if len(self.batch.records)>=60 or self.batch.charged+self.batch.reserved+reserve>600000: raise BudgetStop('HTTP_OR_TOKEN_BUDGET')
            remaining=5400-(time.monotonic()-self.batch.start)
            if remaining<=0: raise BudgetStop('BATCH_DEADLINE')
        except BaseException as exc:
            exc.request_sent=False;raise
        if not repair: self.batch.logical+=1
        token=CALL.set(dict(logical=self.batch.logical,repair=repair,route=self.route,schema=schema['title']))
        try: return await asyncio.wait_for(self.inner.generate(messages,schema=schema),min(self.timeout,remaining))
        except BaseException:
            self.batch.finalize_unknown();self.batch.save(status='REQUEST_FAILED');raise
        finally: CALL.reset(token)


async def audit(core,batch,prep,*,stage):
    sid=batch.primary
    if sid is None: return
    detail=await core.detail(sid,prep['user_id'])
    _,_,_,_,tokens=await core.read_state(sid,prep['user_id'])
    async with core.factory() as db:
        evaluations=list((await db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id==sid).order_by(InterviewEvaluation.id))).all())
        answers=list((await db.scalars(select(InterviewAnswer).where(InterviewAnswer.session_id==sid).order_by(InterviewAnswer.id))).all())
        admissions=list((await db.scalars(select(ModelAdmission))).all())
        assert all(a.status=='SETTLED' and a.actual_tokens==a.charged_tokens for a in admissions)
        assert sum(a.actual_tokens for a in admissions)==batch.charged
        assert len({e.answer_id for e in evaluations})==len(evaluations) and len(evaluations)<=20
        memories=await CapabilityMemoryService(db).list(prep['user_id'])
        row=dict(stage=stage,session_id=sid,status=detail['status'],state_version=detail['state_version'],
            active_question_id=detail['active_question_id'],session_tokens=tokens,
            question_count=len(detail['questions']),answer_count=len(answers),evaluation_count=len(evaluations),
            questions=detail['questions'],messages=detail['messages'],
            evaluations=[dict(id=e.id,answer_id=e.answer_id,question_id=e.session_question_id,artifact=e.artifact_json,usage=e.token_usage_json) for e in evaluations],
            plan=detail['interview_plan_json'],snapshot=detail['context_snapshot'],memory=memories,
            quota=dict(http=len(admissions),actual=sum(a.actual_tokens for a in admissions)),
            average_tokens_per_evaluation=tokens/len(evaluations) if evaluations else None)
        if any(bad_basis([e.artifact_json.get('missing_points'),e.artifact_json.get('reference_answer')]) for e in evaluations):
            batch.stop_reason='CONFIRMED_INCORRECT_GENERATED_SCORING_BASIS'
        batch.results.append(row)
        checkpoint=batch.output.parent/'v23_final_checkpoint15_20261029_a.json'
        if len(evaluations)==15 and not checkpoint.exists():
            checkpoint.write_text(json.dumps(row,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    batch.save(status=stage)


def serve(database,preparation,output):
    import httpx
    import uvicorn
    settings.database_url=make_url(settings.database_url).set(database=database).render_as_string(hide_password=False)
    os.environ.update(DATABASE_URL=settings.database_url,APP_ENV='test')
    original_default=settings.interview_max_model_tokens
    assert original_default==200000
    settings.interview_max_model_tokens=SESSION_TOKENS  # Only this new isolation process.
    prep=json.loads(preparation.read_text(encoding='utf-8'));assert prep['database']==database
    configured_routes()
    from interview_api.main import app
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.interview import router as interviews
    from interview_api.modules.interview_report import router as reports
    batch=FinalBatch(output);batch.phase='PRODUCT_UI_OPENING'
    client=httpx.AsyncClient(event_hooks={'request':[batch.on_request],'response':[batch.on_response]})
    configs=(build_interview_router(),build_interview_coach_router())
    router=ModelRouter({r:FinalMeter(p,batch,r,client) for conf in configs for r,p in conf.routes.items()},concurrency=configs[0].concurrency)

    class AcceptanceCore(MockInterviewService):
        async def create(self,uid,request):
            batch.check()
            assert uid==prep['user_id'] and request.adaptive_interview
            if batch.primary is not None:
                assert (await self.detail(batch.primary,uid))['status']=='COMPLETED'
                assert batch.secondary is None, 'Only one memory-check Session is allowed'
                assert request.question_count<=3
            else: assert request.question_count==20
            result=await super().create(uid,request)
            if batch.primary is None: batch.primary=result['id']
            else: batch.secondary=result['id']
            assert result['context_snapshot']['budgets']['max_model_tokens']==SESSION_TOKENS
            assert not bad_basis([result['context_snapshot']['requirements'],result['context_snapshot']['public_questions'],result['context_snapshot']['interviewer_config']])
            batch.save(status='SESSION_CREATED')
            return result
        async def start(self,sid,uid):
            assert sid in (batch.primary,batch.secondary)
            batch.phase='OPENING' if sid==batch.primary else 'NEXT_SESSION_MEMORY_OPENING'
            try:
                result=await super().start(sid,uid)
                if sid==batch.primary: await audit(self,batch,prep,stage='OPENING_COMMITTED')
                else:
                    d=await self.detail(sid,uid)
                    batch.results.append(dict(stage='NEXT_SESSION_OPENING',session_id=sid,question=result,snapshot=d['context_snapshot']))
                    batch.save(status='NEXT_SESSION_OPENING_COMMITTED')
                return result
            except BaseException as exc:
                batch.stop_reason=batch.stop_reason or getattr(exc,'code',type(exc).__name__);batch.save(status='STOPPED');raise
        async def answer(self,sid,uid,qid,key,content,*args,**kwargs):
            assert sid==batch.primary
            batch.phase='PRIMARY_ANSWER'
            try:
                events=await super().answer(sid,uid,qid,key,content,*args,**kwargs)
                await audit(self,batch,prep,stage='ANSWER_COMMITTED')
                return events
            except BaseException as exc:
                batch.stop_reason=batch.stop_reason or getattr(exc,'code',type(exc).__name__)
                try: await audit(self,batch,prep,stage='STOPPED')
                except Exception: pass
                batch.save(status='STOPPED');raise

    core=AcceptanceCore(async_session_factory,router)
    interviews.build_core=lambda:core
    class AcceptanceReport(InterviewReportService):
        async def generate(self,sid,uid,*args,**kwargs):
            assert sid==batch.primary
            batch.phase='FINAL_COACH'
            try:
                result=await super().generate(sid,uid,*args,**kwargs)
                batch.results.append(dict(stage='COACH_RESULT',report=result))
                await audit(core,batch,prep,stage='COACH_COMMITTED')
                return result
            except BaseException as exc:
                batch.stop_reason=batch.stop_reason or getattr(exc,'code',type(exc).__name__);batch.save(status='STOPPED');raise
    reports.build_core=lambda:AcceptanceReport(async_session_factory,router)
    batch.save(status='READY_NO_MODEL_CALLS',production_default_session_tokens=original_default,isolated_session_tokens=SESSION_TOKENS)
    uvicorn.run(app,host='127.0.0.1',port=8007,log_level='warning',access_log=False)


def client_command(mode,preparation,output,content=None):
    """Print question-only view while simulating candidate; no hidden rubric/eval."""
    import httpx
    prep=json.loads(preparation.read_text(encoding='utf-8'))
    with httpx.Client(base_url='http://127.0.0.1:8007/api/v1',timeout=240) as client:
        response=client.post('/auth/login',json=dict(email='v23.live@example.com',password='DEMO-v23-live-only'));response.raise_for_status()
        client.headers['Authorization']='Bearer '+response.json()['data']['access_token']
        batch=json.loads(output.read_text(encoding='utf-8'))
        assert not batch['stop_reason'],batch['stop_reason']
        sid=batch['primary_session_id']
        if mode=='answer':
            detail=client.get(f'/interview/sessions/{sid}').json()['data']
            question=detail['current_question'];qid=question['question_id']
            response=client.post(f'/interview/sessions/{sid}/messages/stream',json=dict(content=content,question_id=qid,idempotency_key=f'FINAL-LIVE-Q-{qid}'))
            response.raise_for_status()
            if 'event: error' in response.text:
                print('PRODUCT_STREAM_REPORTED_ERROR; inspect persisted state, do NOT retry')
        elif mode=='coach':
            response=client.post(f'/interview-reports/sessions/{sid}/generate',json={});response.raise_for_status()
        elif mode=='next':
            response=client.post('/interview/sessions',json=dict(company_id=prep['company_id'],job_id=prep['job_id'],
                question_count=3,interviewer_skill_id='ai_agent_application',adaptive_interview=True,
                training_mode='FULL',training_request_key='FINAL-MEMORY-NEXT-ONLY',title='DEMO next Session memory check, opening only'))
            response.raise_for_status();sid=response.json()['data']['id']
            response=client.post(f'/interview/sessions/{sid}/start');response.raise_for_status()
        detail=client.get(f'/interview/sessions/{sid}');detail.raise_for_status();d=detail.json()['data']
        batch=json.loads(output.read_text(encoding='utf-8'))
        q=d.get('current_question')
        print(json.dumps(dict(session_id=sid,status=d['status'],state_version=d['state_version'],
            answered=sum(m['role']=='USER' for m in d['messages']),question=dict(id=q['question_id'],text=q['question']) if q else None,
            batch_http=batch['http_attempts'],batch_tokens=batch['conservative_charged_tokens'],stop_reason=batch['stop_reason']),ensure_ascii=False),flush=True)


async def inspect_final(database,preparation,output):
    """Read-only final evidence, also recheck all protected old databases."""
    prep=json.loads(preparation.read_text(encoding='utf-8'))
    batch=json.loads(output.read_text(encoding='utf-8'))
    settings.database_url=make_url(settings.database_url).set(database=database).render_as_string(hide_password=False)
    engine=create_async_engine(settings.database_url)
    result={'session_id':batch['primary_session_id']}
    try:
        async with engine.connect() as db:
            await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            result['alembic']=await db.scalar(text('SELECT version_num FROM alembic_version'))
            sid=batch['primary_session_id']
            for table in ('interview_sessions','interview_session_questions','interview_answers','interview_evaluations','interview_agent_calls','interview_context_snapshots'):
                key='id' if table=='interview_sessions' else 'session_id'
                condition='id=(SELECT context_snapshot_id FROM interview_sessions WHERE id=:sid)' if table=='interview_context_snapshots' else f'{key}=:sid'
                result[table]=list((await db.scalars(text(f'SELECT row_to_json(t) FROM {table} t WHERE {condition} ORDER BY id'),{'sid':sid})).all())
            runs=[r['agent_run_id'] for r in result['interview_agent_calls']]
            for table in ('experience_agent_runs','experience_agent_step_runs'):
                key='id' if table=='experience_agent_runs' else 'agent_run_id'
                result[table]=list((await db.scalars(text(f'SELECT row_to_json(t) FROM {table} t WHERE {key}=ANY(:ids) ORDER BY id'),{'ids':runs})).all())
            result['model_admissions']=list((await db.scalars(text('SELECT row_to_json(t) FROM model_admissions t ORDER BY created_at'))).all())
            names=list((await db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND (tablename LIKE '%capability%' OR tablename LIKE '%competency%' OR tablename LIKE '%report%' OR tablename LIKE '%knowledge_observation%')"))).all())
            for table in names:
                result[table]=list((await db.scalars(text(f'SELECT row_to_json(t) FROM {table} t'))).all())
        result['protected_after']=await protected()
        result['protected_unchanged']=result['protected_after']==prep['protected_before']
        from interview_api.modules.interview.adaptive import AdaptiveEvaluation,resolve_observations
        last=batch['http'][-1]
        payload=json.loads(last['request_messages'][-1]['content'])['input']
        try:
            resolve_observations(AdaptiveEvaluation.model_validate(last['business_output']),payload)
            result['same_output_offline_validation']='ACCEPTED'
        except Exception as exc:
            result['same_output_offline_validation']=dict(code=getattr(exc,'code',type(exc).__name__),message=str(exc))
        destination=output.with_name(output.stem+'_readonly_final.json')
        assert not destination.exists()
        destination.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(json.dumps(dict(protected_unchanged=result['protected_unchanged'],session=result['interview_sessions'],
            answers=[dict(id=r['id'],status=r['status'],error=r['error']) for r in result['interview_answers']],
            evaluation_count=len(result['interview_evaluations']),question_count=len(result['interview_session_questions']),
            admissions=[dict(route=r['route'],status=r['status'],actual=r['actual_tokens'],charged=r['charged_tokens']) for r in result['model_admissions']],
            offline_reproduction=result['same_output_offline_validation']),ensure_ascii=False,default=str))
    finally:await engine.dispose()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','serve','inspect','current','answer','coach','next'])
    p.add_argument('--database',required=True);p.add_argument('--output',required=True);p.add_argument('--preparation');p.add_argument('--content')
    args=p.parse_args();assert args.database.startswith('interview_agent_v21_v23_live_final_') and args.database.replace('_','').isalnum()
    output=Path(args.output).resolve();assert output.is_relative_to((ROOT/'.runtime/acceptance').resolve())
    if args.mode=='prepare':
        assert not output.exists();asyncio.run(prepare(args.database,output))
    elif args.mode=='inspect':asyncio.run(inspect_final(args.database,Path(args.preparation),output))
    elif args.mode=='serve':
        assert not output.exists()
        with (output.parent/'v23_live.lock').open('a+b') as lock:
            if lock.tell()==0:lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            try:serve(args.database,Path(args.preparation),output)
            finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
    else: client_command(args.mode,Path(args.preparation),output,args.content)
