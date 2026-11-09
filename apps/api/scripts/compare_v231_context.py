"""Offline paired replay of the seven retained LIVE inputs; no network or DB writes.

Token estimates are usage-calibrated UTF-8/lexical proxies, NOT DeepSeek tokenizer
measurements. Exact baseline usage is retained separately from all predictions.
"""
import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import re
import sys

API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API / 'src'))
from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import ModelRouter
from interview_api.infrastructure.models.structured import generate_structured
from interview_api.modules.interview.adaptive import AdaptiveSelection, AdaptiveEvaluation, resolve_selection, validate_decision, resolve_observations
from interview_api.modules.interview.adaptive_prompts import freeze_prompts
from interview_api.modules.interview.context_projection import model_payload, VERSION


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def measures(text):
    # CJK codepoints, identifier/number pieces, punctuation: independent of raw
    # character length. UTF-8 supplies a second, deliberately different proxy.
    return len(text.encode('utf-8')), len(re.findall(r'[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\w\s]', text))


def wire(system, payload, schema):
    return system + '\n' + dumps(dict(input=payload, schema=schema))


def fixed_dynamic(system, payload, schema):
    fixed = dict(system=system, schema=schema, snapshot=payload['snapshot'])
    return dumps(fixed), dumps({k: v for k, v in payload.items() if k != 'snapshot'})


def forecast(rows):
    """Explicit sensitivity assumptions, not a fitted long-interview model."""
    cases = []
    for level, growth, coach15, coach20, retry15, retry20 in (
        ('low', 200, 15000, 20000, 10000, 12000),
        ('neutral', 450, 25000, 35000, 20000, 25000),
        ('high', 900, 40000, 55000, 40000, 50000)):
        opening = rows[0]['projected_input_'+level] + rows[0]['actual_output_before']
        main = [r for r in rows if r['route']=='interview_main' and r['http']!=1]
        evaluation = [r for r in rows if r['route']=='answer_evaluation']
        main_mean = sum(r['projected_input_'+level]+r['actual_output_before'] for r in main)/len(main)
        evaluation_mean = sum(r['projected_input_'+level]+r['actual_output_before'] for r in evaluation)/len(evaluation)
        def cost(n):
            # N answers, N Main calls including opening: no Main after last answer.
            # Explicit linear per-round growth after the first three, summed.
            return opening+(n-1)*main_mean+n*evaluation_mean+max(0,n-3)*max(0,n-2)/2*growth
        cases.append(dict(scenario=level, combined_increment_per_round_after_3=growth,
            approximate_rounds_under_200k_with_40k_admission_headroom=max(n for n in range(1,41) if cost(n)+40000<=200000),
            horizons=[dict(answers=n, main_evaluation=round(cost(n)), coach_planning_reserve=coach,
                abnormal_planning_reserve=retry, whole_batch=round(cost(n)+coach+retry))
                for n,coach,retry in ((15,coach15,retry15),(20,coach20,retry20))]))
    return dict(cases=cases, assumptions='Growth, Coach and abnormal reserves are planning sensitivity parameters, not measured LIVE usage. Session cap covers Main/Evaluation, Coach is separate. 40k admission headroom is approximate, actual reservation is request-specific.')


