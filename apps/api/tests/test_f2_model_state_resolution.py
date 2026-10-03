"""F2 fixes: availability-only model selection and downward-only skill states."""
import asyncio
import json
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from interview_api.infrastructure.models.provider import CompatibleModelProvider, ReplayModelProvider, ModelProviderError
from interview_api.infrastructure.models.router import ModelRouter, EXTRACT_ROUTE, RESUME_PROFILE_ROUTE
from interview_api.infrastructure.models.structured import generate_structured, StructuredOutputError
from interview_api.modules.public_career.research.schemas import ResearchQuality
from interview_api.modules.public_career.research.audit import ResearchModelCalls
from interview_api.modules.candidate_profile.evidence import normalize_skill_evidence_states, resolve_and_validate
from interview_api.modules.candidate_profile.schemas import CandidateProfileDelta
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.models import CandidateProfileVersion
from interview_api.modules.candidate_profile.prompts import CandidatePromptRegistry
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
from test_m3_candidate_profile import seed, response


@pytest.mark.parametrize('status,code,message,allowed', [
    (404,'ModelNotFound','model not found',True),
    (400,'InvalidEndpoint','endpoint does not exist',True),
    (429,'RateLimitExceeded','quota',True),
    (503,'ServiceUnavailable','unavailable',True),
    (504,'GatewayTimeout','upstream unavailable',True),
    (401,'InvalidApiKey','unauthorized',False),
    (403,'PermissionDenied','forbidden',False),
    (400,'InvalidParameter','invalid response format',False),
    (500,'InternalError','not classified as retriable',False),
])
def test_availability_only_fallback(status,code,message,allowed):
    async def run():
        requests=[]
        def respond(request):
            model=json.loads(request.content)['model']; requests.append(model)
            if model=='primary':
                return httpx.Response(status,json={'error':dict(code=code,message=message)})
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"suitable":true}'}}],
                'usage':{'prompt_tokens':4,'completion_tokens':2,'total_tokens':6,
                    'completion_tokens_details':{'reasoning_tokens':1}}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            providers={name:CompatibleModelProvider(base_url='https://model.invalid/v1',api_key='fixture',
                model=name,timeout_seconds=1,max_output_tokens=200,client=client) for name in ('primary','fallback')}
            router=ModelRouter({EXTRACT_ROUTE:providers['primary']},fallback_routes={EXTRACT_ROUTE:providers['fallback']})
            if allowed:
                result=await generate_structured(router,EXTRACT_ROUTE,system='JSON',payload={},response_model=ResearchQuality)
                assert requests==['primary','fallback'] and result.structured_retry==0
                assert router.resolve(EXTRACT_ROUTE).model_name=='fallback'
                assert result.invocations[0]['status']=='FAILED'
                assert result.invocations[1]['fallback_reason']==f'provider_availability_http_{status}'
                assert result.usage['reasoning_tokens']==1
            else:
                with pytest.raises(ModelProviderError) as caught:
                    await generate_structured(router,EXTRACT_ROUTE,system='JSON',payload={},response_model=ResearchQuality)
                assert not caught.value.availability_reason and requests==['primary']
    asyncio.run(run())


@pytest.mark.parametrize('failure',['schema','timeout','business'])
def test_non_availability_failure_does_not_fallback(failure):
    class FailedProvider(ReplayModelProvider):
        async def generate(self,messages,*,schema):
            self.calls+=1
            raise ModelProviderError('model_timeout' if failure=='timeout' else 'UNSUPPORTED_SKILL_STATE','fixture')
    async def run():
        primary=ReplayModelProvider([{},{}]) if failure=='schema' else FailedProvider([])
        fallback=ReplayModelProvider([{'suitable':True}])
        router=ModelRouter({EXTRACT_ROUTE:primary},fallback_routes={EXTRACT_ROUTE:fallback})
        with pytest.raises(StructuredOutputError if failure=='schema' else ModelProviderError):
            await generate_structured(router,EXTRACT_ROUTE,system='JSON',payload={},response_model=ResearchQuality)
        assert fallback.calls==0 and primary.calls==(2 if failure=='schema' else 1)
    asyncio.run(run())


@pytest.mark.parametrize('proposed,concrete,expected',[
    ('STRONG_EVIDENCE',0,'MENTIONED'),('STRONG_EVIDENCE',1,'DEMONSTRATED'),
    ('STRONG_EVIDENCE',2,'STRONG_EVIDENCE'),('DEMONSTRATED',2,'DEMONSTRATED'),
    ('MENTIONED',2,'MENTIONED'),
])
def test_skill_state_only_converges_down(proposed,concrete,expected):
    evidence={'mention':dict(key='mention',section='skills',excerpt='Redis')}
    for i in range(concrete): evidence[str(i)]=dict(key=str(i),section='projects',excerpt='Used Redis cache')
    body={'skills':[dict(label='Redis',statement='Redis',evidence_ids=list(evidence),proposed_evidence_state=proposed)]}
    delta=CandidateProfileDelta.model_validate(body)
    diagnostics=normalize_skill_evidence_states(delta,evidence)
    resolve_and_validate(delta,evidence)
    assert delta.skills[0].evidence_state==expected
    assert diagnostics[0]['maximum_supported_state']==('MENTIONED','DEMONSTRATED','STRONG_EVIDENCE')[concrete]
    assert diagnostics[0]['referenced_evidence_ids']==list(evidence)
    assert delta.model_dump()['skills'][0]['evidence_state']==expected


