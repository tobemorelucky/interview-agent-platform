"""Read-only post-batch DB / quota / invalid-reference audit. No model calls."""
import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import asyncpg
from sqlalchemy.engine import make_url

API=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(API/'src'))
from interview_api.core.config import settings
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.interview.adaptive import AdaptiveSelection,resolve_selection,validate_decision


async def inspect(database,meter_path,output):
    assert database.startswith('interview_agent_v21_v23_live_ab_') and database.replace('_','').isalnum()
    meter=json.loads(meter_path.read_text(encoding='utf-8'))
    conn=await asyncpg.connect(make_url(settings.database_url).set(drivername='postgresql',database=database).render_as_string(hide_password=False),timeout=5)
    try:
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            async def rows(table,where):
                return [json.loads(row[0]) for row in await conn.fetch(f'SELECT row_to_json(t)::text FROM {table} t WHERE {where} ORDER BY id')]
            result=dict(database=database,revision=await conn.fetchval('select version_num from alembic_version'),meter=meter)
            for table in ('interview_sessions','interview_context_snapshots','interview_answers','interview_evaluations','interview_session_questions','interview_agent_calls'):
                result[table]=await rows(table, 'id IN (4,5)' if table=='interview_sessions' else
                    'interview_session_id IN (4,5)' if table=='interview_context_snapshots' else 'session_id IN (4,5)')
            for table in ('capability_memories','capability_memory_versions'):
                result[table]=await rows(table,'true')
            result['live_steps']=await rows('experience_agent_step_runs',f"model_name='{meter['model']}'")
            ids=[r['agent_run_id'] for r in result['live_steps']]
            result['live_runs']=await rows('experience_agent_runs','id IN ('+','.join(map(str,ids))+')')
            quota=await conn.fetch("SELECT call_key,route,model,status,actual_tokens,charged_tokens FROM model_admissions WHERE owner_key='user:1' ORDER BY created_at")
            result['quota']=[dict(r) for r in quota]
            assert len(quota)==6 and all(r['status']=='SETTLED' for r in quota)
            assert sum(r['actual_tokens'] for r in quota)==sum(r['total_tokens'] for r in meter['http'])==42192
            assert all(r['status']=='SUCCEEDED' and r['structured_retry']==0 for r in result['live_steps'])
            for session in result['interview_sessions']:
                plan=session['interview_plan_json']
                assert not plan.get('answer_processing') and not plan.get('model_call_reservations')
                assert not plan.get('reserved_model_tokens') and not plan.get('unreported_model_tokens')
            for snapshot in result['interview_context_snapshots']:
                assert digest(snapshot['context_json'])==snapshot['content_fingerprint']
                assert snapshot['context_json']['adaptive_policy']['context_projection']=='ROLE_CONTEXT_V1'
                assert snapshot['context_json']['budgets']['max_model_tokens']==200000
            answers=result['interview_answers'];evaluations=result['interview_evaluations']
            assert all(a['status']=='COMPLETED' for a in answers)
            assert len({a['session_question_id'] for a in answers})==len(answers)
            assert len({e['answer_id'] for e in evaluations})==len(evaluations)
            assert sum(e['session_id']==5 for e in evaluations)==1
            # Reconstruct the complete validation catalog from retained old wire
            # and actual frozen Blueprint. These are offline rejected copies.
            record=next(r for r in meter['http'] if r['phase']=='B_main_old')
            full=json.loads(record['request_messages'][1]['content'])['input']
            context=next(s['context_json'] for s in result['interview_context_snapshots'] if s['interview_session_id']==4)
            full['snapshot']['adaptive_policy']=context['adaptive_policy']
            full['snapshot']['adaptive_blueprint']=context['adaptive_blueprint']
            by_id={t['knowledge_id']:t for t in context['adaptive_blueprint']['targets']}
            grouped={c['target_knowledge_id']:dict(target=by_id[c['target_knowledge_id']],strategies=c['strategies']) for c in full['adaptive']['candidates']}
            full['adaptive']['choices']=list(grouped.values())
            validate_decision(resolve_selection(AdaptiveSelection.model_validate(record['business_output']),full),full)
            result['offline_rejections']={}
            for kind in ('unknown_ref','unknown_candidate'):
                bad=deepcopy(record['business_output'])
                if kind=='unknown_ref':bad['supporting_refs'].append('fact:UNKNOWN')
                else:bad['candidate_id']='C_UNKNOWN'
                try:validate_decision(resolve_selection(AdaptiveSelection.model_validate(bad),full),full)
                except Exception as error:result['offline_rejections'][kind]=getattr(error,'code',type(error).__name__)
                else:raise AssertionError('Invalid copy accepted')
            result['quota_reconciled']=True
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            print(json.dumps(dict(quota_reconciled=True,logical=meter['logical_calls'],http=meter['http_attempts'],tokens=sum(r['actual_tokens'] for r in quota),
                sessions=[dict(id=s['id'],status=s['status'],version=s['state_version'],active_question_id=s['active_question_id']) for s in result['interview_sessions']],
                answers=len(answers),evaluations=len(evaluations),offline_rejections=result['offline_rejections']),ensure_ascii=True))
    finally:await conn.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--meter',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to((API.parents[1]/'.runtime/acceptance').resolve()):raise SystemExit('Fresh ignored output required')
    asyncio.run(inspect(args.database,args.meter,args.output))
