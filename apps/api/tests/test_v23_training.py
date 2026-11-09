"""Training public service/API seams, real PostgreSQL and explicit Replay model I/O."""
import asyncio
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from interview_api.modules.interview.schemas import CreateSessionRequest
from v22_support import kafka_seed


def test_training_requires_the_legal_adaptive_scope(database_url):
    async def run():
        from interview_api.modules.interview.core_service import MockInterviewService
        from interview_api.core.exceptions import AppError
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            with pytest.raises(AppError) as denied:
                await MockInterviewService(factory).create(uid, CreateSessionRequest(
                    company_id=ctx['company_id'], job_id=ctx['job_id'],
                    interviewer_skill_id='java_backend', adaptive_interview=False, training_mode='FULL'))
            assert denied.value.code == 'TRAINING_REQUIRES_ADAPTIVE'
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_workbench_first_visit_has_legal_goals_without_claiming_mastery(database_url):
    async def run():
        from interview_api.modules.interview.training import TrainingService
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            request = CreateSessionRequest(company_id=ctx['company_id'], job_id=ctx['job_id'],
                interviewer_skill_id='java_backend', adaptive_interview=True, question_count=8)
            board = await TrainingService(factory).workbench(uid, request)
            assert board['targets']
            kafka = next(t for t in board['targets'] if t['knowledge_id']=='java_backend:kafka_reliability')
            assert kafka['state']=='UNKNOWN' and kafka['available_modes']==['FULL']
            assert board['coverage']['reviewed_question_count']==0
            assert board['coverage']['fallback']=='GENERIC_SKILL'
            again = await TrainingService(factory).workbench(uid, request)
            assert again['targets']==board['targets']
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_training_api_recheck_conflict_and_private_feedback(database_url):
    async def run():
        import httpx
        from types import SimpleNamespace
        from unittest.mock import patch
        from uuid import uuid4
        from interview_api.main import app
        from interview_api.api.deps import get_current_user
        from interview_api.modules.interview import router as routes
        from interview_api.modules.interview.core_service import MockInterviewService
        from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
        from v22_support import MemoryReplayProvider
        engine=create_async_engine(database_url)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        actor=0
        async def user(): return SimpleNamespace(id=actor,role='USER')
        previous=dict(app.dependency_overrides)
        try:
            actor,ctx=await kafka_seed(factory)
            provider=MemoryReplayProvider()
            core=MockInterviewService(factory,ModelRouter({r:provider for r in (INTERVIEW_MAIN_ROUTE,ANSWER_EVALUATION_ROUTE)}))
            params=dict(company_id=ctx['company_id'],job_id=ctx['job_id'],interviewer_skill_id='java_backend',adaptive_interview=True,question_count=1)
            histories=[await core.create(actor,CreateSessionRequest(**params)) for _ in range(3)]
            for index,h in enumerate(histories):
                q=await core.start(h['id'],actor)
                await core.answer(h['id'],actor,q['question_id'],uuid4().hex,
                    'Kafka 事务和唯一键，提交后 ACK，失败恢复' if index<2 else 'Kafka ACK，失败重试')
            app.dependency_overrides[get_current_user]=user
            with patch.object(routes,'build_core',lambda:core):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                    board=await client.post('/api/v1/interview/training/workbench',json=params)
                    assert board.status_code==200
                    kafka=next(t for t in board.json()['data']['targets'] if t['knowledge_id']=='java_backend:kafka_reliability')
                    assert kafka['state']=='CONFLICTED' and 'RECHECK' in kafka['available_modes']
                    body={**params,'training_mode':'RECHECK','training_knowledge_id':kafka['knowledge_id'],'training_request_key':uuid4().hex}
                    a,b=await asyncio.gather(client.post('/api/v1/interview/sessions',json=body),client.post('/api/v1/interview/sessions',json=body))
                    assert a.status_code==b.status_code==201
                    assert a.json()['data']['id']==b.json()['data']['id']
                    sid=a.json()['data']['id']
                    q=await core.start(sid,actor)
                    await core.answer(sid,actor,q['question_id'],uuid4().hex,'Kafka 事务和唯一键，提交后 ACK，失败恢复')
                    feedback=await client.get(f'/api/v1/interview/training/sessions/{sid}')
                    assert feedback.status_code==200
                    assert feedback.json()['data']['targets'][0]['conflict_status']=='OPEN'
                    assert any(a['model_input']=='PREPARED' and a['controller_verdict']=='ACCEPTED' for a in feedback.json()['data']['memory_usage'])
                    actor+=999999
                    assert (await client.get(f'/api/v1/interview/training/sessions/{sid}')).status_code==404
        finally:
            app.dependency_overrides.clear(); app.dependency_overrides.update(previous)
            await engine.dispose()
    asyncio.run(run())


