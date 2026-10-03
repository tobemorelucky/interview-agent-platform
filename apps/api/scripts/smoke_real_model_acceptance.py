"""F2 only: LIVE configured models + explicitly LOCAL_FIXTURE/REPLAY data.

Never run against the business database. No Search, model substitution, prompt
changes, relaxed validators, raw model responses or hidden reasoning logging.
The parser/ResumeReport input is a local fixture; all six Agent outputs are LIVE.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import interview_api.modules.models  # noqa: F401
from interview_api.core.config import settings
from interview_api.core.security import hash_password
from interview_api.infrastructure.models.provider import CompatibleModelProvider
from interview_api.infrastructure.models.router import (
    build_model_router, build_resume_profile_router, build_job_matching_router,
    build_interview_router, build_interview_coach_router,
)
from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
from interview_api.modules.public_career.models import (
    PublicDataRefreshTask, SourceSnapshot, Company, Job, JobDescriptionVersion,
    JobRequirement, JobTag, ClaimEvidence,
)
from interview_api.modules.public_career.tools.factory import build_tool_gateway, FETCH_TOOL_NAME
from interview_api.modules.public_career.tools.contracts import ToolContext
from interview_api.modules.public_career.tools.replay import ReplaySearchProvider, ReplayFetchProvider
from interview_api.modules.public_career.tool_models import ToolInvocation
from interview_api.modules.public_career.refresh_service import PublicDataRefreshService, RefreshTaskStatus
from interview_api.modules.public_career.research.agent import PublicDataResearchAgent
from interview_api.modules.public_career.research.schemas import JobJDExtraction, ResearchQuality
from interview_api.modules.public_career.research.evidence import EvidenceCatalog
from interview_api.modules.public_career.dedupe import group_independent_content
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.models import CandidateProfile, CandidateProfileVersion, ResumeEvidence
from interview_api.modules.candidate_profile.schemas import CandidateProfileDelta
from interview_api.modules.candidate_profile.service import CandidateProfileService
from interview_api.modules.resume.parser import ResumeParser
from interview_api.modules.resume.models import Resume, ResumeReport
from interview_api.modules.users.models import User
from interview_api.modules.experience.models import InterviewQuestion, ExperienceAgentRun, ExperienceAgentStepRun
from interview_api.modules.job_matching.service import JobMatchingService
from interview_api.modules.job_matching.schemas import MatchRequest, MatchFilters, MatchArtifact
from interview_api.modules.job_matching.models import JobMatchRun
from interview_api.modules.interview.core_service import MockInterviewService
from interview_api.modules.interview.schemas import CreateSessionRequest, SendMessageRequest
from interview_api.modules.interview.artifacts import MainArtifact, EvaluationArtifact, DIMENSIONS
from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation, InterviewSessionQuestion, InterviewSession
from interview_api.modules.interview_report.service import InterviewReportService
from interview_api.modules.interview_report.schemas import CoachArtifact
from interview_api.modules.interview_report.statistics import interview_records, deterministic_statistics
from interview_api.modules.interview_report.models import InterviewPerformanceObservation
from interview_api.modules.memory.models import UserMemoryItem, UserSkillProfile
from interview_api.modules.memory.proposal_models import MemoryProposal


FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'
DATABASE = 'interview_agent_f2_real_model_smoke_attempt2'
ROLES = ('PublicDataResearchAgent', 'ResumeProfileAgent', 'JobMatchingAgent',
         'InterviewMainAgent', 'AnswerEvaluationAgent', 'SummaryCoachAgent')
SCHEMAS = dict(public_research_extract=JobJDExtraction, public_research_quality=ResearchQuality,
    resume_profile=CandidateProfileDelta, job_matching=MatchArtifact,
    interview_main=MainArtifact, answer_evaluation=EvaluationArtifact, interview_coach=CoachArtifact)


class Observer:
    """Transparent HTTP metrics; never retains response text/reasoning/key."""
    def __init__(self):
        self.calls = []
        self.clients = []

    def attach(self, router):
        for route, provider in list(router.routes.items()) + list(router.fallback_routes.items()):
            assert type(provider) is CompatibleModelProvider
            assert provider.provider_name != 'replay'
            expected_url = provider.base_url + '/chat/completions'

            async def request_hook(request, expected=expected_url):
                assert request.method == 'POST' and str(request.url) == expected
                request.extensions['f2_started'] = time.perf_counter()

            async def response_hook(response, name=route, configured=provider):
                await response.aread()
                row = dict(route=name, provider=configured.provider_name, model=configured.model_name,
                    http_status=response.status_code,
                    latency_ms=round((time.perf_counter()-response.request.extensions['f2_started'])*1000),
                    input_tokens=None, output_tokens=None, reasoning_tokens=None, cost=None)
                try:
                    data = response.json()
                    usage = data.get('usage') or {}
                    row.update(input_tokens=usage.get('prompt_tokens'), output_tokens=usage.get('completion_tokens'),
                        reasoning_tokens=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens'))
                    if response.is_error:
                        row['response_category'] = 'provider_http_error'
                        error = data.get('error') or {}
                        row['provider_error_code'] = error.get('code')
                        row['provider_error_type'] = error.get('type')
                    else:
                        choice = data['choices'][0]
                        row['finish_reason'] = choice.get('finish_reason')
                        # Only the final structured content is inspected, never reasoning_content.
                        try:
                            parsed = json.loads(choice['message'].get('content') or '')
                            SCHEMAS[name].model_validate(parsed)
                            row['response_category'] = 'schema_valid'
                        except json.JSONDecodeError:
                            row['response_category'] = 'json_syntax_error'
                        except ValidationError as exc:
                            row['response_category'] = 'schema_validation_error'
                            row['validation_locations'] = [dict(path=list(e['loc']),type=e['type'])
                                for e in exc.errors(include_input=False,include_url=False)]
                except (ValueError, KeyError, IndexError, TypeError):
                    row['response_category'] = 'provider_response_envelope_error'
                self.calls.append(row)

            client = httpx.AsyncClient(timeout=provider.timeout,
                event_hooks={'request':[request_hook], 'response':[response_hook]})
            provider.client = client
            self.clients.append(client)
        return router

    async def close(self):
        for client in self.clients:
            await client.aclose()


async def research(factory, router):
    body = (FIXTURES / 'f2_jd_fixture.txt').read_text(encoding='utf-8')
    url = 'https://f2-fixture.example/careers/demo-ai'
    gateway = build_tool_gateway(factory, search_provider=ReplaySearchProvider([]),
        fetch_provider=ReplayFetchProvider({url:dict(content=body,title='TEST LOCAL_FIXTURE JD')}))
    async with factory() as db:
        task = PublicDataRefreshTask(target_type='JOB_JD',target_ref='F2 TEST LOCAL_FIXTURE JD',
            reason='F2 LIVE MODEL acceptance with local fixture data; no Live Search')
        db.add(task); await db.commit()
        task_id = task.id
        controller = PublicDataRefreshService(db,gateway)
        await controller.transition(task,RefreshTaskStatus.SEARCHING)
        await controller.transition(task,RefreshTaskStatus.FETCHING)
        await db.commit()
    fetched = await gateway.invoke(FETCH_TOOL_NAME,dict(url=url),ToolContext(refresh_task_id=task_id))
    refs = fetched.data['source_snapshot_refs']
    # Actual fixture Fetch goes through the same Gateway persistence and audit.
    repeated = await gateway.invoke(FETCH_TOOL_NAME,dict(url=url),ToolContext(refresh_task_id=task_id))
    assert refs == repeated.data['source_snapshot_refs']
    async with factory() as db:
        task = await db.get(PublicDataRefreshTask,task_id)
        await PublicDataRefreshService(db,gateway).transition(task,RefreshTaskStatus.READY_FOR_EXTRACTION)
        await db.commit()
    result = await PublicDataResearchAgent(factory,router).run(task_id)
    if result['status'] != 'COMPLETED': raise RuntimeError(result.get('error','public_research_failed'))
    async with factory() as db:
        snapshot = await db.get(SourceSnapshot,refs[0]['source_snapshot_id'])
        jd = await db.get(JobDescriptionVersion,result['jd_version_id'])
        requirements = list((await db.scalars(select(JobRequirement).where(JobRequirement.job_description_version_id==jd.id))).all())
        catalog = EvidenceCatalog([snapshot])
        assert snapshot.content_text == body and requirements
        evidence_count = 0
        for requirement in requirements:
            assert requirement.claim_id is not None
            records = list((await db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id==requirement.claim_id))).all())
            assert records
            for evidence in records:
                span = catalog.resolve([evidence.evidence_key])[0]
                assert evidence.source_snapshot_id == snapshot.id and evidence.excerpt == span.excerpt
                assert body[span.start:span.end] == evidence.excerpt
                evidence_count += 1
        calls = list((await db.scalars(select(ToolInvocation).where(ToolInvocation.refresh_task_id==task_id))).all())
        assert calls and all(c.provider_mode=='REPLAY' and c.tool_name==FETCH_TOOL_NAME for c in calls)
        counts = group_independent_content([snapshot])
        assert jd.metadata_json['independent_content_count'] == counts.independent_content_count == 1
        return dict(task_id=task_id,status='COMPLETED',snapshot_id=snapshot.id,jd_id=jd.id,
            requirements=len(requirements),verified_claim_evidence=evidence_count,
            tool_mode='REPLAY',data_mode='LOCAL_FIXTURE',publication_status=jd.publication_status,
            independent_content_count=counts.independent_content_count)


async def profile(factory, router):
    content = (FIXTURES / 'f2_resume_demo.txt').read_bytes()
    raw_text = ResumeParser().parse(content,'txt')
    storage = MinioObjectStorageProvider()
    await storage.ensure_bucket(settings.minio_bucket)
    async with factory() as db:
        user = User(email='f2-demo-model@example.com',username='F2_DEMO_MODEL',
            password_hash=hash_password('F2DemoInputOnly!'),role='USER')
        db.add(user); await db.flush()
        user_id = user.id
        key = f'f2-local-fixture-demo/{DATABASE}/resume.txt'
        await storage.upload(settings.minio_bucket,key,content,'text/plain')
        resume = Resume(user_id=user.id,filename='f2_resume_demo.txt',storage_key=key,
            file_type='txt',file_size=len(content),content_hash=sha256(content).hexdigest(),
            raw_text=raw_text,status='COMPLETED',processing_stage='SAVING_REPORT')
        db.add(resume); await db.flush()
        # Explicit input fixture, not an invented live legacy ResumeReport extraction.
        db.add(ResumeReport(resume_id=resume.id,user_id=user.id,
            summary_json=dict(mode='LOCAL_FIXTURE_DEMO',parser='ResumeParser',raw_text=raw_text),
            questions_json={'questions':[]},retrieval_queries_json={'queries':[]}))
        await db.commit(); resume_id = resume.id
    result = await ResumeProfileAgent(factory,router).run(resume_id)
    if result['status'] != 'COMPLETED': raise RuntimeError(result.get('error','resume_profile_failed'))
    async with factory() as db:
        version = await db.get(CandidateProfileVersion,result['version_id'])
        actual = await CandidateProfileService(db).serialize(user_id)
        assert version.provider!='replay' and version.summary and version.snapshot_json['skills']
        assert version.snapshot_json['projects'] and version.snapshot_json['education']
        evidence = list((await db.scalars(select(ResumeEvidence).where(ResumeEvidence.resume_id==resume_id))).all())
        assert all(raw_text[e.start_offset:e.end_offset]==e.excerpt and e.page is None for e in evidence)
        assert all(actual['normalized'][name] for name in ('skills','projects','education'))
        profile_audit = await db.get(ExperienceAgentRun,version.agent_run_id)
        states = profile_audit.output_json.get('skill_state_resolution',[])
        result.update(user_id=user_id,resume_id=resume_id,evidence_count=len(evidence),
            skill_state_resolution=states,normalized_skill_states=sum(s['normalized'] for s in states),
            normalized_counts={k:len(v) for k,v in actual['normalized'].items()},
            unknowns=version.snapshot_json['unknowns'],resume_report_mode='LOCAL_FIXTURE_DEMO')
    retry = await ResumeProfileAgent(factory,router).run(resume_id)
    assert retry['reused'] and retry['version_id']==result['version_id']
    return result


async def matching(factory, router, user_id, profile_id, resume_run_id=None):
    if resume_run_id is not None:
        core = JobMatchingService(factory,router)
        run = await core.detail(user_id,resume_run_id)
        assert run['status']=='FAILED' and run['error']=='model_timeout' and len(run['attempts'])==1
        return await finish_matching(core,run,user_id,profile_id)
    definitions = json.loads((FIXTURES/'m6_jobs_demo.json').read_text(encoding='utf-8'))['jobs']
    async with factory() as db:
        company = Company(canonical_name='F2 DEMO Matching Company',status='ACTIVE')
        db.add(company); await db.flush()
        for index,item in enumerate(definitions):
            job = Job(company_id=company.id,canonical_title=item['title'],job_family=item['family'],campus_type='CAMPUS')
            db.add(job); await db.flush()
            jd = JobDescriptionVersion(job_id=job.id,version_number=1,publication_status='PUBLISHED',
                raw_text='F2 LOCAL_FIXTURE DEMO JD: '+item['title']+' / '+' / '.join(item['topics']),
                metadata_json={'fixture':True,'demo':True,'mode':'LOCAL_FIXTURE'},
                structured_data_json={'matching_constraints':dict(locations=[item['location']],
                    education_minimum=item['qualification'],majors=[],graduation_years=[])})
            db.add(jd); await db.flush()
            for topic in item['topics']:
                db.add(JobRequirement(job_description_version_id=jd.id,statement='DEMO role requires '+topic,
                    normalized_value=topic,requirement_type='SKILL'))
                db.add(JobTag(job_id=job.id,tag=topic))
            for question,points in (
                ('TEST/DEMO: How would you cache document search results in Redis while keeping results fresh?',
                 'Cache key design\nTTL and invalidation\nCache misses and fallback\nConcurrency'),
                ('TEST/DEMO: How would you evaluate a document retrieval application built with Python?',
                 'Ground truth queries\nRetrieval recall and ranking\nFailure cases\nLatency and test isolation')):
                db.add(InterviewQuestion(company_id=company.id,job_id=job.id,question=question,
                    standard_answer=points,review_status='APPROVED',difficulty='MEDIUM',category='Engineering'))
        await db.commit()
        other_companies = list((await db.scalars(select(Company.id).where(Company.id!=company.id))).all())
    core = JobMatchingService(factory,router)
    run = await core.create(user_id,MatchRequest(request_key='F2-LIVE-MODEL-DEMO',candidate_profile_version_id=profile_id,
        filters=MatchFilters(locations=['北京'],campus_types=['CAMPUS'],exclude_company_ids=other_companies)))
    return await finish_matching(core,run,user_id,profile_id)


async def finish_matching(core,run,user_id,profile_id):
    eligibility = {c['job_id']:c['eligibility_status'] for c in run['candidates']+run['excluded_candidates']}
    assert sorted(eligibility.values()) == ['NOT_SATISFIED','SATISFIED','UNKNOWN']
    result = await core.generate(user_id,run['id'])
    if result['status']!='COMPLETED': raise RuntimeError(result.get('error','job_matching_failed'))
    assert eligibility == {c['job_id']:c['eligibility_status'] for c in result['candidates']+result['excluded_candidates']}
    assert all(c['pre_rank'] and c['agent_rank'] and c['final_rank'] and c['recommendation'] for c in result['candidates'])
    assert all(c['agent_rank'] is None and c['recommendation'] is None for c in result['excluded_candidates'])
    selected = next(c for c in result['candidates'] if c['eligibility_status']=='SATISFIED')
    return dict(run_id=run['id'],status=result['status'],company_id=selected['company_id'],job_id=selected['job_id'],
        ranks=[dict(job_id=c['job_id'],eligibility=c['eligibility_status'],pre=c['pre_rank'],agent=c['agent_rank'],
            final=c['final_rank'],band=c['recommendation']['fit_band']) for c in result['candidates']],
        excluded_count=len(result['excluded_candidates']),profile_id=profile_id)


ANSWERS = (
    'TEST fixed smoke answer: I would cache normalized query results in Redis with a TTL and retrieve documents on a cache miss. I have not explained concurrent invalidation yet.',
    'TEST fixed smoke answer: Include document version in the cache key, use TTL and invalidate on document changes. Use a single-flight lock for a hot miss, do not cache failures, and test concurrent readers and version updates.',
    'TEST fixed smoke answer: Build a small labeled query set and compare expected documents to retrieval results. Test missing documents, stale cache entries and irrelevant results. Measure retrieval latency separately from generation and add repeatable integration tests. I have not specified a large-scale evaluation dataset.'
)


async def interview(factory,router,user_id,match):
    core = MockInterviewService(factory,router)
    detail = await core.create(user_id,CreateSessionRequest(company_id=match['company_id'],job_id=match['job_id'],
        question_count=3,title='F2 TEST / LIVE MODEL / LOCAL_FIXTURE Interview'))
    session_id = detail['id']; pinned = deepcopy(detail['context_snapshot'])
    await core.start(session_id,user_id)
    event_names=[]
    for index in range(3):
        current = await core.detail(session_id,user_id)
        if current['status']=='COMPLETED': break
        active = current['current_question']
        request = SendMessageRequest(question_id=active['question_id'],idempotency_key=f'F2-ANSWER-{index}',content=ANSWERS[index])
        chunks = [chunk async for chunk in core.stream(session_id,user_id,request)]
        events = [(chunk.split('\n')[0][7:],json.loads(chunk.split('\ndata: ',1)[1])) for chunk in chunks]
        error = next((data for event,data in events if event=='error'),None)
        if error: raise RuntimeError(error['code'])
        assert events[0][0]=='status' and events[-1][0]=='done'
        assert any(event=='evaluation' for event,_ in events), 'Smoke budget ended before required evaluation'
        event_names.extend(event for event,_ in events)
        assert (await core.detail(session_id,user_id))['context_snapshot']==pinned
    final = await core.detail(session_id,user_id)
    assert final['status']=='COMPLETED' and final['active_question_id'] is None
    assert all(q['status'] not in ('PENDING','ASKED','ANSWERED') for q in final['questions'])
    assert final['completion_json']['evaluated_count']>=2
    assert final['completion_json']['follow_up_count']<=1
    async with factory() as db:
        answers=list((await db.scalars(select(InterviewAnswer).where(InterviewAnswer.session_id==session_id))).all())
        evaluations=list((await db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id==session_id))).all())
        assert len(evaluations)==len(answers)
        assert all(e.session_question_id==next(a.session_question_id for a in answers if a.id==e.answer_id) for e in evaluations)
        assert all(e.provider!='replay' and all(1<=getattr(e,d)<=5 for d in DIMENSIONS) for e in evaluations)
    return dict(session_id=session_id,status=final['status'],statistics=final['completion_json'],events=event_names,
        snapshot_fingerprint=final['snapshot_fingerprint'],answer_mode='TEST_FIXED_INPUT')


async def coach(factory,router,user_id,session_id):
    service = InterviewReportService(factory,router)
    result = await service.generate(session_id,user_id)
    if result['status']!='COMPLETED': raise RuntimeError(result.get('error','interview_coach_failed'))
    async with factory() as db:
        questions,evaluations = await interview_records(db,session_id)
        assert result['statistics']==deterministic_statistics(questions,evaluations)
        ids={e['id'] for e in evaluations}
        for category in ('strengths','weaknesses','recurring_issues','job_alignment'):
            for finding in result['coach_analysis'][category]:
                refs=set(finding['supporting_evaluation_ids'])
                assert refs and refs<=ids and finding['occurrence_count']==len(refs)
                if category=='recurring_issues': assert len(refs)>=2
        proposals=list((await db.scalars(select(MemoryProposal).where(MemoryProposal.report_id==result['id']))).all())
        assert all(p.status=='PENDING' for p in proposals)
        assert not await db.scalar(select(func.count(UserMemoryItem.id)).where(UserMemoryItem.user_id==user_id))
        assert not await db.scalar(select(func.count(UserSkillProfile.id)).where(UserSkillProfile.user_id==user_id))
        observations=await db.scalar(select(func.count(InterviewPerformanceObservation.id)).where(InterviewPerformanceObservation.report_id==result['id']))
        assert proposals and observations, 'Coach must produce supported observations and pending proposals for this smoke'
    return dict(report_id=result['id'],status=result['status'],statistics=result['statistics'],
        observations=observations,proposal_count=len(proposals),proposal_states=[p.status for p in proposals],
        no_automatic_memory=True)


async def audits(factory):
    async with factory() as db:
        rows=(await db.execute(select(ExperienceAgentRun,ExperienceAgentStepRun)
            .join(ExperienceAgentStepRun,ExperienceAgentStepRun.agent_run_id==ExperienceAgentRun.id)
            .order_by(ExperienceAgentStepRun.id))).all()
        return [dict(run_id=run.id,role=run.agent_role,run_status=run.status,run_error=run.error_message,
            step_id=step.id,prompt=step.prompt_name,prompt_version=step.prompt_version,
            provider=step.provider_name,model=step.model_name,status=step.status,latency_ms=step.latency_ms,
            structured_repair_count=step.structured_retry,usage=step.token_usage_json,error=step.error_message)
            | dict(model_invocations=(step.output_json or {}).get('model_invocations',[]),
                validation_diagnostics=(run.output_json or {}).get('validation_failure'),
                skill_state_resolution=(run.output_json or {}).get('skill_state_resolution',[]))
            for run,step in rows]


async def main(stage_limit,resume_matching=False,resume_budget=False):
    assert make_url(settings.database_url).database==DATABASE, 'F2 isolated database guard failed'
    engine=create_async_engine(settings.database_url)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    observer=Observer()
    output=dict(database=DATABASE,model_mode='LIVE',tool_mode='REPLAY',data_mode='LOCAL_FIXTURE / TEST / DEMO',
        live_search=False,success=False,stages={})
    stage='setup'
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()=='0024'
            if not (resume_matching or resume_budget):
                assert not (await conn.execute(text('SELECT count(*) FROM experience_agent_runs'))).scalar_one(), 'Use a fresh F2 DB; no automatic smoke regeneration'
            elif resume_matching:
                assert (await conn.execute(text('SELECT count(*) FROM interview_sessions'))).scalar_one()==0
            else:
                assert (await conn.execute(text('SELECT count(*) FROM interview_sessions'))).scalar_one()==1
                async with factory() as db:
                    previous=await db.get(InterviewSession,1)
                    assert previous.status=='COMPLETED' and previous.interview_plan_json.get('budget_exhausted')
                    assert settings.interview_max_model_tokens==500000
                    output['prior_budget_session']=dict(id=1,status=previous.status,
                        reason='Existing conservative reservation budget reached; no validator/model failure')
        research_router=observer.attach(build_model_router())
        profile_router=observer.attach(build_resume_profile_router())
        match_router=observer.attach(build_job_matching_router())
        interview_router=observer.attach(build_interview_router())
        coach_router=observer.attach(build_interview_coach_router())
        for configured in (research_router,profile_router,match_router,interview_router,coach_router):
            assert all(p.model_name=='deepseek-v4-pro-ga-260813' for p in configured.routes.values())
            assert all(p.model_name=='doubao-seed-2-1-turbo-260628' for p in configured.fallback_routes.values())
        if resume_matching or resume_budget:
            async with factory() as db:
                version=(await db.scalars(select(CandidateProfileVersion))).one()
                owner=(await db.scalars(select(CandidateProfile))).one()
                assert owner.current_version_id==version.id and version.model=='deepseek-v4-pro-ga-260813'
                profile_run=await db.get(ExperienceAgentRun,version.agent_run_id)
                normalized=await CandidateProfileService(db).serialize(owner.user_id)
                states=profile_run.output_json['skill_state_resolution']
                output['stages']['research']=dict(task_id=1,snapshot_id=1,jd_id=1,status='COMPLETED',
                    resumed_persisted_result=True,data_mode='LOCAL_FIXTURE',tool_mode='REPLAY')
                output['stages']['profile']=dict(status='COMPLETED',version_id=version.id,user_id=owner.user_id,
                    resume_id=version.source_resume_id,skill_state_resolution=states,
                    normalized_skill_states=sum(s['normalized'] for s in states),
                    normalized_counts={k:len(v) for k,v in normalized['normalized'].items()},resumed_persisted_result=True)
        else:
            stage='PublicDataResearchAgent'; print('F2 stage: '+stage,flush=True)
            output['stages']['research']=await research(factory,research_router)
            if stage_limit=='research': return output
            stage='ResumeProfileAgent'; print('F2 stage: '+stage,flush=True)
            output['stages']['profile']=await profile(factory,profile_router)
        user_id=output['stages']['profile']['user_id']
        stage='JobMatchingAgent'; print('F2 stage: '+stage,flush=True)
        if resume_budget:
            match_service=JobMatchingService(factory,match_router)
            fixed_run=await match_service.detail(user_id,1)
            assert fixed_run['status']=='COMPLETED'
            output['stages']['matching']=await finish_matching(match_service,fixed_run,user_id,output['stages']['profile']['version_id'])
            output['stages']['matching']['resumed_persisted_result']=True
        else:
            output['stages']['matching']=await matching(factory,match_router,user_id,output['stages']['profile']['version_id'],1 if resume_matching else None)
        stage='InterviewMainAgent / AnswerEvaluationAgent'; print('F2 stage: '+stage,flush=True)
        output['stages']['interview']=await interview(factory,interview_router,user_id,output['stages']['matching'])
        stage='SummaryCoachAgent'; print('F2 stage: '+stage,flush=True)
        output['stages']['coach']=await coach(factory,coach_router,user_id,output['stages']['interview']['session_id'])
        output['success']=True
    except Exception as exc:
        code=getattr(exc,'code',type(exc).__name__)
        message=str(exc)[:1000]
        for key in (settings.llm_api_key,settings.jwt_secret_key):
            if key: message=message.replace(key,'[REDACTED]')
        output.update(failed_stage=stage,error_code=code,error=message)
    finally:
        output['agent_audit']=await audits(factory)
        output['http_metrics']=observer.calls
        output['successful_roles']=sorted({a['role'] for a in output['agent_audit'] if a['run_status']=='SUCCEEDED'})
        output['not_successful_roles']=[r for r in ROLES if r not in output['successful_roles']]
        await observer.close(); await engine.dispose()
        print('F2_RESULT '+json.dumps(output,ensure_ascii=False),flush=True)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-limit',choices=('research','all'),default='all')
    parser.add_argument('--resume-after-matching-timeout',action='store_true',help='Explicit one-time continuation; preserve failed attempt audit')
    parser.add_argument('--resume-after-interview-budget',action='store_true',help='New session with scoped smoke budget; preserve prior completed budget session')
    args=parser.parse_args()
    assert not (args.resume_after_matching_timeout and args.resume_after_interview_budget)
    result=asyncio.run(main(args.stage_limit,args.resume_after_matching_timeout,args.resume_after_interview_budget))
    raise SystemExit(0 if result['success'] else 1)
