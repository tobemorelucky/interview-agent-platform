"""Single authorized 8-Evaluation LIVE batch; explicit Replay parents, real Controller.

No business code/config changes. Old arm is a 2.3.1 Agent-input counterfactual;
only 2.3.2 commits to InterviewEvaluation. Refuse output/DB overwrite.
"""
import argparse
import asyncio
from copy import deepcopy
import json
import msvcrt
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accept_v23_live import API, ROOT, LiveBatch, Meter, BudgetStop, TARGET, prepare as base_prepare
from prepare_v22_live import configured_routes
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.core.config import settings
from interview_api.infrastructure.models.provider import ModelResponse
from interview_api.infrastructure.models.router import ModelRouter, build_interview_router
from interview_api.infrastructure.models.governance_models import ModelAdmission
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.controller import SessionController
from interview_api.modules.interview.adaptive import planning_payload, AdaptiveEvaluation
from interview_api.modules.interview.adaptive_prompts import freeze_prompts
from interview_api.modules.interview.skill_engine import agent_payload
from interview_api.modules.interview.context_projection import model_payload
from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation, InterviewSessionQuestion, InterviewSession
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.public_career.models import Job, JobDescriptionVersion, JobRequirement
from v21_support import AdaptiveReplayProvider
from v22_support import kafka_seed

LIMITS = dict(logical=8, http=10, tokens=160000, seconds=720)


def scenarios():
    cases = json.loads((API/'tests/fixtures/evaluation_calibration_v1.json').read_text(encoding='utf-8'))['samples']
    result = []
    for identifier in ('E03', 'E12', 'E02', 'E07'):
        c = deepcopy(next(c for c in cases if c['id'] == identifier))
        if identifier == 'E03':
            parent = ('RAG 混合检索的分数如何融合，无证据时怎么办？',
                '我直接相加 BM25 和余弦分数，没有文档时让模型补全并生成引用。')
        elif identifier == 'E02':
            parent = ('RAG 中如何融合两路检索并确保回答有依据？', '先检索，再把找到的内容交给模型回答。')
        elif identifier == 'E12':
            parent = ('Kafka 消费处理订单后，怎样提交消费位点？', '收到消息就处理，失败了就重试，其他还没设计。')
        else:
            parent = ('Kafka 重复投递怎样避免重复落库？可以考虑订单ID唯一约束，把去重记录与订单写入放在同一事务，成功后再提交消费位点；请解释失败窗口。',
                '我只知道成功以后 ACK，还没弄清数据库事务和幂等的关系。')
        c.update(parent_question=parent[0], parent_answer=parent[1],
            gap='RRF排名融合与无依据拒答' if ':rag_' in c['target'] else '事务边界与重复副作用')
        result.append(c)
    return result


class FixtureReplay(AdaptiveReplayProvider):
    model_name = 'EXPLICIT_V232_PARENT_FIXTURE_NOT_LIVE'

    def __init__(self, case):
        super().__init__()
        self.case = case

    async def generate(self, messages, *, schema):
        c = self.case
        p = json.loads(messages[-1]['content'])['input']
        if schema['title'] == 'AdaptiveSelection':
            a = p['adaptive']
            probe = bool(p.get('answer'))
            strategy = 'FOLLOW_UP' if probe else 'NEXT'
            choice = next(row for row in a['candidates'] if row['target_knowledge_id'] == c['target']
                and strategy in row['strategies'] and row['source']['ref'].startswith('skill:'))
            value = dict(candidate_id=choice['candidate_id'], strategy=strategy,
                supporting_refs=[choice['source']['ref']] + ([a['current_answer_ref']] if probe else []),
                reason_tags=['MISSING_MECHANISM' if probe else 'OPENING'],
                proposed_question=dict(question=c['question'] if probe else c['parent_question'],
                    expected_points=c['rubric'], dimension=c['target'].split(':')[1], difficulty=p['desired_difficulty']))
        elif schema['title'] == 'AdaptiveEvaluation':
            value = dict(technical_correctness=2, coverage=2, depth=2, relevance=4, communication=4,
                evaluation='EXPLICIT REPLAY parent observation; not LIVE or gold scoring.', covered_points=[],
                missing_points=[c['gap']], factual_or_reasoning_issues=[], follow_up_focus=[c['gap']],
                next_action_recommendation='FOLLOW_UP', knowledge_observations=[dict(knowledge_id=c['target'],
                    state='NEEDS_CLARIFICATION', answer_quotes=[p['answer']], missing_mechanisms=[c['gap']], resolved_gap_ids=[])])
        else:
            raise RuntimeError('Unexpected preparation model schema')
        return ModelResponse(json.dumps(value, ensure_ascii=False), dict(input_tokens=0, output_tokens=0, total_tokens=0))


