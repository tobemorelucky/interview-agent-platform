"""Public structured-generation and interview service seams; Replay only at model I/O."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import pytest

from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.infrastructure.models.structured import generate_structured, StructuredOutputError
from interview_api.modules.interview.adaptive import AdaptiveSelection


def recorded_failure():
    return json.loads((Path(__file__).parent/'fixtures/v23_live_supporting_refs_failure.json').read_text(encoding='utf-8'))


def test_recorded_seventeen_refs_can_be_selected_once_without_changing_the_decision():
    from interview_api.modules.interview.director_repair import SupportingRefsRepair
    fixture=recorded_failure()
    reduced=deepcopy(fixture['output'])
    reduced['supporting_refs']=['answer:3','evaluation_run:7','requirement:3']

    class Replay(ReplayModelProvider):
        async def generate(self,messages,*,schema):
            if self.calls:
                instruction=json.loads(messages[-1]['content'])
                assert instruction['validation_errors'][0]['path']==['supporting_refs']
                assert instruction['validation_errors'][0]['actual_count']==17
                assert instruction['validation_errors'][0]['limit']==12
                assert instruction['reference_subset_repair']['required_source_ref']=='requirement:3'
            return await super().generate(messages,schema=schema)

    provider=Replay([fixture['output'],reduced])
    result=asyncio.run(generate_structured(ModelRouter({'interview_main':provider}), 'interview_main',
        system='Replay of retained LIVE failure',payload=fixture['payload'],response_model=AdaptiveSelection,
        repair_policy=SupportingRefsRepair(fixture['payload'])))
    assert result.parsed.supporting_refs==['answer:3','evaluation_run:7','requirement:3']
    assert result.parsed.proposed_question.model_dump()==fixture['output']['proposed_question']
    assert result.parsed.candidate_id=='C_6fbaf249a8e8a389386dd23b'
    assert result.structured_retry==1
    assert result.invocations[-1]['reference_subset_repair']['original_count']==17
    assert result.invocations[-1]['reference_subset_repair']['retained_count']==3


def generate(outputs, fixture=None, *, policy=True):
    from interview_api.modules.interview.director_repair import SupportingRefsRepair
    fixture=fixture or recorded_failure()
    provider=ReplayModelProvider(outputs)
    return asyncio.run(generate_structured(ModelRouter({'interview_main':provider}), 'interview_main',
        system='DEMO deterministic boundary test',payload=fixture['payload'],response_model=AdaptiveSelection,
        repair_policy=SupportingRefsRepair(fixture['payload']) if policy else None))


def subset():
    value=recorded_failure()['output']
    value['supporting_refs']=['answer:3','requirement:3']
    return value


def test_twelve_is_accepted_without_repair_and_thirteen_is_not_silently_truncated():
    raw=recorded_failure()['output']
    raw['supporting_refs']=['answer:3','evaluation_run:7','requirement:3',
        'fact:1','fact:2','fact:3','fact:4','fact:5','fact:6','fact:7','question:1','question:2']
    assert len(generate([raw]).parsed.supporting_refs)==12
    raw['supporting_refs'].append('question:3')
    with pytest.raises(StructuredOutputError) as caught:
        generate([raw,raw])
    assert caught.value.validation_errors[0]['path']==['supporting_refs']
    assert caught.value.validation_errors[0]['actual_count']==13
    assert len(caught.value.invocations)==2 and caught.value.retried


@pytest.mark.parametrize('change',[
    'unknown_ref','new_allowed_ref','drop_source','drop_current','duplicate_ref',
    'question','expected_points','candidate_id','strategy','reason_tags','extra_field',
])
def test_subset_repair_cannot_rewrite_the_decision_or_discard_essential_support(change):
    fixture=recorded_failure()
    fixed=subset()
    if change=='unknown_ref':fixed['supporting_refs'].append('fact:INVENTED')
    if change=='new_allowed_ref':
        fixture['payload']['adaptive']['allowed_refs'].append('fact:allowed_but_absent')
        fixed['supporting_refs'].append('fact:allowed_but_absent')
    if change=='drop_source':fixed['supporting_refs']=['answer:3']
    if change=='drop_current':fixed['supporting_refs']=['requirement:3']
    if change=='duplicate_ref':fixed['supporting_refs'].append('answer:3')
    if change=='question':fixed['proposed_question']['question']='Agent 新增了另一种无依据问题，不能在修复时改写。'
    if change=='expected_points':fixed['proposed_question']['expected_points'].append('凭空新增依据')
    if change=='candidate_id':fixed['candidate_id']='C_REPLACEMENT'
    if change=='strategy':fixed['strategy']='FOLLOW_UP'
    if change=='reason_tags':fixed['reason_tags']=['GAP_RESOLVED']
    if change=='extra_field':fixed['source_id']=9999
    with pytest.raises(StructuredOutputError):
        generate([fixture['output'],fixed],fixture)


def test_invalid_original_scope_cannot_be_laundered_by_dropping_references():
    fixture=recorded_failure()
    fixture['output']['supporting_refs'][-1]='fact:UNKNOWN'
    with pytest.raises(StructuredOutputError):generate([fixture['output'],subset()],fixture)
    fixture=recorded_failure()
    fixture['output']['candidate_id']='C_UNKNOWN'
    fixed=subset();fixed['candidate_id']='C_UNKNOWN'
    with pytest.raises(StructuredOutputError):generate([fixture['output'],fixed],fixture)


def test_other_structured_calls_keep_the_default_fact_inventory_contract():
    with pytest.raises(StructuredOutputError):
        generate([recorded_failure()['output'],subset()],policy=False)


@pytest.mark.parametrize('mode',['repaired','repair_still_invalid','unknown_ref','illegal_candidate'])
def test_interview_can_commit_a_repaired_selection_once(database_url,mode):
    async def run():
        from uuid import uuid4
        from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
        from interview_api.infrastructure.models.provider import ModelResponse
        from interview_api.modules.interview.core_service import MockInterviewService
        from interview_api.modules.interview.schemas import CreateSessionRequest
        from interview_api.modules.public_career.models import JobRequirement,JobDescriptionVersion
        from sqlalchemy import select
        from v22_support import kafka_seed
        from v21_support import AdaptiveReplayProvider

        class LongRefs(AdaptiveReplayProvider):
            injected=False
            async def generate(self,messages,*,schema):
                body=json.loads(messages[-1]['content'])
                if 'invalid_output' in body:
                    value=json.loads(body['invalid_output'])
                    plan=body.get('reference_subset_repair')
                    assert plan is not None, 'Adaptive Main must opt into its narrow subset repair'
                    if mode!='repair_still_invalid':
                        value['supporting_refs']=[plan['required_current_any'][0],plan['required_source_ref']]
                    return ModelResponse(json.dumps(value),{'total_tokens':0})
                result=await super().generate(messages,schema=schema)
                if schema['title']=='AdaptiveSelection' and body['input'].get('answer') and not self.injected:
                    self.injected=True
                    value=json.loads(result.text);adaptive=body['input']['adaptive']
                    if mode=='unknown_ref':
                        value['supporting_refs']=[adaptive['current_answer_ref'],'fact:UNKNOWN']
                        return ModelResponse(json.dumps(value),result.usage)
                    if mode=='illegal_candidate':
                        value['candidate_id']='C_UNKNOWN'
                        return ModelResponse(json.dumps(value),result.usage)
                    pair=next(c for c in adaptive['candidates'] if c['candidate_id']==value['candidate_id'])
                    refs=[adaptive['current_answer_ref'],adaptive['current_evaluation_ref'],pair['source']['ref']]
                    refs+= [r for r in adaptive['allowed_refs'] if r not in refs]
                    assert len(refs)>=17,'DEMO setup must provide 17 real allowed references'
                    value['supporting_refs']=refs[:17]  # Construct oversized MODEL output, not a business repair.
                    return ModelResponse(json.dumps(value),result.usage)
                return result

        engine=create_async_engine(database_url);factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            uid,ctx=await kafka_seed(factory)
            async with factory() as db:
                jd=await db.scalar(select(JobDescriptionVersion).where(JobDescriptionVersion.job_id==ctx['job_id']).order_by(JobDescriptionVersion.version_number.desc()))
                for topic in ('Kafka 消息可靠性','Redis 缓存一致性','MySQL 事务隔离'):
                    for index in range(7):
                        db.add(JobRequirement(job_description_version_id=jd.id,statement=f'DEMO {topic} 场景{index}',requirement_type='SKILL'))
                await db.commit()
            provider=LongRefs()
            core=MockInterviewService(factory,ModelRouter({r:provider for r in ('interview_main','answer_evaluation')}))
            session=await core.create(uid,CreateSessionRequest(company_id=ctx['company_id'],job_id=ctx['job_id'],
                interviewer_skill_id='java_backend',adaptive_interview=True,question_count=4,training_mode='FULL',
                training_request_key=uuid4().hex))
            assert session['context_snapshot']['adaptive_policy']['prompts']['interview_main']['version']=='2.6.0'
            opening=await core.start(session['id'],uid)
            key=uuid4().hex;answer='Kafka 先处理业务再 ACK，我还没有完整设计数据库事务边界。'
            if mode!='repaired':
                from interview_api.core.exceptions import AppError
                with pytest.raises((StructuredOutputError,AppError)) as rejected:
                    await core.answer(session['id'],uid,opening['question_id'],key,answer)
                assert rejected.value.code=={'repair_still_invalid':'structured_output_failed','unknown_ref':'UNKNOWN_DIRECTOR_REFERENCE',
                    'illegal_candidate':'UNKNOWN_DIRECTOR_CANDIDATE'}[mode]
                failed=await core.detail(session['id'],uid)
                assert len(failed['questions'])==1 and failed['status']=='IN_PROGRESS'
                assert failed['pending_answer']['idempotency_key']==key and failed['pending_answer']['retryable']
                assert not failed['interview_plan_json'].get('answer_processing')
                assert not any((m['metadata_json'] or {}).get('type')=='EVALUATION' for m in failed['messages'])
            events=await core.answer(session['id'],uid,opening['question_id'],key,answer)
            detail=await core.detail(session['id'],uid)
            assert detail['status']=='IN_PROGRESS' and len(detail['questions'])==2
            assert detail['pending_answer'] is None
            assert provider.calls.count('AdaptiveEvaluation')==1
            if mode=='repaired':
                assert len(detail['questions'][-1]['evidence']['adaptive_decision']['supporting_refs'])==2
            assert events==await core.answer(session['id'],uid,opening['question_id'],key,answer)
            again=await core.detail(session['id'],uid)
            assert again['state_version']==detail['state_version']
            assert len([m for m in again['messages'] if m['role']=='USER'])==1
            assert again['context_snapshot']==session['context_snapshot']
        finally:await engine.dispose()
    asyncio.run(run())
