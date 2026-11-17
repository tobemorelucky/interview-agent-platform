"""Versioned model DTOs. Never used to resolve provenance or commit state."""
from copy import deepcopy
import json

from interview_api.core.exceptions import AppError

VERSION = 'ROLE_CONTEXT_V1'
SCORING_VERSION = 'FOLLOWUP_SCORING_V1'
BOUNDARY_CONTRACT = 'FIVE_DIMENSION_BOUNDARIES_V1'

# Deterministic contract mismatches: the same request can never succeed, and a
# retry must not spend another model call. Streaming clients must not offer a
# "retry" affordance for these codes.
TERMINAL_CODES = frozenset({
    'CONTEXT_ACTIVE_TARGET_REQUIRED',
    'CONTEXT_TARGET_NOT_FOUND',
    'CONTEXT_TARGET_SOURCES_MISSING',
    'CONTEXT_CANDIDATE_TARGET_NOT_FOUND',
    # Attribution failures are configuration, not transient provider trouble:
    # retrying the same request cannot fix them and must not spend another call.
    'MODEL_SCOPE_REQUIRED',
    'MODEL_LEDGER_TARGET_MISMATCH',
})


def enabled(payload):
    return payload.get('snapshot', {}).get('adaptive_policy', {}).get('context_projection') == VERSION


def expected_evaluation_prompt(policy, *, gap_scope: bool, scoring: bool) -> str:
    """Which Evaluation prompt this frozen policy must be read with.

    A session either froze the five-dimension boundary contract (and therefore the
    exact 2.3.4 prompt) or it did not (2.3.3 and older). Anything else is a mixed
    contract and is refused rather than silently upgraded: re-reading an old answer
    under a new rubric would change what its stored scores mean.
    """
    boundary = (policy or {}).get('evaluation_boundary_contract')
    if boundary not in (None, BOUNDARY_CONTRACT):
        raise AppError('EVALUATION_BOUNDARY_UNSUPPORTED', '本场评分边界契约不可用，未自动升级', 409)
    if gap_scope:
        return '2.3.4' if boundary else '2.3.3'
    return '2.3.2' if scoring else '2.3.1'


def frozen_target(blueprint, knowledge_id):
    """Resolve exactly one frozen target by id, or fail with a business error.

    A missing target means the persisted question and the session's frozen
    blueprint disagree. Substituting *another* target would score the answer
    against a different rubric while still looking like a legitimate record, so
    this always fails instead of guessing. ``next(...)`` used to raise
    ``StopIteration``, which PEP 479 turns into ``RuntimeError`` at the coroutine
    boundary: an unclassified, "retryable" failure with no diagnostic.
    """
    targets = (blueprint or {}).get('targets') or []
    if knowledge_id is None:
        raise AppError('CONTEXT_ACTIVE_TARGET_REQUIRED',
            '评分请求缺少当前考察目标，未猜测补齐', 409)
    for target in targets:
        if target.get('knowledge_id') == knowledge_id:
            if 'sources' not in target:
                raise AppError('CONTEXT_TARGET_SOURCES_MISSING',
                    f'考察目标 {knowledge_id!r} 缺少来源集合，未猜测补齐', 409)
            return target
    raise AppError('CONTEXT_TARGET_NOT_FOUND',
        f'本场冻结目标集合中不存在考察目标 {knowledge_id!r}（共 {len(targets)} 个），未替换为其他目标', 409)