def test_user_can_train_a_weak_goal_and_read_frozen_feedback(database_url):
    async def run():
        from uuid import uuid4
        from interview_api.modules.interview.training import TrainingService
        from interview_api.modules.interview.core_service import MockInterviewService
        from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE
        from interview_api.core.exceptions import AppError
        from v22_support import MemoryReplayProvider
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            uid, ctx = await kafka_seed(factory)
            provider = MemoryReplayProvider()
            core = MockInterviewService(factory, ModelRouter({r:provider for r in (INTERVIEW_MAIN_ROUTE,ANSWER_EVALUATION_ROUTE)}))
            params = dict(company_id=ctx['company_id'],job_id=ctx['job_id'],interviewer_skill_id='java_backend',adaptive_interview=True,question_count=1)
            first=await core.create(uid,CreateSessionRequest(**params))
            q=await core.start(first['id'],uid)
            await core.answer(first['id'],uid,q['question_id'],uuid4().hex,'Kafka ACK，失败重试')
            request=CreateSessionRequest(**params,training_mode='WEAKNESS',training_knowledge_id='java_backend:kafka_reliability',training_request_key=uuid4().hex)
            detail=await core.create(uid,request)
            assert (await core.create(uid,request))['id']==detail['id']
            assert [t['knowledge_id'] for t in detail['context_snapshot']['adaptive_blueprint']['targets']]==['java_backend:kafka_reliability']
            q=await core.start(detail['id'],uid)
            key=uuid4().hex
            events=await core.answer(detail['id'],uid,q['question_id'],key,'Kafka 业务唯一键和业务更新同一事务提交后 ACK，处理失败恢复')
            assert events==await core.answer(detail['id'],uid,q['question_id'],key,'Kafka 业务唯一键和业务更新同一事务提交后 ACK，处理失败恢复')
            feedback=await TrainingService(factory).feedback(uid,detail['id'])
            assert feedback['targets'][0]['before_state']=='GAP_SUSPECTED'
            assert feedback['targets'][0]['after_state']=='PRACTICING'
            assert feedback['targets'][0]['effective_evaluations']==1
            assert feedback['targets'][0]['independent_evaluations']==1
            assert feedback==await TrainingService(factory).feedback(uid,detail['id'])
            with pytest.raises(AppError) as denied:
                await TrainingService(factory).feedback(uid+999999,detail['id'])
            assert denied.value.status_code==404
            with pytest.raises(AppError):
                await core.create(uid,CreateSessionRequest(**params,training_mode='WEAKNESS',training_knowledge_id='ai_agent_application:agent'))
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_training_safely_retries_rejected_selection_and_keeps_user_controls(database_url):
    async def run():
        import json
        from uuid import uuid4
        from interview_api.modules.interview.training import TrainingService
        from interview_api.modules.interview.core_service import MockInterviewService
        from interview_api.infrastructure.models.router import ModelRouter, INTERVIEW_MAIN_ROUTE, ANSWER_EVALUATION_ROUTE, INTERVIEW_COACH_ROUTE
        from interview_api.infrastructure.models.provider import ModelResponse
        from interview_api.modules.memory.capability_service import CapabilityMemoryService
        from interview_api.modules.interview_report.service import InterviewReportService
        from interview_api.core.exceptions import AppError
        from v22_support import MemoryReplayProvider
        class IgnoreThenReject(MemoryReplayProvider):
            rejected=False
            async def generate(self,messages,*,schema):
                payload=json.loads(messages[-1]['content'])['input']
                if schema['title']=='AdaptiveSelection' and not payload.get('answer'):
                    c=next(c for c in payload['adaptive']['candidates'] if c['target_knowledge_id'].endswith(':redis'))
                    return ModelResponse(json.dumps(dict(candidate_id=c['candidate_id'],strategy='NEXT',supporting_refs=[c['source']['ref']],reason_tags=['OPENING'],
                        proposed_question=dict(question='DEMO Redis 缓存如何处理并发一致性？',expected_points=['缓存一致性'],dimension='缓存',difficulty='MEDIUM'))),dict(total_tokens=0))
                result=await super().generate(messages,schema=schema)
                if schema['title']=='AdaptiveSelection' and not self.rejected:
                    self.rejected=True; value=json.loads(result.text);value['candidate_id']='C_FAKE_REJECT_ME'
                    return ModelResponse(json.dumps(value),result.usage)
                return result
        engine=create_async_engine(database_url); factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            uid,ctx=await kafka_seed(factory)
            p=MemoryReplayProvider(); base=MockInterviewService(factory,ModelRouter({r:p for r in (INTERVIEW_MAIN_ROUTE,ANSWER_EVALUATION_ROUTE)}))
            params=dict(company_id=ctx['company_id'],job_id=ctx['job_id'],interviewer_skill_id='java_backend',adaptive_interview=True)
            old=await base.create(uid,CreateSessionRequest(**params,question_count=1));q=await base.start(old['id'],uid)
            await base.answer(old['id'],uid,q['question_id'],uuid4().hex,'Kafka ACK 重试')
            service=TrainingService(factory)
            assert not (await service.feedback(uid,old['id']))['available']
            p=IgnoreThenReject();core=MockInterviewService(factory,ModelRouter({r:p for r in (INTERVIEW_MAIN_ROUTE,ANSWER_EVALUATION_ROUTE,INTERVIEW_COACH_ROUTE)}))
            session=await core.create(uid,CreateSessionRequest(**params,question_count=2,training_mode='FULL'))
            pinned=session['context_snapshot'];q=await core.start(session['id'],uid)
            usage=(await service.feedback(uid,session['id']))['memory_usage']
            assert usage[0]['selection']=='OTHER_TARGET_SELECTED' and usage[0]['candidate_memory']
            key=uuid4().hex; answer='Redis 更新与事务一致性，具体实现及失败恢复'
            with pytest.raises(AppError) as rejected:
                await core.answer(session['id'],uid,q['question_id'],key,answer)
            assert rejected.value.code=='UNKNOWN_DIRECTOR_CANDIDATE'
            failed=await core.detail(session['id'],uid)
            assert len(failed['questions'])==1 and failed['pending_answer']['retryable']
            assert any(u['controller_verdict']=='NOT_REACHED' for u in (await service.feedback(uid,session['id']))['memory_usage'])
            await core.answer(session['id'],uid,q['question_id'],key,answer)
            q=(await core.detail(session['id'],uid))['current_question']
            await core.answer(session['id'],uid,q['question_id'],uuid4().hex,'Kafka 事务提交与唯一键去重，失败恢复后 ACK')
            assert p.calls.count('AdaptiveEvaluation')==2
            before=await service.feedback(uid,session['id'])
            reports=InterviewReportService(factory,core.router)
            assert (await reports.generate(session['id'],uid))['generation_status']=='COMPLETED'
            assert (await reports.generate(session['id'],uid,regenerate=True,request_key=uuid4().hex))['generation_status']=='COMPLETED'
            assert before==await service.feedback(uid,session['id'])
            async with factory() as db:
                root=next(r for r in await CapabilityMemoryService(db).list(uid) if r['topic']=='kafka_reliability')
                await CapabilityMemoryService(db).correct(uid,root['id'],expected_revision=root['revision'],excluded=[],note='DEMO 撤销推断',revoke=True)
                await db.commit()
            board=await service.workbench(uid,CreateSessionRequest(**params,question_count=8))
            target=next(t for t in board['targets'] if t['knowledge_id'].endswith(':kafka_reliability'))
            assert target['revoked'] and target['available_modes']==['FULL']
            assert (await core.detail(session['id'],uid))['context_snapshot']==pinned
            empty=await base.create(uid,CreateSessionRequest(**params,question_count=1,training_mode='FULL'))
            q=await base.start(empty['id'],uid);await base.skip(empty['id'],uid,q['question_id'])
            result=await service.feedback(uid,empty['id'])
            assert result['targets'][0]['result']=='NO_NEW_OBSERVATIONS'
        finally: await engine.dispose()
    asyncio.run(run())
