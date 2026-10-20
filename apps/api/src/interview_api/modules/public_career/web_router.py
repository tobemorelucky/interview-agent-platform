"""Administrator Web collection boundary. No ordinary-user paid tools."""
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from interview_api.api.deps import require_admin
from interview_api.core.config import settings
from interview_api.core.exceptions import AppError
from interview_api.core.rate_limit import search_run_limit, fetch_run_limit, extraction_run_limit, experience_review_write_limit
from interview_api.core.response import success
from interview_api.infrastructure.db.session import async_session_factory, get_db
from interview_api.modules.audit.models import AuditLog
from .models import PublicDataRefreshTask, JobDescriptionVersion, ClaimEvidence, JobRequirement, Job, Company
from .review import PublicCareerReviewService
from .research.agent import PublicDataResearchAgent
from .tools.errors import ToolExecutionError
from .web_collection import WebCollectionService

router = APIRouter(prefix='/api/v1/admin/public-career', tags=['admin-public-career'], dependencies=[Depends(require_admin)])


def get_web_service():
    return WebCollectionService(async_session_factory)


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company: str = Field('', max_length=40)
    job_title: str = Field('', max_length=40)
    keywords: str = Field('', max_length=80)
    count: int = Field(5, ge=1, le=10)
    sites: list[str] = Field(default_factory=list, max_length=3)
    published_after: str | None = None
    published_before: str | None = None
    need_content: bool = True
    request_key: UUID


class SelectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_url: str = Field(max_length=2048)
    document_type: Literal['job_jd', 'interview_experience']
    source_type: Literal['official', 'community']
    refresh_body: bool = False


class ResearchInput(BaseModel):
    retry_failed: bool = False


class ReviewInput(BaseModel):
    action: Literal['publish', 'reject', 'request_correction']
    reason: str = Field(min_length=1, max_length=1000)
    matching_constraints: dict | None = None


async def tool_error(awaitable):
    try:
        return await awaitable
    except ToolExecutionError as exc:
        status = 503 if exc.code == 'deepseek_web_key_missing' else 429 if exc.code in {
            'deepseek_web_budget_exhausted', 'deepseek_web_busy', 'deepseek_web_rate_limited'} else 422 if exc.code.startswith('invalid_') else 502
        raise AppError(exc.code, exc.message, status) from exc
    except ValueError as exc:
        raise AppError('WEB_INPUT_INVALID', '采集输入或任务状态无效', 422) from exc


@router.get('/status')
async def status():
    return success(data=dict(provider='deepseek_web', credential_configured=bool(settings.deepseek_web_api_key),
        daily_search_limit=settings.deepseek_web_daily_search_limit, daily_fetch_limit=settings.deepseek_web_daily_fetch_limit,
        model_route_unchanged=True, no_live_search_claim_without_success=True))


@router.post('/search', dependencies=[Depends(search_run_limit)])
async def search(body: SearchInput, service=Depends(get_web_service)):
    return success(data=await tool_error(service.search(body.model_dump(mode='json'))))


@router.get('/tasks')
async def tasks(db=Depends(get_db)):
    rows = (await db.scalars(select(PublicDataRefreshTask).where(PublicDataRefreshTask.idempotency_key.like('web-%'))
        .order_by(PublicDataRefreshTask.id.desc()).limit(50))).all()
    return success(data=[dict(id=t.id, status=t.status, target_type=t.target_type, error=t.error_message) for t in rows])


@router.get('/tasks/{task_id}')
async def detail(task_id: int, service=Depends(get_web_service)):
    return success(data=await service.detail(task_id))


@router.post('/tasks/{task_id}/select', dependencies=[Depends(fetch_run_limit)])
async def select_source(task_id: int, body: SelectionInput, service=Depends(get_web_service)):
    return success(data=await tool_error(service.select(task_id, body.source_url, body.document_type,
        body.source_type, refresh_body=body.refresh_body)))


@router.post('/tasks/{task_id}/extract', dependencies=[Depends(extraction_run_limit)])
async def extract(task_id: int, body: ResearchInput, service=Depends(get_web_service)):
    current = await service.detail(task_id)
    if not current['manifest']:
        raise AppError('WEB_BODY_REQUIRED', '先选择并取得正文，再运行结构化抽取', 409)
    if current['status'] == 'COMPLETED':
        return success(data=current)
    if current['status'] != 'READY_FOR_EXTRACTION' and not (current['status'] == 'FAILED' and body.retry_failed):
        raise AppError('WEB_RESEARCH_STATE', '抽取正在执行或失败重试尚未确认，请先刷新任务状态', 409)
    result = await tool_error(PublicDataResearchAgent(service.factory).run(task_id, force=body.retry_failed))
    return success(data=await service.detail(task_id))


@router.get('/jd/{jd_id}')
async def jd_detail(jd_id: int, db=Depends(get_db)):
    jd = await db.get(JobDescriptionVersion, jd_id)
    if not jd:
        raise AppError('JD_NOT_FOUND', 'JD不存在', 404)
    reqs = list((await db.scalars(select(JobRequirement).where(JobRequirement.job_description_version_id == jd_id))).all())
    claim_ids = [r.claim_id for r in reqs] + [(jd.metadata_json or {}).get('identity_claim_id')]
    claim_ids += [item['claim_id'] for item in (jd.metadata_json or {}).get('attribute_claims', [])]
    evidence = list((await db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id.in_([i for i in claim_ids if i])))).all())
    job = await db.get(Job, jd.job_id)
    company = await db.get(Company, job.company_id)
    return success(data=dict(id=jd.id, company=company.canonical_name, title=job.canonical_title,
        attributes=(jd.structured_data_json or {}).get('attributes', []),
        publication_status=jd.publication_status, metadata=jd.metadata_json,
        requirements=[dict(id=r.id, text=r.statement, claim_id=r.claim_id) for r in reqs],
        evidence=[dict(claim_id=e.claim_id, snapshot_id=e.source_snapshot_id, excerpt=e.excerpt,
            location=e.source_location_json) for e in evidence]))


@router.post('/jd/{jd_id}/review', dependencies=[Depends(experience_review_write_limit)])
async def review(jd_id: int, body: ReviewInput, user=Depends(require_admin), db=Depends(get_db)):
    if body.action == 'publish':
        result = await PublicCareerReviewService(db).publish_jd(jd_id, reviewer_id=user.id,
            reason=body.reason, matching_constraints=body.matching_constraints)
    else:
        jd = await db.scalar(select(JobDescriptionVersion).where(JobDescriptionVersion.id == jd_id).with_for_update())
        if not jd:
            raise AppError('JD_NOT_FOUND', 'JD不存在', 404)
        if jd.publication_status == 'PUBLISHED':
            raise AppError('PUBLISHED_JD_IMMUTABLE', '不能用候选审核覆盖已发布版本', 409)
        previous = jd.publication_status
        jd.publication_status = 'REJECTED' if body.action == 'reject' else 'WAITING_REVIEW'
        db.add(AuditLog(actor_user_id=user.id, actor_role='ADMIN', action='public_career.jd.' + body.action,
            resource_type='JobDescriptionVersion', resource_id=str(jd.id),
            metadata_json=dict(reason=body.reason, previous=previous)))
        result = dict(jd_version_id=jd.id, publication_status=jd.publication_status)
    await db.commit()
    return success(data=result)
