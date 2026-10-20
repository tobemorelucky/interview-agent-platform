"""Durable per-HTTP-attempt budget and conservative cross-process Web pacing.

Reuses ToolInvocation, not the model Token ledger. One account-wide PG advisory
lock protects admission across API processes. No transaction spans network I/O.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from interview_api.core.config import settings
from .tool_models import ToolInvocation
from .tools.errors import ToolExecutionError


class WebHTTPAudit:
    def __init__(self, factory, task_id, mode='LIVE'):
        self.factory, self.task_id, self.mode = factory, task_id, mode

    async def reserve(self, kind, payload):
        now = datetime.now(timezone.utc)
        async with self.factory() as db:
            await db.execute(text('SELECT pg_advisory_xact_lock(1400140014)'))
            name = 'deepseek.web.' + kind + '.http'
            count = await db.scalar(select(func.count()).select_from(ToolInvocation).where(
                ToolInvocation.tool_name == name, ToolInvocation.provider_mode == self.mode,
                ToolInvocation.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0)))
            limit = settings.deepseek_web_daily_search_limit if kind == 'search' else settings.deepseek_web_daily_fetch_limit
            if count >= limit:
                raise ToolExecutionError('deepseek_web_budget_exhausted', '今日 Web API 请求预算已用尽（含失败和重试）')
            previous = await db.scalar(select(func.max(ToolInvocation.started_at)).where(
                ToolInvocation.tool_name.like('deepseek.web.%.http'), ToolInvocation.provider_mode == self.mode))
            slot = max(now, previous + timedelta(seconds=settings.deepseek_web_min_interval_seconds)) if previous else now
            # Do not build an unbounded queue behind the rate limiter.
            if (slot - now).total_seconds() > 5:
                raise ToolExecutionError('deepseek_web_busy', '采集请求繁忙，请稍后重试')
            row = ToolInvocation(refresh_task_id=self.task_id, tool_name=name, tool_version='1.4',
                provider_name='deepseek_web', provider_mode=self.mode, status='RESERVED', request_json=payload,
                response_json={}, timeout_seconds=settings.deepseek_web_timeout_seconds, started_at=slot)
            db.add(row)
            await db.commit()
            ticket = row.id
        try:
            await asyncio.sleep(max(0, (slot - datetime.now(timezone.utc)).total_seconds()))
        except asyncio.CancelledError:
            # Conservatively retain the attempt count even if cancellation
            # happened in the pacing wait, but never leave a pending audit.
            await asyncio.shield(self.finish(ticket, 'UNKNOWN', {'error_code': 'cancelled_before_send'}))
            raise
        return ticket

    async def finish(self, ticket, status, details):
        async with self.factory() as db:
            row = await db.get(ToolInvocation, ticket)
            row.status, row.response_json = status, details
            row.completed_at = datetime.now(timezone.utc)
            row.latency_ms = details.get('latency_ms')
            row.error_code = details.get('error_code')
            await db.commit()
