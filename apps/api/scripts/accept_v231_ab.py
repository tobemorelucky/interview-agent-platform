"""One authorized LIVE A/B batch; existing Agents, Controller and API only.

Old arm is an explicit counterfactual Agent-input boundary, NOT a rewritten old
Session. Only the new arm commits. All histories are marked synthetic Replay.
"""
import argparse
import asyncio
from copy import deepcopy
import json
import msvcrt
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accept_v23_live import API, ROOT, LiveBatch, Meter, BudgetStop, TARGET, prepare as base_prepare
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.core.config import settings
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.infrastructure.models.router import ModelRouter, build_interview_router
from interview_api.infrastructure.models.provider import ModelResponse
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.modules.interview.adaptive import planning_payload, validate_decision, AdaptiveSelection, AdaptiveEvaluation
from interview_api.modules.interview.adaptive_prompts import freeze_prompts
from interview_api.modules.interview.controller import SessionController
from v21_support import AdaptiveReplayProvider

ANSWER = '我把 BM25 原始分数和向量余弦相似度直接相加，分数越大越相关，没有标注集。检索不到资料时让模型按经验补齐并生成引用。没有考虑量纲、排名融合和无证据拒答。'


class HistoryReplay(AdaptiveReplayProvider):
    model_name = 'EXPLICIT_SYNTHETIC_AB_HISTORY_NOT_LIVE'
    good = False

    async def generate(self, messages, *, schema):
        p = json.loads(messages[-1]['content'])['input']
        if schema['title'] == 'AdaptiveSelection':
            a = p['adaptive']
            available = [c for c in a['candidates'] if 'NEXT' in c['strategies'] or p.get('answer')]
            preferred = [c for c in available if c['target_knowledge_id'].endswith(':rag_retrieval') and
                c['source']['ref'] == ('skill:ai_agent_application@1.0.0' if p.get('answer') else 'requirement:2')]
            c = (preferred or available)[0]
            probe = bool(p.get('answer')) and 'FOLLOW_UP' in c['strategies']
            label = a['targets'][c['target_knowledge_id']]['label']
            value = dict(candidate_id=c['candidate_id'], strategy='FOLLOW_UP' if probe else 'NEXT',
                supporting_refs=[c['source']['ref']] + ([a['current_answer_ref']] if p.get('answer') else []),
                reason_tags=['MISSING_MECHANISM' if probe else 'OPENING'],
                proposed_question=dict(question=(f"DEMO Replay 第{len(p['history'])+1}题：RAG里为什么不能直接相加BM25和向量原始分数？检索不到依据时如何处理？"
                    if c['target_knowledge_id'].endswith(':rag_retrieval') else f"DEMO Replay 第{len(p['history'])+1}题：{label}的机制和失败边界如何设计？"),
                    expected_points=['不同分数量纲不可直接比较', 'RRF按排名融合', '真实引用与无证据拒答'],
                    dimension='rag_retrieval', difficulty=p['desired_difficulty']))
        else:
            text = p['answer']
            value = dict(**{k: 4 if self.good else 2 for k in ('technical_correctness','coverage','depth','relevance','communication')},
                evaluation='DEMO Replay history only', covered_points=['说明检索'] if self.good else [],
                missing_points=[] if self.good else ['RRF排名融合与无依据拒答'], factual_or_reasoning_issues=[],
                follow_up_focus=[] if self.good else ['解释分数量纲和原文引用'], next_action_recommendation='NEXT',
                knowledge_observations=[dict(knowledge_id=p['adaptive']['active_knowledge_id'],
                    state='DEMONSTRATED_IN_SESSION' if self.good else 'NEEDS_CLARIFICATION',
                    answer_quotes=[text], missing_mechanisms=[] if self.good else ['RRF排名融合与无依据拒答'], resolved_gap_ids=[])])
        return ModelResponse(json.dumps(value, ensure_ascii=False), dict(input_tokens=0,output_tokens=0,total_tokens=0))


