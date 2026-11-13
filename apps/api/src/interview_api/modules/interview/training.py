"""Personal training projections over the existing interview and capability records."""
from copy import deepcopy
from sqlalchemy import select, text
from interview_api.core.exceptions import AppError
from interview_api.modules.interview.context import build_context
from interview_api.modules.memory.capability_service import CapabilityMemoryService
from interview_api.modules.candidate_profile.evidence import digest


def freeze_training(context, request, states, full):
    if request.design_interview:
        raise AppError('TRAINING_MODE_INVALID', '训练不能与系统设计工具模式同时启用', 422)
    if not full.get('targets'):
        raise AppError('TRAINING_NO_TARGETS', '当前岗位与专业规则没有合法考察目标，请更换专业方向', 422)
    views = target_views({**context, 'adaptive_blueprint': full}, states)
    if request.training_mode == 'FULL':
        if request.training_knowledge_id:
            raise AppError('TRAINING_TARGET_INVALID', '完整面试不绑定单一知识目标', 422)
    else:
        view = next((t for t in views if t['knowledge_id'] == request.training_knowledge_id), None)
        if not view or request.training_mode not in view['available_modes']:
            raise AppError('TRAINING_TARGET_INVALID', '该知识点不属于当前岗位或当前状态不适合此类训练，请刷新推荐', 422)
        if request.training_mode == 'RECHECK' and context['interviewer_config']['style'].get('guidance'):
            raise AppError('INDEPENDENT_RECHECK_STYLE', '独立复测请使用标准或严格风格，避免开场提示影响验证', 422)
        selected = deepcopy(full)
        selected['targets'] = [t for t in full['targets'] if t['knowledge_id'] == view['knowledge_id']]
        selected['fingerprint'] = digest({k:v for k,v in selected.items() if k!='fingerprint'})
        context['adaptive_blueprint'] = selected
    ids = {t['knowledge_id'] for t in context['adaptive_blueprint']['targets']}
    context['training'] = dict(version='2.3.0', mode=request.training_mode,
        selected_knowledge_id=request.training_knowledge_id, request_key=request.training_request_key,
        request_fingerprint=digest(request.model_dump()),
        before=[v for v in views if v['knowledge_id'] in ids])
    from interview_api.modules.memory.capability_rules import memory_plan, instant
    context['memory_guided_plan'] = memory_plan(states,context['adaptive_blueprint']['targets'],
        instant(context['memory_guided_plan']['frozen_at']))


async def completion_feedback(db, session, context):
    from interview_api.modules.interview.models import InterviewSessionQuestion, InterviewEvaluation
    current = {r['topic']:r for r in await CapabilityMemoryService(db).list(session.user_id)}
    questions = {q.id:q for q in await db.scalars(select(InterviewSessionQuestion).where(InterviewSessionQuestion.session_id==session.id))}
    evaluations = {e.id:e for e in await db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id==session.id))}
    observations = (session.interview_plan_json or {}).get('adaptive_knowledge', {}).get('observations', [])
    targets=[]
    for before in context['training']['before']:
        kid=before['knowledge_id']
        rows=[o for o in observations if o['knowledge_id']==kid and o.get('evaluation_id') in evaluations
            and evaluations[o['evaluation_id']].answer_id==o['answer_id'] and o['question_id'] in questions]
        after=current.get(kid.rsplit(':',1)[-1], {})
        independent=sum(questions[o['question_id']].question_kind=='NEXT' and not
            (questions[o['question_id']].candidate_refs_json or {}).get('interviewer_guidance') for o in rows)
        unresolved=any(c['status']=='OPEN' for c in after.get('conflicts', []))
        conflict='OPEN' if unresolved else ('RESOLVED' if rows and before['state']=='CONFLICTED' and
            any(c['status']=='RESOLVED_BY_INDEPENDENT_RECHECK' for c in after.get('conflicts', [])) else 'UNKNOWN')
        targets.append(dict(knowledge_id=kid,label=before['label'],before_state=before['state'],before_revision=before['revision'],
            after_state=after.get('state','UNKNOWN'),after_revision=after.get('revision'),
            effective_evaluations=len(rows), independent_evaluations=independent, conflict_status=conflict,
            result='OBSERVED' if rows else 'NO_NEW_OBSERVATIONS',
            evaluations=[dict(evaluation_id=o['evaluation_id'],question_id=o['question_id'],state=o['state']) for o in rows],
            next_action='安排新的独立场景复核' if after.get('state')!='VERIFIED' else '切换其他岗位相关知识点'))
    decisions=[dict(question_id=q.id,question=q.question,
        strategy=(q.candidate_refs_json or {}).get('adaptive_decision',{}).get('strategy'),
        knowledge_id=(q.candidate_refs_json or {}).get('adaptive_decision',{}).get('target_knowledge_id')) for q in questions.values()]
    return dict(version='2.3.0',mode=context['training']['mode'],targets=targets,questions=decisions,
        semantics='根据已提交评价与能力规则计算，不是掌握概率或记忆因果收益证明')


