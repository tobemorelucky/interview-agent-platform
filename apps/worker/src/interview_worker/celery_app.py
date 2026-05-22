import logging

from celery import Celery
from celery.signals import worker_shutdown

from interview_api.core.config import settings
from interview_worker._asyncio import run_async, shutdown_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = Celery("interview_worker", broker=settings.celery_broker_url)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_backend=settings.celery_result_backend,
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Register task modules explicitly so @app.task decorators are processed.
import interview_worker.tasks.kb_tasks  # noqa: E402, F401


@app.task(name="ping")
def ping():
    return "pong"


@worker_shutdown.connect
def close_async_resources(**_kwargs):
    """Release async resources before the worker process exits."""
    from interview_api.infrastructure.db.engine import engine

    run_async(engine.dispose())
    shutdown_loop()