def test_duplicate_or_uncited_evidence_does_not_increase_state():
    evidence={str(i):dict(key=str(i),section='projects',excerpt='Redis') for i in range(2)}
    delta=CandidateProfileDelta.model_validate({'skills':[dict(label='Redis',statement='Redis',
        evidence_ids=['0','0'],proposed_evidence_state='STRONG_EVIDENCE')]})
    diagnostics=normalize_skill_evidence_states(delta,evidence)
    assert diagnostics[0]['maximum_supported_state']=='DEMONSTRATED'
    assert delta.skills[0].evidence_state=='DEMONSTRATED'


def test_unknown_skill_reference_rejected_with_diagnostics():
    delta=CandidateProfileDelta.model_validate({'skills':[dict(label='Redis',statement='Redis',
        evidence_ids=['invented'],proposed_evidence_state='STRONG_EVIDENCE')]})
    with pytest.raises(ValueError) as caught: normalize_skill_evidence_states(delta,{})
    assert caught.value.diagnostics['referenced_evidence_ids']==['invented']
    assert caught.value.diagnostics['proposed_state']=='STRONG_EVIDENCE'
    assert str(caught.value)=='UNKNOWN_CANDIDATE_EVIDENCE'


def test_unsupported_strength_still_rejected_after_skill_resolution():
    delta=CandidateProfileDelta.model_validate({'strengths':[dict(statement='Expert Redis',evidence_ids=['x'])]})
    evidence={'x':dict(key='x',section='skills',excerpt='Redis')}
    normalize_skill_evidence_states(delta,evidence)
    with pytest.raises(ValueError,match='UNSUPPORTED_STRENGTH'): resolve_and_validate(delta,evidence)


def test_profile_prompt_history_is_available():
    registry=CandidatePromptRegistry()
    assert registry.get('resume_profile').version=='1.0.1'
    assert registry.get('resume_profile','1.0.0').version=='1.0.0'
    assert 'proposed_evidence_state' not in registry.get('resume_profile','1.0.0').system


def test_profile_downward_resolution_persisted_and_audited(database_url):
    async def run():
        engine=create_async_engine(database_url)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            _,resume_id=await seed(factory)
            body=response(resume_id); body['skills'][0]['evidence_state']='STRONG_EVIDENCE'
            result=await ResumeProfileAgent(factory,ModelRouter({RESUME_PROFILE_ROUTE:ReplayModelProvider([body])})).run(resume_id)
            assert result['status']=='COMPLETED'
            async with factory() as db:
                version=await db.get(CandidateProfileVersion,result['version_id'])
                audit=await db.get(ExperienceAgentRun,version.agent_run_id)
                assert version.snapshot_json['skills'][0]['evidence_state']=='DEMONSTRATED'
                record=audit.output_json['skill_state_resolution'][0]
                assert record['proposed_state']=='STRONG_EVIDENCE' and record['normalized']
                assert record['maximum_supported_state']=='DEMONSTRATED'
        finally: await engine.dispose()
    asyncio.run(run())


def test_fallback_actual_model_and_reason_in_agent_audit(database_url):
    class Unavailable(ReplayModelProvider):
        model_name='primary-demo'
        async def generate(self,messages,*,schema):
            error=ModelProviderError('model_request_failed','fixture')
            error.availability_reason='provider_availability_http_429'
            raise error
    async def run():
        engine=create_async_engine(database_url); factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            async with factory() as db:
                audit=ExperienceAgentRun(agent_role='F2_TEST_FIXTURE',graph_name='test',graph_version='1',status='RUNNING')
                db.add(audit); await db.commit(); run_id=audit.id
            fallback=ReplayModelProvider([{'suitable':True}]); fallback.model_name='fallback-demo'
            router=ModelRouter({EXTRACT_ROUTE:Unavailable([])},fallback_routes={EXTRACT_ROUTE:fallback})
            await ResearchModelCalls(factory,router,run_id).call('quality',EXTRACT_ROUTE,{},ResearchQuality)
            async with factory() as db:
                step=await db.scalar(select(ExperienceAgentStepRun).where(ExperienceAgentStepRun.agent_run_id==run_id))
                assert step.model_name=='fallback-demo'
                calls=step.output_json['model_invocations']
                assert calls[0]['model']=='primary-demo' and calls[0]['status']=='FAILED'
                assert calls[1]['model']=='fallback-demo' and calls[1]['fallback_reason']=='provider_availability_http_429'
        finally: await engine.dispose()
    asyncio.run(run())
