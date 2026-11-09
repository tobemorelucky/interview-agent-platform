"""Versioned model DTOs. Never used to resolve provenance or commit state."""
from copy import deepcopy
import json

from interview_api.core.exceptions import AppError

VERSION = 'ROLE_CONTEXT_V1'
SCORING_VERSION = 'FOLLOWUP_SCORING_V1'


def enabled(payload):
    return payload.get('snapshot', {}).get('adaptive_policy', {}).get('context_projection') == VERSION


def pick(value, keys):
    return {k: deepcopy(value[k]) for k in keys if k in value}


def model_payload(payload, route):
    """Consume a role-sanitized payload; old sessions retain the exact old DTO."""
    result = deepcopy(payload)
    if not payload.get('adaptive'):
        return result
    snapshot = result['snapshot']
    policy = snapshot['adaptive_policy']
    snapshot['adaptive_policy'] = dict(version=policy['version'],
        prompt_fingerprints={k: v['fingerprint'] for k, v in policy['prompts'].items()})
    snapshot.pop('adaptive_blueprint', None)
    if policy.get('decision_contract') == 'TARGET_SOURCE_PAIR_V1' and route == 'interview_main':
        result['adaptive'].pop('choices', None)
    contract = policy.get('context_projection')
    if contract is None:
        return result
    if contract != VERSION:
        raise AppError('CONTEXT_PROJECTION_UNSUPPORTED', '本场上下文投影版本不可用，未自动升级', 409)
    scoring = policy.get('evaluation_context')
    if scoring not in (None, SCORING_VERSION):
        raise AppError('SCORING_CONTEXT_UNSUPPORTED', '本场评分语境版本不可用，未自动升级', 409)
    expected_eval = '2.3.2' if scoring else '2.3.1'
    if policy['prompts']['interview_main']['version'] != '2.3.1' or policy['prompts']['answer_evaluation']['version'] != expected_eval:
        raise AppError('CONTEXT_PROJECTION_PROMPT_MISMATCH', '投影与冻结 Prompt 契约不一致，未猜测兼容', 409)
    adaptive = result['adaptive']
    blueprint = payload['snapshot']['adaptive_blueprint']
    if route == 'answer_evaluation':
        active_id = adaptive['active_knowledge_id']
        target = next(t for t in blueprint['targets'] if t['knowledge_id'] == active_id)
        # All source scopes for THIS goal remain available. They are context,
        # not extra rubric requirements and not candidate-confirmed skill claims.
        refs = {s['ref'] for s in target['sources']}
        snapshot = pick(snapshot, ('company_name', 'job_title', 'interviewer_evaluation'))
        snapshot.update(source_context(payload['snapshot'], refs))
        snapshot['source_semantics'] = 'CONTEXT_ONLY_SCORE_CURRENT_QUESTION_AND_RUBRIC'
        gaps = adaptive['knowledge_state']['items'].get(active_id, {}).get('open_gaps', [])
        result = pick(result, ('current_question', 'answer', 'rubric', 'desired_difficulty'))
        result.update(snapshot=snapshot, adaptive=dict(active_knowledge_id=active_id,
            target=pick(target, ('knowledge_id', 'topic', 'label', 'purpose', 'scope')),
            knowledge_state=dict(items={active_id: dict(open_gaps=gaps)})))
        if scoring:
            sc = payload.get('scoring_context')
            if not sc or sc.get('version') != scoring or sc.get('assistance_status') != 'UNKNOWN' or sc.get('open_gaps') != gaps:
                raise AppError('SCORING_CONTEXT_REQUIRED', '本题评分语境缺失或不一致，未猜测补齐', 409)
            result['scoring_context'] = deepcopy(sc)
    elif route == 'interview_main':
        result.pop('scoring_context', None)  # Evaluation-only, even if a caller reuses its packet.
        result.pop('preferred_source', None)
        result.pop('requested_action', None)
        targets, sources = {}, {}
        by_id = {t['knowledge_id']: t for t in blueprint['targets']}
        candidates = []
        for candidate in adaptive['candidates']:
            kid, ref = candidate['target_knowledge_id'], candidate['source']['ref']
            targets[kid] = pick(by_id[kid], ('knowledge_id', 'topic', 'label', 'purpose', 'scope', 'priority'))
            sources[ref] = {**pick(candidate['source'], ('ref', 'source_type', 'source_id', 'kind')),
                'semantics': candidate['source_semantics']}
            candidates.append(pick(candidate, ('candidate_id', 'target_knowledge_id', 'strategies')) |
                dict(source=dict(ref=ref)))
        adaptive.update(candidates=candidates, targets=targets, sources=sources)
        state = adaptive['knowledge_state']
        adaptive['knowledge_state'] = dict(version=state['version'],
            items={kid: pick(item, ('state', 'open_gaps', 'resolved_gap_ids', 'question_id', 'answer_id', 'evaluation_id'))
                for kid, item in state['items'].items()},
            observations=[pick(o, ('knowledge_id', 'state', 'answer_quotes', 'missing_mechanisms', 'resolved_gap_ids',
                'question_id', 'answer_id', 'evaluation_id')) for o in state['observations']])
        snapshot = pick(snapshot, ('company_name', 'job_title', 'candidate_facts', 'candidate_evidence',
            'requirements', 'public_questions', 'approved_memory_context', 'memory_guided_plan', 'interviewer_config', 'training'))
        if 'training' in snapshot:
            # before/revisions/request keys belong to feedback and idempotency,
            # not to the interviewer. The frozen MemoryGuidedPlan remains whole.
            snapshot['training'] = pick(snapshot['training'], ('version', 'mode', 'selected_knowledge_id'))
        config = snapshot.get('interviewer_config')
        if config:
            snapshot['interviewer_config'] = pick(config, ('skill', 'round', 'style', 'company_background'))
            snapshot['interviewer_config']['skill'].pop('evaluation_anchors', None)
            snapshot['interviewer_config']['skill'].pop('content_fingerprint', None)
        for name in ('requirements', 'public_questions'):
            snapshot[name] = [{k: v for k, v in row.items() if k != 'fingerprint'} for row in snapshot.get(name, [])]
        result['snapshot'] = snapshot
    # Stable wire order as well as canonical hashing; never sort semantic lists.
    return json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))


def source_context(snapshot, refs):
    result = {}
    for name, prefix in (('requirements', 'requirement'), ('public_questions', 'public_question'), ('candidate_facts', 'fact')):
        result[name] = [deepcopy(row) for row in snapshot[name] if f"{prefix}:{row['id']}" in refs]
    keys = {key for fact in result['candidate_facts'] for key in fact['evidence_ids']}
    result['candidate_evidence'] = [deepcopy(row) for row in snapshot['candidate_evidence'] if row['key'] in keys]
    return result