async def controller_rejection(factory, run_id, code):
    from interview_api.modules.experience.models import ExperienceAgentRun
    async with factory() as db:
        run = await db.get(ExperienceAgentRun, run_id)
        if run:
            run.output_json = {**(run.output_json or {}), 'controller_result': dict(verdict='REJECTED',error=code)}
            await db.commit()


def modes(state, revoked=False):
    if revoked:
        return ['FULL']
    return ['FULL'] + (['WEAKNESS'] if state in ('GAP_SUSPECTED', 'PRACTICING') else
                       ['RECHECK'] if state in ('CONFLICTED', 'RECHECK_DUE') else [])


def target_views(context, states):
    index = {r['topic']: r for r in states}
    return [dict(knowledge_id=t['knowledge_id'], label=t['label'],
        state=index.get(t['topic'], {}).get('state', 'UNKNOWN'),
        revision=index.get(t['topic'], {}).get('revision'),
        revoked=index.get(t['topic'], {}).get('revoked', False),
        available_modes=modes(index.get(t['topic'], {}).get('state', 'UNKNOWN'), index.get(t['topic'], {}).get('revoked', False)),
        reason_tags=index.get(t['topic'], {}).get('reason_tags', []),
        independent_sessions=index.get(t['topic'], {}).get('independent_sessions', 0),
        conflicts=index.get(t['topic'], {}).get('conflicts', []),
        observations=[dict(session_id=o['session_id'], assisted=o['assisted'], state=o['state'],
            evaluation_id=o['evaluation_id'], observed_at=o['observed_at']) for o in index.get(t['topic'], {}).get('observations', [])[-8:]])
        for t in context['adaptive_blueprint']['targets']]


