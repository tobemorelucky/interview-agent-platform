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


def dispatch_process_kb_document(document_id: int) -> None:
    """Send a process_kb_document task to the Celery worker via the broker.

    The worker must register a task named ``process_kb_document`` that accepts
    a single ``document_id`` argument.  This function does NOT import any
    interview_worker module.
    """
    _app.send_task("process_kb_document", args=[document_id])
    logger.info("Dispatched process_kb_document task for doc_id=%s", document_id)