def frozen_by_id(blueprint):
    """Index the frozen targets by id; an empty blueprint stays empty (no invention)."""
    return {target['knowledge_id']: target for target in (blueprint or {}).get('targets') or []
            if 'knowledge_id' in target}


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
    if contract not in (None, VERSION):
        raise AppError('CONTEXT_PROJECTION_UNSUPPORTED', '本场上下文投影版本不可用，未自动升级', 409)
    from interview_api.modules.interview.adaptive import GAP_SCOPE_CONTRACT, PAIR_CONTRACT
    gap_scope = policy.get('gap_scope_contract')
    if gap_scope is not None and (gap_scope != GAP_SCOPE_CONTRACT or contract != VERSION or
            policy.get('evaluation_context') != SCORING_VERSION or policy.get('decision_contract') != PAIR_CONTRACT):
        raise AppError('GAP_SCOPE_UNSUPPORTED', '本场缺口作用域版本不可用', 409)
    harness = policy.get('main_harness_version')
    if harness is not None:
        from interview_api.modules.interview.main_harness import VERSION as HARNESS_VERSION
        from interview_api.modules.interview.adaptive_prompts import MAIN_HARNESS, MAIN_HARNESS_CONTEXT_HINTS
        if harness != HARNESS_VERSION or gap_scope != GAP_SCOPE_CONTRACT:
            raise AppError('MAIN_HARNESS_UNSUPPORTED', '本场 Main 工具契约不可用', 409)
    if contract is None:
        return result
    scoring = policy.get('evaluation_context')
    if scoring not in (None, SCORING_VERSION):
        raise AppError('SCORING_CONTEXT_UNSUPPORTED', '本场评分语境版本不可用，未自动升级', 409)
    main_version = policy['prompts']['interview_main']['version']
    # 2.5.0 keeps the 2.3.3 decision/project shape and adds the conversation
    # contract, so both remain valid for the same projection. Anything else is a
    # mixed pair and is refused rather than guessed.
    expected_main = (MAIN_HARNESS.version, MAIN_HARNESS_CONTEXT_HINTS.version) if harness else (
        # 2.6.0 (new sessions), 2.5.0 and 2.3.3 (frozen older sessions) all keep
        # the same decision/project shape; a mixed pair is refused, not guessed.
        ('2.6.0', '2.5.0', '2.3.3') if gap_scope else ('2.3.1',))
    # 2.3.4 adds the five-dimension boundary contract. It is only accepted when the
    # session froze that contract: an older session must keep reading 2.3.3 with the
    # exact prompt it was scored under, and a mixed pair is refused, not upgraded.
    expected_eval = expected_evaluation_prompt(policy, gap_scope=gap_scope, scoring=bool(scoring))
    if main_version not in expected_main or policy['prompts']['answer_evaluation']['version'] != expected_eval:
        raise AppError('CONTEXT_PROJECTION_PROMPT_MISMATCH', '投影与冻结 Prompt 契约不一致，未猜测兼容', 409)
    adaptive = result['adaptive']
    blueprint = payload['snapshot']['adaptive_blueprint']
    if route == 'answer_evaluation':
        active_id = adaptive['active_knowledge_id']
        target = frozen_target(blueprint, active_id)
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
            if gap_scope:
                result['scoring_context']['focus_gap_ids'] = deepcopy(adaptive.get('focus_gap_ids'))
    elif route == 'interview_main':
        result.pop('scoring_context', None)  # Evaluation-only, even if a caller reuses its packet.
        result.pop('preferred_source', None)
        result.pop('requested_action', None)
        targets, sources = {}, {}
        by_id = frozen_by_id(blueprint)
        candidates = []
        for candidate in adaptive['candidates']:
            kid, ref = candidate['target_knowledge_id'], candidate['source']['ref']
            frozen = by_id.get(kid)
            if frozen is None:
                raise AppError('CONTEXT_CANDIDATE_TARGET_NOT_FOUND',
                    f'候选组合引用的考察目标 {kid!r} 不在本场冻结目标集合中，未替换为其他目标', 409)
            targets[kid] = pick(frozen, ('knowledge_id', 'topic', 'label', 'purpose', 'scope', 'priority',
                'mention_terms'))
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
                'question_id', 'answer_id', 'evaluation_id', *(
                    ('question_state', 'focus_gap_ids') if gap_scope else ()))) for o in state['observations']])
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
        if harness:
            if main_version == MAIN_HARNESS_CONTEXT_HINTS.version:
                snapshot['context_availability'] = context_availability(snapshot, adaptive)
            lazy_main(result)
    # Bound the role DTO, independently of the richer internal planning packet.
    from interview_api.modules.interview.adaptive import bounded
    bounded(result)
    # Stable wire order as well as canonical hashing; never sort semantic lists.
    return json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))


def context_availability(snapshot, adaptive):
    requirements = {r['id']: r.get('importance') for r in snapshot['requirements']}
    targets = {}
    for pair in adaptive['candidates']:
        source = adaptive['sources'][pair['source']['ref']]
        if source['source_type'] == 'JOB_REQUIREMENT' and source['source_id'] in requirements:
            targets.setdefault(source['source_id'], set()).add(pair['target_knowledge_id'])
    items = [dict(requirement_id=rid, importance=requirements[rid], target_knowledge_ids=sorted(ids))
             for rid, ids in sorted(targets.items())]
    current = adaptive['knowledge_state']['items'].get(adaptive['active_knowledge_id'], {})
    gap_count = len(current.get('open_gaps', []))
    return dict(job_requirements=dict(detail='AVAILABLE_ON_DEMAND' if items else 'NO_CANDIDATE_REQUIREMENTS',
            tool='inspect_job_requirement', items=items),
        active_gaps=dict(detail='AVAILABLE_ON_DEMAND' if gap_count else 'NO_ACTIVE_GAPS',
            tool='read_active_knowledge_gaps', count=gap_count),
        skill=dict(summary='IDENTITY_ROUND_AND_TONE', detail='AVAILABLE_ON_DEMAND', tool='inspect_current_skill'))


def lazy_main(result):
    snapshot, adaptive = result['snapshot'], result['adaptive']
    snapshot['requirement_ids'] = [row['id'] for row in snapshot.pop('requirements')]
    config = snapshot['interviewer_config']
    config['skill'] = pick(config['skill'], ('id', 'name', 'version'))
    config['round'] = pick(config['round'], ('id', 'focus'))
    config['style'] = pick(config['style'], ('id', 'probe_mode', 'guidance'))
    config['company_background'] = pick(config['company_background'], ('mode',))
    plan = snapshot.get('memory_guided_plan')
    if plan:
        plan['items'] = [pick(row, ('knowledge_id', 'state', 'intent', 'priority', 'needs_recheck',
            'reason_tags', 'memory_revision')) for row in plan['items']]
    state = adaptive['knowledge_state']
    adaptive['knowledge_state'] = dict(version=state['version'], items={
        kid: dict(state=item['state'], open_gap_count=len(item.get('open_gaps', [])))
        for kid, item in state['items'].items()})
    history = result['history']
    result['history'] = [pick(row, ('question_id', 'knowledge_id', 'status')) for row in history[:-3]] + history[-3:]
    # Do not duplicate lazy gap detail through raw observations or Evaluation's
    # missing-mechanism fields. The actual answer, latest score/coverage and
    # candidate/source dictionaries remain available without a tool roundtrip.
    if result.get('evaluation'):
        result['evaluation'] = pick(result['evaluation'], ('technical_correctness',
            'coverage', 'depth', 'relevance', 'communication', 'covered_points'))


def source_context(snapshot, refs):
    result = {}
    for name, prefix in (('requirements', 'requirement'), ('public_questions', 'public_question'), ('candidate_facts', 'fact')):
        result[name] = [deepcopy(row) for row in snapshot[name] if f"{prefix}:{row['id']}" in refs]
    keys = {key for fact in result['candidate_facts'] for key in fact['evidence_ids']}
    result['candidate_evidence'] = [deepcopy(row) for row in snapshot['candidate_evidence'] if row['key'] in keys]
    return result
