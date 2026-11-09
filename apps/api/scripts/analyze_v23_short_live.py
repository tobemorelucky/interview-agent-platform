"""Read-only artifact/context and persisted audit analysis; never calls models."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import asyncpg
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from interview_api.core.config import settings
from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


async def main(file):
    data = json.loads(file.read_text(encoding='utf-8'))
    for record in data['meter']['http']:
        user = json.loads(record['request_messages'][1]['content'])
        payload = user['input']
        adaptive, snapshot = payload.get('adaptive', {}), payload['snapshot']
        output = record.get('business_output', {})
        pair = next((p for p in adaptive.get('candidates', [])
            if p['candidate_id'] == output.get('candidate_id')), None)
        refs = output.get('supporting_refs', [])
        if record['route'] == 'interview_main':
            assert pair and pair['source']['ref'] in refs
            assert not set(refs) - set(adaptive['allowed_refs'])
            if payload.get('answer'):
                assert set(refs) & {adaptive['current_answer_ref'], adaptive['current_evaluation_ref']}
        print(json.dumps(dict(http=record['number'], route=record['route'],
            input_chars=record['input_chars'], system_chars=len(record['request_messages'][0]['content']),
            history_count=len(payload.get('history', [])), history_chars=size(payload.get('history', [])),
            snapshot_chars=size(snapshot), adaptive_chars=size(adaptive),
            memory_plan_chars=size(snapshot.get('memory_guided_plan')),
            choices=len(adaptive.get('choices', [])), candidates=len(adaptive.get('candidates', [])),
            schema_chars=size(user.get('schema')), refs=refs, pair=pair,
            strategy=output.get('strategy'), knowledge_observations=output.get('knowledge_observations'),
            cumulative_tokens=sum(r['total_tokens'] for r in data['meter']['http'][:record['number']])),
            ensure_ascii=False))
    name = data['database']
    assert name.startswith('interview_agent_v21_v23_live_short_')
    connection = await asyncpg.connect(make_url(settings.database_url).set(
        drivername='postgresql', database=name).render_as_string(hide_password=False), timeout=5)
    try:
        async with connection.transaction(readonly=True):
            for model in (ExperienceAgentRun, ExperienceAgentStepRun):
                rows = await connection.fetch(f'SELECT row_to_json(t)::text FROM {model.__tablename__} t ORDER BY id')
                for row in rows:
                    item = json.loads(row[0])
                    print(json.dumps(dict(table=model.__tablename__, **{k:item[k] for k in
                        ('id', 'status', 'agent_role', 'prompt_version', 'model_name', 'token_usage_json', 'structured_retry')
                        if k in item}), ensure_ascii=False))
            print('revision=' + await connection.fetchval('SELECT version_num FROM alembic_version'))
    finally:
        await connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('file', type=Path)
    asyncio.run(main(parser.parse_args().file))