def isolate(database):
    settings.database_url = make_url(settings.database_url).set(database=database).render_as_string(hide_password=False)
    os.environ.update(DATABASE_URL=settings.database_url, APP_ENV='test')
    return async_sessionmaker(create_async_engine(settings.database_url), expire_on_commit=False)


async def prepare(database, output, resume=False):
    routes = configured_routes()  # inspect only; no model call
    if not resume:
        await base_prepare(database)
    factory = isolate(database)
    records = []
    try:
        async with factory() as db:
            from interview_api.modules.experience.models import ExperienceAgentStepRun
            assert await db.scalar(select(func.count()).select_from(ExperienceAgentStepRun).where(ExperienceAgentStepRun.model_name==TARGET))==0
            job=await db.scalar(select(Job).where(Job.company_id==1,Job.canonical_title=='DEMO Kafka Java 后端'))
            if not job:
                job = Job(company_id=1, canonical_title='DEMO Kafka Java 后端', job_family='backend')
                db.add(job)
                await db.flush()
                jd = JobDescriptionVersion(job_id=job.id, version_number=1,
                    raw_text='DEMO Replay fixture：Kafka可靠消费、位点提交、数据库事务和业务幂等。',
                    publication_status='PUBLISHED', metadata_json={'fixture': True})
                db.add(jd)
                await db.flush()
                db.add(JobRequirement(job_description_version_id=jd.id, statement='Kafka消息可靠消费、事务与幂等', requirement_type='SKILL', importance='CORE'))
                await db.commit()
            java_job = job.id
        # Reuse the existing Java-specific fixture/Profile builder, rather than
        # widening the AI user's frozen Blueprint to force Kafka into two slots.
        java_marker=ROOT/'.runtime/acceptance'/('v232_java_'+database+'.json')
        if java_marker.exists():
            java_user=json.loads(java_marker.read_text(encoding='utf-8'))
        else:
            java_uid, java_ctx=await kafka_seed(factory)
            java_user=dict(user_id=java_uid,company_id=java_ctx['company_id'],job_id=java_ctx['job_id'])
            java_marker.write_text(json.dumps(java_user),encoding='utf-8')
        for c in scenarios():
            owner=java_user if c['target'].startswith('java_') else dict(user_id=1,company_id=1,job_id=1)
            uid=owner['user_id']
            router = ModelRouter({r: FixtureReplay(c) for r in ('interview_main','answer_evaluation')})
            core = MockInterviewService(factory, router)
            async with factory() as db:
                existing=await db.scalar(select(InterviewSession.id).where(InterviewSession.user_id==uid,
                    InterviewSession.title=='DEMO V232 calibration '+c['id']))
            s = await core.detail(existing,uid) if existing else await core.create(uid, CreateSessionRequest(company_id=owner['company_id'],
                job_id=owner['job_id'], question_count=2,
                interviewer_skill_id=c['target'].split(':')[0], adaptive_interview=True,
                title='DEMO V232 calibration '+c['id']))
            q = await core.start(s['id'], uid) if s['status']=='READY' else s['questions'][0]
            events = await core.answer(s['id'], uid, q['question_id'], 'REPLAY-parent-'+c['id'], c['parent_answer'])
            parent_answer_id = next(v['answer_id'] for k,v in events if k=='evaluation')
            d = await core.detail(s['id'], uid)
            child = d['current_question']
            assert child['parent_question_id'] == q['question_id'] and child['question_kind'] == 'FOLLOW_UP'
            assert child['question'] == c['question']
            async with factory() as db:
                pending=await db.scalar(select(InterviewAnswer).where(InterviewAnswer.session_id==s['id'],
                    InterviewAnswer.session_question_id==child['question_id'],InterviewAnswer.idempotency_key=='LIVE-pair-'+c['id']))
                if pending and pending.status=='RUNNING':
                    a,expected,cached=pending,d['state_version'],False
                else:
                    a, expected, cached = await SessionController(db).prepare_answer(s['id'],uid,child['question_id'],'LIVE-pair-'+c['id'],c['answer'])
                assert not cached
                await db.commit()
                aid = a.id
            state, ctx, qs, active, _ = await core.read_state(s['id'],uid)
            p = planning_payload(ctx, qs, active, state.interview_plan_json, answer=c['answer'], answer_id=aid)
            p = await core.evaluation_context(s['id'],uid,p,answer_id=aid)
            sc = p['scoring_context']
            assert sc['parent_status']=='AVAILABLE' and sc['parent']['answer']['id']==parent_answer_id
            assert sc['parent']['question']['content']==c['parent_question'] and sc['parent']['answer']['content']==c['parent_answer']
            assert sc['assistance_status']=='UNKNOWN'
            old = deepcopy(p)
            old.pop('scoring_context')
            old['snapshot']['adaptive_policy'].pop('evaluation_context')
            old['snapshot']['adaptive_policy']['prompts'] = freeze_prompts(context_projection=True)
            from interview_api.modules.interview.agents import configured_prompt_registry
            measurements = {}
            for name, packet in (('old',old),('new',p)):
                cleaned=agent_payload(packet,'answer_evaluation')
                dto=model_payload(cleaned,'answer_evaluation')
                prompt=configured_prompt_registry(cleaned).get('answer_evaluation')
                wire=[dict(role='system',content=prompt.system),dict(role='user',content=json.dumps(dict(input=dto,schema=AdaptiveEvaluation.model_json_schema()),ensure_ascii=False))]
                measurements[name]=dict(prompt_version=prompt.version,wire_bytes=len(json.dumps(wire,ensure_ascii=False).encode()),
                    input_fingerprint=digest(wire),system_fingerprint=prompt.content_hash)
                assert dto['answer']==c['answer'] and dto['rubric']==c['rubric']
            records.append(dict(case=c,user_id=uid,session_id=s['id'],answer_id=aid,parent_answer_id=parent_answer_id,
                question_id=child['question_id'],expected=expected,snapshot_hash=s['snapshot_fingerprint'],
                old_packet=old,new_packet=p,measurements=measurements))
        output.write_text(json.dumps(dict(database=database,routes=routes,cases=records,
            semantics='Replay parent Q/A/Evaluation and Main; only upcoming paired Evaluations are LIVE',
            schema=AdaptiveEvaluation.model_json_schema(),proposed_limits=LIMITS),ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(status='PREPARED_NO_LIVE',cases=[dict(id=r['case']['id'],session=r['session_id'],measurements=r['measurements'],scoring_context=r['new_packet']['scoring_context']) for r in records]),ensure_ascii=False),flush=True)
    finally:
        await factory.kw['bind'].dispose()