class TrainingService:
    def __init__(self, factory):
        self.factory = factory

    async def create(self, user_id, request):
        from interview_api.modules.interview.models import InterviewContextSnapshot
        from interview_api.modules.interview.controller import SessionController
        from interview_api.modules.interview.core_service import MockInterviewService
        # Starting a training session is a retryable one-click action, so the
        # client must carry a key it can reuse. Minting one here would *look*
        # idempotent while every retry still inserted a row, so a caller that
        # omits the key is told to send one instead of being silently upgraded.
        if not request.training_request_key:
            raise AppError('TRAINING_REQUEST_KEY_REQUIRED',
                '创建训练需要客户端提供 training_request_key；同一次操作重试请复用同一个键，'
                '用户重新开始一场训练请换一个新键', 422)
        expected = digest(request.model_dump())
        async with self.factory() as db:
            # Serialise one user's creations for the whole transaction: two
            # concurrent attempts with the same key cannot both insert. The lock
            # is transactional and database-wide, so it also holds across
            # processes, and commit/rollback releases it.
            await db.execute(text('SELECT pg_advisory_xact_lock(2300, :uid)'),dict(uid=user_id))
            old=await db.scalar(select(InterviewContextSnapshot).where(InterviewContextSnapshot.user_id==user_id,
                InterviewContextSnapshot.context_json['training']['request_key'].as_string()==request.training_request_key))
            if old:
                # The key names one operation. Reusing it for different content
                # is a client bug, not a retry, so it is refused instead of
                # silently returning the old session.
                if old.context_json['training']['request_fingerprint']!=expected:
                    raise AppError('TRAINING_REQUEST_CONFLICT','相同创建请求不能更换训练内容',409)
                sid=old.interview_session_id
            else:
                session=await SessionController(db).create(user_id,request)
                sid=session.id
            await db.commit()
        return await MockInterviewService(self.factory).detail(sid,user_id)

    async def feedback(self, user_id, session_id):
        from interview_api.modules.interview.core_service import MockInterviewService
        detail=await MockInterviewService(self.factory).detail(session_id,user_id)
        training=detail['context_snapshot'].get('training')
        if not training:
            return dict(available=False, status=detail['status'], targets=[], message='历史面试未冻结训练起始状态，不能追补成长变化')
        stored=(detail.get('completion_json') or {}).get('training_feedback')
        from interview_api.modules.interview.models import InterviewAgentCall
        from interview_api.modules.experience.models import ExperienceAgentRun, ExperienceAgentStepRun
        accepted={(q.get('evidence') or {}).get('main_agent_run_id') for q in detail['questions']}
        usage=[]
        async with self.factory() as db:
            calls=(await db.execute(select(ExperienceAgentRun,ExperienceAgentStepRun).join(InterviewAgentCall,
                InterviewAgentCall.agent_run_id==ExperienceAgentRun.id).join(ExperienceAgentStepRun,
                ExperienceAgentStepRun.agent_run_id==ExperienceAgentRun.id).where(
                InterviewAgentCall.session_id==session_id,ExperienceAgentRun.agent_role=='InterviewMainAgent',
                ExperienceAgentStepRun.prompt_name=='interview_main')
                .distinct(ExperienceAgentRun.id).order_by(ExperienceAgentRun.id,ExperienceAgentStepRun.id).limit(80))).all()
            for run,step in calls:
                audit=(step.input_json or {}).get('memory_usage', {})
                artifact=(run.output_json or {}).get('validated_artifact', {})
                rejected=(run.output_json or {}).get('controller_result')
                target=artifact.get('target_knowledge_id')
                usage.append(dict(run_id=run.id,model_input=audit.get('model_input','UNKNOWN'),
                    candidate_memory=audit.get('candidate_memory', []),plan_fingerprint=audit.get('plan_fingerprint'),
                    target_knowledge_id=target,strategy=artifact.get('strategy'),candidate_id=artifact.get('candidate_id'),
                    selection=('RELATED_TARGET_SELECTED' if target and target in {i['knowledge_id'] for i in audit.get('candidate_memory',[])} else
                        'OTHER_TARGET_SELECTED' if target else 'UNKNOWN'),
                    controller_verdict='ACCEPTED' if run.id in accepted else 'REJECTED' if rejected else
                        'NOT_REACHED' if run.status=='FAILED' else 'UNKNOWN',
                    error=run.error_message or (rejected or {}).get('error')))
        snapshot = detail['context_snapshot']
        return dict(available=True,status=detail['status'],job_id=snapshot['job_id'],
            company_id=snapshot['company_id'],
            job_target_id=(snapshot.get('job_target') or {}).get('id'),
            skill_id=snapshot['interviewer_config']['skill']['id'],
            memory_usage=usage,
            **(stored or dict(mode=training['mode'],targets=[],message='训练尚未完成，暂不生成成长结论')))

    async def workbench(self, user_id, request):
        request = request.model_copy(update=dict(adaptive_interview=True, question_count=8,
            training_mode=None, training_knowledge_id=None, training_request_key=None))
        async with self.factory() as db:
            try:
                context, _, _ = await build_context(db, user_id, request)
                states = await CapabilityMemoryService(db).list(user_id)
                # A private JD has no public company/job ids. Keep the same
                # response shape and add its own reference so the page can
                # offer training without inventing a public job.
                return dict(company_id=context['company_id'], job_id=context['job_id'],
                    job_target_id=(context.get('job_target') or {}).get('id'),
                    source_label=("用户提供（未经企业核实）" if context.get('job_target') else None),
                    company_name=context['company_name'], job_title=context['job_title'],
                    skill_id=context['interviewer_config']['skill']['id'],
                    targets=target_views(context, states),
                    recommendations=context['memory_guided_plan']['items'],
                    coverage=dict(reviewed_question_count=len(context['public_questions']),
                        fallback=context['adaptive_blueprint']['fallback']))
            finally:
                # Reuse exact creation rules without publishing read-preview projections.
                await db.rollback()

    async def candidates(self, user_id, request, *, session_id=None):
        """Round 16: a read-time, rule-only projection of executable training options.

        No report is generated or rewritten, no Coach/Harness contract changes,
        nothing is written to CapabilityMemory (the same rollback-only preview
        `workbench` uses). With `session_id`, the source and Skill come from that
        user's frozen session snapshot, never from a mutable JD.
        """
        from datetime import datetime, timezone
        from interview_api.core.exceptions import AppError
        from interview_api.modules.interview.models import InterviewContextSnapshot, InterviewSession
        from interview_api.modules.interview.training_candidates import (
            PROJECTION_VERSION, SEMANTICS, build_training_candidates)

        computed_at = datetime.now(timezone.utc).isoformat()
        target_unavailable = ("JOB_TARGET_NOT_CONFIRMED", "JOB_TARGET_NOT_FOUND",
                              "JOB_SOURCE_CONFLICT", "JOB_SOURCE_REQUIRED",
                              "PROFILE_REQUIRED", "PUBLISHED_JD_REQUIRED",
                              "REQUIREMENTS_REQUIRED", "INVALID_COMPANY_JOB",
                              "INTERVIEWER_SKILL_REQUIRED", "SESSION_NOT_FOUND")
        async with self.factory() as db:
            try:
                if session_id:
                    session = await db.scalar(select(InterviewSession).where(
                        InterviewSession.id == session_id, InterviewSession.user_id == user_id))
                    if session is None:
                        raise AppError("SESSION_NOT_FOUND", "面试会话不存在", 404)
                    snapshot = await db.get(InterviewContextSnapshot, session.context_snapshot_id)
                    frozen = (snapshot.context_json if snapshot else {}) or {}
                    target = frozen.get('job_target') or {}
                    skill = (frozen.get('interviewer_config') or {}).get('skill') or {}
                    identity = dict(job_target_id=target.get('id'), job_id=frozen.get('job_id'),
                                    company_id=frozen.get('company_id'),
                                    target_version=target.get('version'), skill_id=skill.get('id'))
                    request = request.model_copy(update=dict(
                        job_target_id=target.get('id') or None,
                        company_id=frozen.get('company_id'), job_id=frozen.get('job_id'),
                        interviewer_skill_id=skill.get('id')))
                else:
                    identity = dict(job_target_id=request.job_target_id, job_id=request.job_id,
                                    company_id=request.company_id, target_version=None,
                                    skill_id=request.interviewer_skill_id)
                request = request.model_copy(update=dict(adaptive_interview=True, question_count=8,
                    training_mode=None, training_knowledge_id=None, training_request_key=None,
                    design_interview=False, main_harness=False))
                context, _, _ = await build_context(db, user_id, request)
                states = await CapabilityMemoryService(db).list(user_id)
                views = target_views(context, states)
                blueprint = context.get('adaptive_blueprint') or {}
                priorities = {t['knowledge_id']: t.get('priority', 0) for t in blueprint.get('targets', [])}
                result = build_training_candidates(views, states,
                    skill_id=request.interviewer_skill_id, computed_at=computed_at,
                    job_target_id=identity['job_target_id'], job_id=identity['job_id'],
                    target_version=identity['target_version'], target_priorities=priorities)
                result['modes_source'] = 'interview.training.modes() (same function freeze_training uses)'
                return result
            except AppError as exc:
                if exc.code not in target_unavailable:
                    raise
                # An edited/unconfirmed/foreign target must not leave an old
                # suggestion looking like something executable right now.
                return dict(projection=PROJECTION_VERSION, source="DETERMINISTIC_RULE",
                    model_calls=0, computed_at=computed_at, status="TARGET_UNAVAILABLE",
                    empty_reason="TARGET_EXPIRED_OR_UNAVAILABLE", error_code=exc.code,
                    candidates=[], general_only=[], fallback=None, semantics=SEMANTICS,
                    message="当前目标版本已变化、未确认或不可用；旧建议不能继续当作当前可直接执行的训练。",
                    disclaimer="规则候选与模型 Coach 建议来源不同，两者不等价。")
            finally:
                await db.rollback()
