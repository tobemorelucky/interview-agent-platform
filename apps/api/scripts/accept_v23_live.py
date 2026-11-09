"""One bounded V2.3 LIVE browser batch. Instrumentation, not a new runtime.

No prompt/model overrides. Prepare explicitly synthetic data in a NEW database;
serve the real app with metered configured Main/Evaluation/Coach providers.
"""
import argparse
import asyncio
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
sys.path[:0] = [str(API/'src'), str(API/'tests'), str(API/'scripts')]
from sqlalchemy.engine import make_url
from interview_api.core.config import settings
from accept_deepseek_v12_v13 import Batch, BudgetStop, CALL, TARGET


class LiveBatch(Batch):
    limits = dict(logical=50, http=60, tokens=650000, seconds=2400)

    def save(self, **extra):
        self.output.write_text(json.dumps(dict(
            model=TARGET, model_mode='LIVE', data_mode='SYNTHETIC_DEMO',
            limits=self.limits,
            logical_calls=self.logical,http_attempts=len(self.records),
            conservative_charged_tokens=self.charged,reserved_tokens=self.reserved,
            elapsed_seconds=round(time.monotonic()-self.start,3),stop_reason=self.stop_reason,
            http=self.records,**extra),ensure_ascii=False,indent=2,default=str),encoding='utf-8')

    def check(self):
        if self.stop_reason or time.monotonic()-self.start>=self.limits['seconds']:
            self.stop_reason=self.stop_reason or 'BATCH_DEADLINE'
            raise BudgetStop(self.stop_reason)

    async def on_request(self, request):
        self.check()
        body=json.loads(request.content)
        if body.get('model')!=TARGET:
            self.stop_reason='UNEXPECTED_MODEL'
            raise BudgetStop(self.stop_reason)
        estimate=len(request.content)+int(body['max_tokens'])+1024
        if len(self.records)>=self.limits['http'] or self.charged+self.reserved+estimate>self.limits['tokens']:
            self.stop_reason='HTTP_OR_TOKEN_BUDGET'
            raise BudgetStop(self.stop_reason)
        call=CALL.get()
        self.reserved+=estimate
        record=dict(number=len(self.records)+1,logical=call['logical'],route=call['route'],
            repair=call['repair'],phase=self.phase,model=TARGET,thinking=body.get('thinking'),
            output_limit=body['max_tokens'],input_chars=sum(len(m.get('content','')) for m in body['messages']),
            estimate=estimate,accounting='RESERVED',http_status=None,
            request_messages=body['messages'])  # DEMO only; no headers / hidden reasoning.
        self.records.append(record)
        request.extensions['acceptance']=(record,time.monotonic())
        self.save(status='RUNNING')

    async def on_response(self,response):
        await super().on_response(response)
        record=self.records[-1]
        data=response.json()
        message=((data.get('choices') or [{}])[0]).get('message') or {}
        try: record['business_output']=json.loads(message.get('content') or '')
        except ValueError: record['business_output_parse']='NOT_JSON'
        self.save(status='RUNNING')


class Meter:
    def __init__(self,inner,batch,route,client):
        self.inner,self.batch,self.route=inner,batch,route
        inner.client=client
        for attr in ('provider_name','model_name','base_url','timeout','max_output_tokens','thinking_type','governed'):
            setattr(self,attr,getattr(inner,attr))

    async def generate(self,messages,*,schema):
        self.batch.check()
        repair=messages[0]['content'].startswith('Repair JSON syntax/structural fields only.')
        if not repair:
            if self.batch.logical>=self.batch.limits['logical']:
                self.batch.stop_reason='LOGICAL_BUDGET';raise BudgetStop('LOGICAL_BUDGET')
            self.batch.logical+=1
        if time.monotonic()-self.batch.start+self.timeout>self.batch.limits['seconds']:
            self.batch.stop_reason='INSUFFICIENT_DEADLINE';raise BudgetStop('INSUFFICIENT_DEADLINE')
        token=CALL.set(dict(logical=self.batch.logical,repair=repair,route=self.route,schema=schema.get('title')))
        try:
            return await self.inner.generate(messages,schema=schema)
        except BaseException:
            self.batch.finalize_unknown()
            self.batch.save(status='REQUEST_FAILED')
            raise
        finally: CALL.reset(token)