class CalibrationBatch(LiveBatch):
    limits = LIMITS

    def save(self, **extra):
        super().save(scenarios=self.results, **extra)

    async def on_request(self, request):
        body=json.loads(request.content)
        if digest(body['messages'])!=self.expected_fingerprint:
            self.stop_reason='FROZEN_WIRE_CHANGED_BEFORE_SEND'
            raise BudgetStop(self.stop_reason)
        await super().on_request(request)

    async def on_response(self, response):
        await super().on_response(response)
        r=self.records[-1]
        try:
            AdaptiveEvaluation.model_validate(r.get('business_output'))
            r['structural_validation']='PASSED'
        except Exception as error:
            r['structural_validation']=str(error)
            self.stop_reason='INVALID_STRUCTURE_STOP_NO_REPAIR'
        self.save(status='RUNNING')


async def live(database, preparation, output):
    import httpx
    factory = isolate(database)
    prep=json.loads(preparation.read_text(encoding='utf-8'))
    assert prep['database']==database
    routes=configured_routes()
    config=build_interview_router()
    batch=CalibrationBatch(output)
    old_new_results=[]
    async def ledger():
        async with factory() as db:
            rows=(await db.scalars(select(ModelAdmission))).all()
            assert all(r.status=='SETTLED' and r.actual_tokens is not None and r.actual_tokens==r.charged_tokens for r in rows)
            assert sum(r.actual_tokens for r in rows)==batch.charged
            return dict(count=len(rows),tokens=sum(r.actual_tokens for r in rows),all_settled=True)
    try:
        async with asyncio.timeout(LIMITS['seconds']):
            async with httpx.AsyncClient(event_hooks={'request':[batch.on_request],'response':[batch.on_response]}) as client:
                router=ModelRouter({'answer_evaluation':Meter(config.primary('answer_evaluation'),batch,'answer_evaluation',client)},concurrency=config.concurrency)
                core=MockInterviewService(factory,router)
                # No second batch: fresh output AND zero existing target-model calls required.
                from interview_api.modules.experience.models import ExperienceAgentStepRun
                async with factory() as db:
                    assert await db.scalar(select(func.count()).select_from(ExperienceAgentStepRun).where(ExperienceAgentStepRun.model_name==TARGET))==0
                for record in prep['cases']:
                    sid,aid,expected,uid=record['session_id'],record['answer_id'],record['expected'],record['user_id']
                    c=record['case']
                    d=await core.detail(sid,uid)
                    assert d['snapshot_fingerprint']==record['snapshot_hash'] and d['state_version']==expected
                    assert d['context_snapshot']['adaptive_policy']['prompts']['answer_evaluation']['version']=='2.3.2'
                    pair={}
                    for arm in ('old','new'):
                        batch.check();batch.phase=c['id']+'_'+arm
                        batch.expected_fingerprint=record['measurements'][arm]['input_fingerprint']
                        packet=record[arm+'_packet']
                        batch.save(status='REQUEST_PREPARED',routes=routes)
                        result=await core.call_agent(core.evaluator.adaptive(),sid,uid,expected,packet,answer_id=aid)
                        assert result is not None
                        assert result.prompt_version==('2.3.1' if arm=='old' else '2.3.2')
                        batch.records[-1].update(business_validation='PASSED',agent_run_id=result.run_id,prompt_version=result.prompt_version,
                            input_fingerprint=digest(batch.records[-1]['request_messages']),controller='VALIDATED_NOT_COMMITTED')
                        pair[arm]=result
                        old_new_results.append(dict(case=c['id'],arm=arm,run_id=result.run_id,artifact=result.artifact.model_dump()))
                        batch.results=old_new_results
                        await ledger()
                        batch.save(status='EVALUATION_VALIDATED')
                    # Natural terminal condition: two persisted questions exhaust the
                    # original two-question budget. No synthetic Main or forced end.
                    async with factory() as db:
                        events=await SessionController(db).commit_answer(sid,uid,aid,expected,pair['new'],None,'COMPLETE',{})
                        await db.commit()
                    assert any(k=='complete' for k,v in events)
                    batch.records[-1]['controller']='ACCEPTED_COMMITTED_NEW_ARM'
                    before=len(batch.records)
                    reused=await core.call_agent(core.evaluator.adaptive(),sid,uid,expected,record['new_packet'],answer_id=aid)
                    assert reused.run_id==pair['new'].run_id and len(batch.records)==before
                    cached=await core.answer(sid,uid,record['question_id'],'LIVE-pair-'+c['id'],c['answer'])
                    assert cached==events and len(batch.records)==before
                    final=await core.detail(sid,uid)
                    assert final['status']=='COMPLETED' and final['context_snapshot']==d['context_snapshot']
                    async with factory() as db:
                        assert await db.scalar(select(func.count()).select_from(InterviewEvaluation).where(InterviewEvaluation.answer_id==aid))==1
                        assert await db.scalar(select(func.count()).select_from(InterviewAnswer).where(InterviewAnswer.session_id==sid))==2
                    assert not final['interview_plan_json']['model_call_reservations']
                    assert not final['interview_plan_json'].get('answer_processing')
                    batch.results.append(dict(case=c['id'],session_id=sid,formal_new_evaluation=True,idempotency=True,
                        state_version=final['state_version'],completion=final['completion_json'],scoring_context=record['new_packet']['scoring_context']))
                    batch.save(status='PAIR_COMMITTED')
                summary=await ledger()
                assert batch.logical==8 and len(batch.records)==8 and not batch.stop_reason
                batch.save(status='COMPLETED',ledger=summary)
                print(json.dumps(dict(status='COMPLETED',logical=batch.logical,http=len(batch.records),tokens=batch.charged,ledger=summary)),flush=True)
    except BaseException as error:
        batch.finalize_unknown()
        batch.stop_reason=batch.stop_reason or getattr(error,'code',type(error).__name__)
        batch.results.append(dict(failure_type=type(error).__name__,failure=str(error)[:1000]))
        batch.save(status='STOPPED')
        # Release only failed in-flight demo Answer leases, retain every audit.
        for r in prep['cases']:
            async with factory() as db:
                a=await db.get(InterviewAnswer,r['answer_id'])
                if a.status=='RUNNING':
                    await SessionController(db).failed_answer(r['session_id'],r['user_id'],a.id,r['expected'],batch.stop_reason)
                    await db.commit()
        raise
    finally:
        await factory.kw['bind'].dispose()


