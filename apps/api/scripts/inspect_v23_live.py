"""Read-only session/ledger checkpoint for the bounded DEMO Live batch."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import asyncpg
API=Path(__file__).resolve().parents[1];ROOT=API.parents[1]
sys.path.insert(0,str(API/'src'))
from sqlalchemy.engine import make_url
from interview_api.core.config import settings
from interview_api.modules.candidate_profile.evidence import digest


async def inspect(name,output,meter):
    if not name.startswith('interview_agent_v21_v23_live_') or not name.replace('_','').isalnum():
        raise ValueError('Isolated Live database only')
    conn=await asyncpg.connect(make_url(settings.database_url).set(drivername='postgresql',database=name).render_as_string(hide_password=False),timeout=5)
    try:
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            async def rows(table,where):
                values=await conn.fetch(f'SELECT row_to_json(t)::text FROM {table} t WHERE {where} ORDER BY id')
                return [json.loads(v[0]) for v in values]
            sessions=await rows('interview_sessions','id=1')
            session=sessions[0]
            snapshots=await rows('interview_context_snapshots','interview_session_id=1')
            assert snapshots[0]['content_fingerprint']==digest(snapshots[0]['context_json'])
            value=dict(database=name,at=datetime.now(timezone.utc).isoformat(),session=session,snapshot=snapshots[0])
            for table in ('interview_session_questions','interview_answers','interview_evaluations','interview_agent_calls'):
                value[table]=await rows(table,'session_id=1')
            for table in ('capability_memories','final_interview_reports','memory_proposals'):
                value[table]=await rows(table,'user_id=1')
            value['capability_memory_versions']=await rows('capability_memory_versions','memory_id IN (SELECT id FROM capability_memories WHERE user_id=1)')
            value['quota']=[dict(r) for r in await conn.fetch("SELECT call_key,route,model,status,actual_tokens,charged_tokens FROM model_admissions WHERE owner_key='user:1' ORDER BY created_at")]
            answers=value['interview_answers'];evaluations=value['interview_evaluations'];questions=value['interview_session_questions']
            assert len({e['answer_id'] for e in evaluations})==len(evaluations),'DUPLICATE_EVALUATION'
            assert len({a['session_question_id'] for a in answers if a['status']=='COMPLETED'})==sum(a['status']=='COMPLETED' for a in answers),'DUPLICATE_VALID_ANSWER'
            assert all(q['user_id']==1 for q in questions) if questions and 'user_id' in questions[0] else True
            value['meter']=json.loads(meter.read_text(encoding='utf-8'))
            measured=[r for r in value['meter']['http'] if r.get('total_tokens') is not None]
            value['quota_reconciled']=all(q['status']=='SETTLED' for q in value['quota']) and sum(q['actual_tokens'] or 0 for q in value['quota'])==sum(r['total_tokens'] for r in measured)
            output.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            current=next((q for q in questions if q['id']==session['active_question_id']),None)
            print(json.dumps(dict(status=session['status'],state_version=session['state_version'],answers=len(answers),evaluations=len(evaluations),questions=len(questions),
                current_question=current['question'] if current else None,logical=value['meter']['logical_calls'],http=value['meter']['http_attempts'],tokens=value['meter']['conservative_charged_tokens'],
                quota_reconciled=value['quota_reconciled'],stop=value['meter']['stop_reason']),ensure_ascii=False))
    finally:await conn.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--output',required=True);p.add_argument('--meter',required=True);a=p.parse_args()
    output=Path(a.output).resolve();meter=Path(a.meter).resolve()
    if output.exists() or not output.is_relative_to((ROOT/'.runtime/acceptance').resolve()):raise SystemExit('Fresh ignored checkpoint required')
    asyncio.run(inspect(a.database,output,meter))