async def prepare(database, output, resume=False):
    if not resume:
        await base_prepare(database)
    engine = create_async_engine(make_url(settings.database_url).set(database=database))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    history = HistoryReplay()
    core = MockInterviewService(factory, ModelRouter({r:history for r in ('interview_main','answer_evaluation')}))
    try:
        from sqlalchemy import select, func
        from interview_api.modules.experience.models import ExperienceAgentStepRun
        from interview_api.modules.interview.models import InterviewSession
        if resume:
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(ExperienceAgentStepRun).where(
                    ExperienceAgentStepRun.model_name==TARGET)) == 0, 'Never resume a paid attempt'
        ids = []
        for i in range(3):
            history.good = i < 2
            title=f'DEMO REPLAY AB history {i+1}'
            async with factory() as db:
                existing=await db.scalar(select(InterviewSession.id).where(InterviewSession.title==title))
            session = (await core.detail(existing,1) if existing else await core.create(1, CreateSessionRequest(company_id=1,job_id=1,question_count=5,
                interviewer_skill_id='ai_agent_application',adaptive_interview=True,title=title)))
            text = ('RAG先两路召回，按排名RRF融合，用真实片段引用，无依据拒答。' if history.good else ANSWER)
            if not any(m['role']=='USER' for m in session['messages']) or session.get('pending_answer'):
                q = await core.start(session['id'],1)
                await core.answer(session['id'],1,q['question_id'],f'replay-history-{i}',text)
            for _ in range(8):
                detail = await core.detail(session['id'],1)
                if detail['status']=='COMPLETED':break
                await core.skip(session['id'],1,detail['current_question']['question_id'])
            assert (await core.detail(session['id'],1))['status']=='COMPLETED'
            ids.append(session['id'])
        history.good = False
        session = await core.create(1,CreateSessionRequest(company_id=1,job_id=1,question_count=4,
            interviewer_skill_id='ai_agent_application',adaptive_interview=True,title='DEMO REPLAY prelude + LIVE A/B boundary'))
        plan = session['context_snapshot']['memory_guided_plan']
        assert any(i['state']=='CONFLICTED' for i in plan['items']), plan
        q = await core.start(session['id'],1)
        await core.answer(session['id'],1,q['question_id'],'replay-prelude','RAG先检索，然后把内容交给模型。')
        state, context, questions, active, _ = await core.read_state(session['id'],1)
        async with factory() as db:
            answer, expected, reused = await SessionController(db).prepare_answer(session['id'],1,active.id,'ab-pending-answer',ANSWER)
            await db.commit()
            assert not reused
            aid = answer.id
        data = dict(database=database, history_sessions=ids, ab_session_id=session['id'],
            answer_id=aid, expected_version=expected, question_id=active.id, key='ab-pending-answer',
            memory_plan=plan, snapshot_hash=digest(context), model_calls=0,
            semantics='Explicit Replay history; old arm is noncommitted counterfactual, new arm commits normally')
        output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(data,ensure_ascii=True))
    finally:
        await engine.dispose()


class ABBatch(LiveBatch):
    limits = dict(logical=6,http=8,tokens=100000,seconds=600)

    def save(self, **extra):
        super().save(scenarios=self.results, **extra)

    async def on_request(self, request):
        await super().on_request(request)
        record = self.records[-1]
        record.update(input_fingerprint=digest(record['request_messages']),
            system_fingerprint=digest(record['request_messages'][0]['content']),
            projection='LEGACY' if self.phase.endswith('_old') else 'ROLE_CONTEXT_V1',
            prompt_version=('2.1.0' if record['route']=='answer_evaluation' else '2.2.1') if self.phase.endswith('_old') else '2.3.1')
        self.save(status='RUNNING')

    async def on_response(self, response):
        await super().on_response(response)
        record = self.records[-1]
        schema = AdaptiveSelection if record['route']=='interview_main' else AdaptiveEvaluation
        try:
            schema.model_validate(record.get('business_output'))
            record['structural_validation'] = 'PASSED'
        except Exception as error:
            record['structural_validation'] = str(error)
        record['supporting_refs_count'] = len((record.get('business_output') or {}).get('supporting_refs', []))
        self.save(status='RUNNING')