async def checkpoint(database, preparation, output):
    """Read-only formal fingerprints plus isolated acceptance records, no model."""
    import asyncpg
    value={}
    for name in ('interview_agent',database):
        conn=await asyncpg.connect(make_url(settings.database_url).set(drivername='postgresql',database=name).render_as_string(hide_password=False),timeout=5)
        try:
            async with conn.transaction(readonly=True,isolation='repeatable_read'):
                revision=await conn.fetchval('SELECT version_num FROM alembic_version')
                if name=='interview_agent':
                    tables=await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
                    values={}
                    for r in tables:
                        table=r['tablename'].replace('"','""')
                        row=await conn.fetchrow(f'''SELECT count(*) AS count,md5(COALESCE(string_agg(row_to_json(t)::text,'' ORDER BY row_to_json(t)::text),'')) AS hash FROM "{table}" t''')
                        values[r['tablename']]=dict(row)
                    value['formal']=dict(revision=revision,tables=values)
                else:
                    prep=json.loads(preparation.read_text(encoding='utf-8'))
                    ids=[r['session_id'] for r in prep['cases']]
                    values={}
                    for table,where in (('interview_sessions','id'),('interview_context_snapshots','interview_session_id'),
                        ('interview_answers','session_id'),('interview_evaluations','session_id'),
                        ('interview_session_questions','session_id'),('interview_agent_calls','session_id')):
                        values[table]=[json.loads(r[0]) for r in await conn.fetch(f'SELECT row_to_json(t)::text FROM {table} t WHERE {where}=ANY($1::bigint[]) ORDER BY id',ids)]
                    values['quota']=[dict(r) for r in await conn.fetch('SELECT call_key,owner_key,route,model,status,actual_tokens,charged_tokens FROM model_admissions ORDER BY created_at')]
                    values['live_steps']=[json.loads(r[0]) for r in await conn.fetch('SELECT row_to_json(t)::text FROM experience_agent_step_runs t WHERE model_name=$1 ORDER BY id',TARGET)]
                    value['isolated']=dict(database=database,revision=revision,**values)
        finally:await conn.close()
    output.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(dict(formal_revision=value['formal']['revision'],formal_tables=len(value['formal']['tables']),
        isolated_revision=value['isolated']['revision'],sessions=len(value['isolated']['interview_sessions']),
        admissions=len(value['isolated']['quota']))),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','live','checkpoint']);p.add_argument('--database',required=True);p.add_argument('--output',required=True);p.add_argument('--preparation');p.add_argument('--resume',action='store_true');a=p.parse_args()
    assert a.database.startswith('interview_agent_v232_calibration_') and a.database.replace('_','').isalnum()
    output=Path(a.output).resolve()
    assert output.is_relative_to((ROOT/'.runtime/acceptance').resolve()) and not output.exists()
    output.parent.mkdir(parents=True,exist_ok=True)
    if a.mode=='prepare':asyncio.run(prepare(a.database,output,resume=a.resume))
    elif a.mode=='checkpoint':asyncio.run(checkpoint(a.database,Path(a.preparation),output))
    else:
        with (output.parent/'v23_live.lock').open('a+b') as lock:
            if lock.tell()==0:lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            try:asyncio.run(live(a.database,Path(a.preparation),output))
            finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
