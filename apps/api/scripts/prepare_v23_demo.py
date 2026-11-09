"""Explicit one-time fixture preparation in an EMPTY V2.3 isolated database."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4
API=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(API/'src'),str(API/'tests')]
from sqlalchemy import select,func,text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from interview_api.core.config import settings
from interview_api.core.security import hash_password
from interview_api.modules.users.models import User
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest
from interview_api.infrastructure.models.router import ModelRouter,INTERVIEW_MAIN_ROUTE,ANSWER_EVALUATION_ROUTE
from interview_api.infrastructure.models.provider import CompatibleModelProvider
from v22_support import kafka_seed,MemoryReplayProvider


async def prepare(name):
    if not name.startswith('interview_agent_v21_v23_browser_') or not name.replace('_','').isalnum():
        raise ValueError('Dedicated isolated browser DB required')
    engine=create_async_engine(make_url(settings.database_url).set(database=name))
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with factory() as db:
            assert await db.scalar(text('SELECT version_num FROM alembic_version'))=='0026'
            assert await db.scalar(select(func.count()).select_from(User))==0,'Never append/reseed an earlier attempt'
        actors=[]
        for label in ('first','conflict'):
            uid,ctx=await kafka_seed(factory)
            async with factory() as db:
                user=await db.get(User,uid)
                user.email=f'v23.{label}@example.com';user.username='DEMO_REPLAY_V23_'+label
                user.password_hash=hash_password('DEMO-v23-local-only')
                await db.commit()
            if label=='conflict':
                p=MemoryReplayProvider();core=MockInterviewService(factory,ModelRouter({r:p for r in (INTERVIEW_MAIN_ROUTE,ANSWER_EVALUATION_ROUTE)}))
                request=CreateSessionRequest(company_id=ctx['company_id'],job_id=ctx['job_id'],interviewer_skill_id='java_backend',adaptive_interview=True,question_count=1,title='DEMO REPLAY V23 conflict history')
                history=[await core.create(uid,request) for _ in range(3)]
                for i,h in enumerate(history):
                    q=await core.start(h['id'],uid)
                    await core.answer(h['id'],uid,q['question_id'],uuid4().hex,'Kafka 事务和唯一键，提交后 ACK，失败恢复' if i<2 else 'Kafka ACK，失败重试')
            actors.append(dict(label=label,user_id=uid,company_id=ctx['company_id'],job_id=ctx['job_id']))
        from test_m6_job_matching import seed_matching,artifact
        matching,provider,uid,jobs,_,_,run=await seed_matching(factory)
        provider.responses.append(artifact(run))
        matched=await matching.generate(uid,run['id'])
        assert matched['status']=='COMPLETED'
        async with factory() as db:
            user=await db.get(User,uid);user.email='v23.ai@example.com';user.username='DEMO_REPLAY_V23_ai'
            user.password_hash=hash_password('DEMO-v23-local-only');await db.commit()
        actors.append(dict(label='ai',user_id=uid,job_id=jobs[0]['job_id'],match_run_id=run['id']))
        print(json.dumps(dict(database=name,mode='DEMO_REPLAY_ONLY',actors=actors),ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);args=parser.parse_args()
    async def forbidden(*a,**k):raise RuntimeError('LIVE is prohibited during fixture preparation')
    with patch.object(CompatibleModelProvider,'generate',forbidden):
        asyncio.run(asyncio.wait_for(prepare(args.database),90))