async def serve(database, output, preparation):
    import httpx
    import uvicorn
    from prepare_v22_live import configured_routes
    from interview_api.modules.interview.skill_engine import agent_payload
    from interview_api.modules.interview.context_projection import model_payload
    from compare_v231_context import measures, wire
    settings.database_url = make_url(settings.database_url).set(database=database).render_as_string(hide_password=False)
    os.environ.update(DATABASE_URL=settings.database_url,APP_ENV='test')
    routes = configured_routes()
    print(json.dumps(dict(routes=routes)),flush=True)
    config = build_interview_router()
    assert not config.fallback_routes and all(p.model_name==TARGET for p in config.routes.values())
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine,expire_on_commit=False)
    prep = json.loads(preparation.read_text(encoding='utf-8'))
    batch = ABBatch(output)
    async with httpx.AsyncClient(event_hooks={'request':[batch.on_request],'response':[batch.on_response]}) as client:
        router = ModelRouter({r:Meter(p,batch,r,client) for r,p in config.routes.items()},concurrency=config.concurrency)

        class GuardedCore(MockInterviewService):
            async def start(self,*a,**k):
                batch.phase='C_main_new'
                result=await super().start(*a,**k)
                batch.records[-1].update(business_validation='PASSED',controller='ACCEPTED')
                batch.save(status='C_QUESTION_READY')
                return result

            async def answer(self,*a,**k):
                batch.phase='C_evaluation_new'
                try:
                    events=await super().answer(*a,**k)
                    batch.records[-1].update(business_validation='PASSED',controller='ACCEPTED')
                    batch.save(status='C_ANSWER_COMMITTED')
                    return events
                except BaseException as error:
                    batch.stop_reason=batch.stop_reason or getattr(error,'code',type(error).__name__)
                    batch.save(status='STOPPED')
                    raise

        core = GuardedCore(factory,router)
        sid, aid, expected = prep['ab_session_id'],prep['answer_id'],prep['expected_version']
        async def call(phase, agent, packet):
            batch.phase=phase
            result=await core.call_agent(agent,sid,1,expected,packet,answer_id=aid)
            assert result is not None
            record=batch.records[-1]
            record.update(business_validation='PASSED',agent_run_id=result.run_id,controller='VALIDATED_NOT_COMMITTED')
            batch.results.append(dict(phase=phase,run_id=result.run_id,prompt_version=result.prompt_version,
                artifact=result.artifact.model_dump()))
            batch.save(status='AB_RUNNING')
            return result

        try:
            state, context, questions, active, _ = await core.read_state(sid,1)
            assert digest(context)==prep['snapshot_hash'] and state.state_version==expected
            p = planning_payload(context,questions,active,state.interview_plan_json,answer=ANSWER,answer_id=aid)
            def old(packet):
                packet=deepcopy(packet)
                packet['snapshot']['adaptive_policy'].pop('context_projection',None)
                packet['snapshot']['adaptive_policy']['prompts']=freeze_prompts(contract='TARGET_SOURCE_PAIR_V1',memory_guided=True)
                return packet
            a_old=await call('A_evaluation_old',core.evaluator.adaptive(),old(p))
            a_new=await call('A_evaluation_new',core.evaluator.adaptive(),p)
            packet=planning_payload(context,questions,active,state.interview_plan_json,answer=ANSWER,answer_id=aid,
                evaluation=a_new.artifact,evaluation_run_id=a_new.run_id)
            b_old=await call('B_main_old',core.main.adaptive(packet),old(packet))
            b_new=await call('B_main_new',core.main.adaptive(packet),packet)
            # Validate equal business inputs and compute pre-Live proxy estimates
            # for these EXACT paired states, not unrelated historical averages.
            for route, old_packet, new_packet, old_record, new_record in (
                ('answer_evaluation',old(p),p,batch.records[0],next(r for r in batch.records if r['phase']=='A_evaluation_new' and not r['repair'])),
                ('interview_main',old(packet),packet,next(r for r in batch.records if r['phase']=='B_main_old' and not r['repair']),next(r for r in batch.records if r['phase']=='B_main_new' and not r['repair']))):
                old_wire,new_wire=old_record['request_messages'],new_record['request_messages']
                bm=measures('\n'.join(m['content'] for m in old_wire));am=measures('\n'.join(m['content'] for m in new_wire))
                estimate=old_record['input_tokens']*sum(a/b for a,b in zip(am,bm))/2
                batch.results.append(dict(route=route,estimated_new_input=estimate,actual_new_input=new_record['input_tokens'],
                    old_input=old_record['input_tokens'],same_answer=old_packet['answer']==new_packet['answer'],
                    same_rubric=old_packet['rubric']==new_packet['rubric'],same_memory_plan=old_packet['snapshot']['memory_guided_plan']==new_packet['snapshot']['memory_guided_plan'],
                    candidate_ids_equal=[c['candidate_id'] for c in old_packet['adaptive']['candidates']]==[c['candidate_id'] for c in new_packet['adaptive']['candidates']]))
            policy=validate_decision(b_new.artifact,packet)
            async with factory() as db:
                await SessionController(db).commit_answer(sid,1,aid,expected,a_new,b_new,b_new.artifact.recommendation,policy)
                await db.commit()
            for record in batch.records:
                if record['phase'] in ('A_evaluation_new','B_main_new'):
                    record['controller']='ACCEPTED_COMMITTED_NEW_ARM'
            before=len(batch.records)
            reused=await core.call_agent(core.evaluator.adaptive(),sid,1,expected,p,answer_id=aid)
            assert reused.run_id==a_new.run_id and len(batch.records)==before
            replayed=await MockInterviewService.answer(core,sid,1,prep['question_id'],prep['key'],ANSWER)
            assert any(e[0]=='evaluation' for e in replayed) and len(batch.records)==before
            assert (await core.detail(sid,1))['snapshot_fingerprint']==prep['snapshot_hash']
            batch.save(status='AB_PASSED_WAITING_C')
            print('AB_PASSED; C API READY 127.0.0.1:8007',flush=True)
            from interview_api.main import app
            from interview_api.api.deps import get_db
            from interview_api.modules.interview import router as interviews
            async def isolated_db():
                async with factory() as db:
                    yield db
            app.dependency_overrides[get_db]=isolated_db
            interviews.build_core=lambda:core
            await uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8007,log_level='warning')).serve()
        except BaseException as error:
            if not isinstance(error,(KeyboardInterrupt,asyncio.CancelledError)):
                batch.stop_reason=batch.stop_reason or getattr(error,'code',type(error).__name__)
                batch.results.append(dict(failure=str(error),type=type(error).__name__))
                async with factory() as db:
                    await SessionController(db).failed_answer(sid,1,aid,expected,batch.stop_reason)
                    await db.commit()
                batch.save(status='STOPPED')
            raise
        finally:
            await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['prepare','prepare-resume','serve'])
    parser.add_argument('--database',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--preparation',type=Path)
    args=parser.parse_args()
    if not args.database.startswith('interview_agent_v21_v23_live_ab_') or not args.database.replace('_','').isalnum():
        raise SystemExit('Dedicated new isolation DB required')
    if args.output.exists() or not args.output.resolve().is_relative_to((ROOT/'.runtime/acceptance').resolve()):
        raise SystemExit('Fresh ignored artifact required')
    if args.mode in ('prepare','prepare-resume'):asyncio.run(prepare(args.database,args.output,resume=args.mode=='prepare-resume'))
    else:
        with (args.output.parent/'v23_live.lock').open('a+b') as lock:
            if lock.tell()==0:lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            try:asyncio.run(serve(args.database,args.output,args.preparation))
            finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
