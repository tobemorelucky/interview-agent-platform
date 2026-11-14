"""Round 23 / L3: the recoverable purge Celery task.

Only the purge_request id crosses the broker. `acks_late=True` means a worker
crash redelivers this task; run_purge resumes from its recorded checkpoints.
"""
import logging
import time

from celery.utils.log import get_task_logger
from interview_worker import _paths  # noqa: F401
from interview_worker._asyncio import run_async
from interview_worker.celery_app import app

logger = get_task_logger(__name__)


@app.task(name="purge_personal_data", bind=True)
def purge_personal_data(self, purge_request_id: int):
    from interview_api.core.config import settings
    from interview_api.core.db_targets import classify, TEST, MIGRATION, E2E, UI
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.purge import service
    from interview_api.modules.purge.cleaner import run_purge
    from interview_api.modules.purge.models import PurgeRequest
    from sqlalchemy import select
    from sqlalchemy.engine import make_url

    async def main():
        async with async_session_factory() as db:
            row = await db.get(PurgeRequest, purge_request_id)
            if row is None:
                logger.warning("purge request %s missing", purge_request_id)
                return {"status": "MISSING"}
            # Defence in depth: even a forged/redelivered task must pass the
            # same three gates as the API before touching any data.
            allowed, note = service.can_execute(
                row.user_id, make_url(settings.database_url).database or "")
            if not allowed:
                logger.warning("purge request %s gated off: %s", purge_request_id, note)
                return {"status": "GATED", "reason": note}
            return await run_purge(purge_request_id, db)

    t0 = time.monotonic()
    logger.info("purge task received request_id=%s task_id=%s", purge_request_id, self.request.id)
    result = run_async(main())
    logger.info("purge task done request_id=%s status=%s elapsed=%.2fs",
                purge_request_id, result.get("status"), time.monotonic() - t0)
    return result