async def compare(meter, checkpoint):
    frozen = checkpoint['snapshot']['context_json']
    rows = []
    for record in meter['http']:
        body = json.loads(record['request_messages'][1]['content'])
        original = body['input']
        full = deepcopy(original)
        full['snapshot']['adaptive_blueprint'] = deepcopy(frozen['adaptive_blueprint'])
        full['snapshot']['adaptive_policy'] = {**deepcopy(frozen['adaptive_policy']),
            'context_projection': VERSION, 'prompts': freeze_prompts(context_projection=True)}
        # Original wire DTO already removed Main choices; reconstruct only the
        # server validation catalog from frozen targets + the actual offered pairs.
        if record['route'] == 'interview_main':
            targets = {t['knowledge_id']: t for t in frozen['adaptive_blueprint']['targets']}
            groups = {}
            for c in original['adaptive']['candidates']:
                groups[c['target_knowledge_id']] = dict(target=targets[c['target_knowledge_id']], strategies=c['strategies'])
            full['adaptive']['choices'] = list(groups.values())
        projected = model_payload(full, record['route'])
        prompt = full['snapshot']['adaptive_policy']['prompts'][record['route']]
        schema_type = AdaptiveSelection if record['route'] == 'interview_main' else AdaptiveEvaluation
        # Same saved LIVE business output, fixed at the external model seam.
        replay = ReplayModelProvider([record['business_output']])
        parsed = (await generate_structured(ModelRouter({record['route']: replay}), record['route'],
            system=prompt['system'], payload=projected, response_model=schema_type)).parsed
        if record['route'] == 'interview_main':
            original_pairs = [(c['candidate_id'], c['target_knowledge_id'], c['source']['ref'], c['strategies'])
                for c in original['adaptive']['candidates']]
            new_pairs = [(c['candidate_id'], c['target_knowledge_id'], c['source']['ref'], c['strategies'])
                for c in projected['adaptive']['candidates']]
            assert new_pairs == original_pairs
            assert projected['adaptive']['allowed_refs'] == original['adaptive']['allowed_refs']
            assert projected['snapshot']['memory_guided_plan'] == original['snapshot']['memory_guided_plan']
            assert projected['history'] == original['history']
            validate_decision(resolve_selection(parsed, full), full)
        else:
            assert projected['rubric'] == original['rubric'] and projected['answer'] == original['answer']
            kid = original['adaptive']['active_knowledge_id']
            assert projected['adaptive']['knowledge_state']['items'][kid]['open_gaps'] == original['adaptive']['knowledge_state']['items'].get(kid, {}).get('open_gaps', [])
            assert not {'candidates', 'choices', 'topic_coverage'} & projected['adaptive'].keys()
            assert not {'memory_guided_plan', 'approved_memory_context', 'recent_performance_observations'} & projected['snapshot'].keys()
            resolve_observations(parsed, full)
        before = wire(record['request_messages'][0]['content'], original, body['schema'])
        after = wire(prompt['system'], projected, body['schema'])
        bm, am = measures(before), measures(after)
        estimates = [record['input_tokens'] * a / b for a, b in zip(am, bm)]
        parts = {}
        for label, system, payload in [('before', record['request_messages'][0]['content'], original), ('after', prompt['system'], projected)]:
            fixed, dynamic = fixed_dynamic(system, payload, body['schema'])
            parts[label] = dict(fixed_utf8=measures(fixed)[0], fixed_lexical=measures(fixed)[1],
                dynamic_utf8=measures(dynamic)[0], dynamic_lexical=measures(dynamic)[1])
        rows.append(dict(http=record['number'], route=record['route'], actual_input_before=record['input_tokens'],
            actual_output_before=record['output_tokens'], before_chars=record['input_chars'],
            after_chars=len(prompt['system'])+len(dumps(dict(input=projected, schema=body['schema']))),
            projected_input_low=round(min(estimates)*.8), projected_input_neutral=round(sum(estimates)/2),
            projected_input_high=round(max(estimates)*1.2), methods=dict(utf8_ratio=am[0]/bm[0], lexical_ratio=am[1]/bm[1]),
            blocks=parts, fixed_replay_business_validation='PASSED'))
    return dict(method='Observed usage calibrated by UTF-8 and lexical ratios; low/high apply additional +/-20% sensitivity, not confidence bounds.',
        actual_input_before=sum(r['actual_input_before'] for r in rows), actual_output_before=sum(r['actual_output_before'] for r in rows),
        estimates={key:sum(r['projected_input_'+key] for r in rows) for key in ('low','neutral','high')}, requests=rows,
        budget_forecast=forecast(rows),
        caveat='Replay proves input/validation compatibility, not unchanged LIVE model quality. No paid calls.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--meter', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = API.parents[1]
    if args.output.exists() or not args.output.resolve().is_relative_to((root/'.runtime/acceptance').resolve()):
        raise SystemExit('Fresh ignored analysis artifact required')
    result = asyncio.run(compare(json.loads(args.meter.read_text(encoding='utf-8')), json.loads(args.checkpoint.read_text(encoding='utf-8'))))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
