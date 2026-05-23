"""Celery task for resume processing — delegates to shared processor.

In Phase 3, resume processing runs in-process (asyncio.create_task in the API).
This Celery task exists for Phase 4+ when we may switch back to async workers
for bulk ingestion. It simply delegates to the shared processor module.
"""

import time
from celery.utils.log import get_task_logger
from interview_worker import _paths  # noqa: F401
from interview_worker._asyncio import run_async
from interview_worker.celery_app import app

logger = get_task_logger(__name__)


@app.task(name="process_resume", bind=True)
def process_resume(self, resume_id: int):
    """Process a resume via the shared processor (delegates to API module)."""
    logger.info(
        "Task received: resume_id=%s task_id=%s",
        resume_id,
        self.request.id,
    )
    t0 = time.monotonic()
    try:
        from interview_api.modules.resume.processor import process_resume_async

        run_async(process_resume_async(resume_id, task_id=self.request.id))
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.exception(
            "Task FAILED: resume_id=%s elapsed=%.2fs", resume_id, elapsed
        )
        try:
            run_async(_mark_resume_failed(resume_id, exc))
        except Exception:
            logger.exception("Failed to mark resume_id=%s as FAILED", resume_id)
        raise
    else:
        elapsed = time.monotonic() - t0
        logger.info(
            "Task finished: resume_id=%s elapsed=%.2fs", resume_id, elapsed
        )


async def _mark_resume_failed(resume_id: int, exc: Exception) -> None:
    """Best-effort status update for failures before the shared processor runs."""
    from interview_api.infrastructure.db.session import async_session_factory
    from interview_api.modules.resume.repository import ResumeRepository

    error_text = f"Worker task failed before resume processing completed: {exc}"
    async with async_session_factory() as db:
        repo = ResumeRepository(db)
        await repo.mark_processing_finished(
            resume_id,
            "FAILED",
            error_message=error_text,
        )
        await repo.update_processing_stage(resume_id, "FAILED", error_text[:500])
        await db.commit()