async def prepare(database):
    import asyncpg
    from unittest.mock import patch
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from interview_api.infrastructure.models.provider import CompatibleModelProvider,ReplayModelProvider
    from interview_api.infrastructure.models.router import ModelRouter,RESUME_PROFILE_ROUTE
    from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
    from interview_api.modules.candidate_profile.evidence import evidence_rows
    from interview_api.core.security import hash_password
    from interview_api.modules.users.models import User
    from interview_api.modules.public_career.models import Company,Job,JobDescriptionVersion,JobRequirement
    from test_m3_candidate_profile import seed
    url=make_url(settings.database_url).set(database=database)
    connection=await asyncpg.connect(url.set(drivername='postgresql',database='postgres').render_as_string(hide_password=False),timeout=5)
    try:
        if await connection.fetchval('SELECT 1 FROM pg_database WHERE datname=$1',database):
            raise RuntimeError('Existing attempt refused; never overwrite')
        await connection.execute(f'CREATE DATABASE "{database}"')
    finally: await connection.close()
    subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=API,
        env={**os.environ,'DATABASE_URL':url.render_as_string(hide_password=False)},check=True,timeout=90)
    raw='''DEMO 测试候选人，纯人工构造，不是真实个人简历
技能
Python RAG Agent Redis PostgreSQL
项目经历
训练助手：使用 Python 实现 RAG 检索与 Agent 工具调用，采用 Schema 校验和工具白名单，Redis 缓存，PostgreSQL 保存状态。
检索项目：将文档拆分后使用 BM25 和向量检索，RRF 融合排名，人工检查引用来源；只在 Docker 本地部署，未验证生产并发。
教育背景
示例大学 计算机科学 硕士
'''
    async def forbidden(*a,**k):raise RuntimeError('No LIVE during preparation')
    engine=create_async_engine(url);factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        uid,rid=await seed(factory,raw)
        rows=evidence_rows(rid,raw)
        def refs(fragment):return [f'E{i:02}' for i,r in enumerate(rows,1) if fragment in r['excerpt']][:1]
        result=dict(skills=[dict(label=s,evidence_ids=refs('训练助手'),proposed_evidence_state='DEMONSTRATED')
            for s in ('Python','RAG','Agent','Redis','PostgreSQL')],
            projects=[dict(label='训练助手',evidence_ids=refs('训练助手')),dict(label='检索项目',evidence_ids=refs('检索项目'))],
            education=[dict(label='示例大学',evidence_ids=refs('示例大学'))],work_experience=[],internship_experience=[],unknowns=['生产规模未确认'])
        with patch.object(CompatibleModelProvider,'generate',forbidden):
            generated=await ResumeProfileAgent(factory,ModelRouter({RESUME_PROFILE_ROUTE:ReplayModelProvider([result])})).run(rid)
        assert generated['status']=='COMPLETED',generated
        async with factory() as db:
            user=await db.get(User,uid);user.email='v23.live@example.com';user.username='DEMO_V23_LIVE'
            user.password_hash=hash_password('DEMO-v23-live-only')
            company=Company(canonical_name='DEMO Live 验收公司');db.add(company);await db.flush()
            job=Job(company_id=company.id,canonical_title='DEMO AI Agent 应用开发工程师',job_family='ai_application');db.add(job);await db.flush()
            jd=JobDescriptionVersion(job_id=job.id,version_number=1,raw_text='DEMO fixture：Python 后端、RAG 检索、Agent 工具权限与状态、系统异常恢复。不是实际招聘 JD。',publication_status='PUBLISHED',metadata_json={'fixture':True});db.add(jd);await db.flush()
            for requirement in ('Python 异步服务与工程实践','RAG 检索与来源验证','Agent 工具调用 Schema、权限和状态管理','Redis 缓存与 PostgreSQL 事务','系统设计、异常处理和失败恢复'):
                db.add(JobRequirement(job_description_version_id=jd.id,statement=requirement,requirement_type='SKILL',importance='CORE'))
            await db.commit()
            print(json.dumps(dict(database=database,user_id=uid,company_id=company.id,job_id=job.id,profile=generated['version_id'],fixture=True)))
    finally:await engine.dispose()


def serve(database,output,short=False):
    from contextlib import asynccontextmanager
    import httpx
    import uvicorn
    os.environ.update(APP_ENV='test',DATABASE_URL=make_url(settings.database_url).set(database=database).render_as_string(hide_password=False))
    settings.database_url=os.environ['DATABASE_URL']
    from prepare_v22_live import configured_routes
    print(json.dumps(dict(routes=configured_routes()),ensure_ascii=False),flush=True)
    from interview_api.main import app
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.infrastructure.models.router import build_interview_router,build_interview_coach_router,ModelRouter
    from interview_api.modules.interview import router as interviews
    from interview_api.modules.interview_report import router as reports
    from interview_api.modules.interview.core_service import MockInterviewService
    from interview_api.modules.interview_report.service import InterviewReportService
    batch=LiveBatch(output)
    if short:
        batch.limits=dict(logical=7,http=9,tokens=120000,seconds=600)
    client=httpx.AsyncClient(event_hooks={'request':[batch.on_request],'response':[batch.on_response]})
    configured=build_interview_router();coach=build_interview_coach_router()
    sources=(configured,) if short else (configured,coach)
    router=ModelRouter({r:Meter(p,batch,r,client) for source in sources for r,p in source.routes.items()},concurrency=configured.concurrency)
    class GuardedCore(MockInterviewService):
        async def start(self,*a,**k):
            batch.phase='opening'
            try:return await super().start(*a,**k)
            except BaseException as exc:
                batch.stop_reason=batch.stop_reason or getattr(exc,'code',type(exc).__name__);batch.save(status='STOPPED');raise
        async def answer(self,*a,**k):
            batch.phase='answer_evaluation_next'
            try:return await super().answer(*a,**k)
            except BaseException as exc:
                batch.stop_reason=batch.stop_reason or getattr(exc,'code',type(exc).__name__);batch.save(status='STOPPED');raise
    interviews.build_core=lambda:GuardedCore(async_session_factory,router)
    reports.build_core=lambda:InterviewReportService(async_session_factory,router)
    batch.save(status='READY_NO_CALLS')
    uvicorn.run(app,host='127.0.0.1',port=8007,log_level='warning')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','serve']);parser.add_argument('--database',required=True);parser.add_argument('--output');parser.add_argument('--short',action='store_true');args=parser.parse_args()
    if not args.database.startswith('interview_agent_v21_v23_live_') or not args.database.replace('_','').isalnum():
        raise SystemExit('New dedicated V2.3 LIVE isolation database required')
    if args.mode=='prepare':asyncio.run(prepare(args.database))
    else:
        output=Path(args.output).resolve()
        if output.exists() or not output.is_relative_to((ROOT/'.runtime/acceptance').resolve()):raise SystemExit('New ignored artifact required')
        output.parent.mkdir(parents=True,exist_ok=True)
        with (output.parent/'v23_live.lock').open('a+b') as lock:
            if lock.tell()==0:lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            try:serve(args.database,output,short=args.short)
            finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
