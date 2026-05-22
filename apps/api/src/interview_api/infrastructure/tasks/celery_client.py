"""Celery client for dispatching async tasks from the API without importing worker modules."""

import logging

from celery import Celery

from interview_api.core.config import settings

logger = logging.getLogger(__name__)

_app = Celery(
    "interview_api",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
)


def dispatch_process_kb_document(document_id: int) -> str:
    """Send a process_kb_document task to the Celery worker via the broker.

    Returns the Celery task_id so the caller can log or track it.
    """
    result = _app.send_task("process_kb_document", args=[document_id])
    task_id = result.id
    logger.info(
        "Dispatched process_kb_document doc_id=%s task_id=%s broker=%s",
        document_id,
        task_id,
        _mask_url(settings.celery_broker_url),
    )
    return task_id


def dispatch_process_resume(resume_id: int) -> str:
    """Send a process_resume task to the Celery worker via the broker.

    Returns the Celery task_id so the caller can log or track it.
    """
    result = _app.send_task("process_resume", args=[resume_id])
    task_id = result.id
    logger.info(
        "Dispatched process_resume resume_id=%s task_id=%s broker=%s",
        resume_id,
        task_id,
        _mask_url(settings.celery_broker_url),
    )
    return task_id


def _mask_url(url: str) -> str:
    """Mask password in a Redis URL for safe logging."""
    if "@" in url:
        return url.rsplit("@", 1)[0].rsplit(":", 1)[0] + ":***@" + url.rsplit("@", 1)[1]
    return url
