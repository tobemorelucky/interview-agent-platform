from celery import Celery

from interview_worker.config import settings

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
# autodiscover_tasks may not recurse into submodules of a package.
import interview_worker.tasks.kb_tasks  # noqa: F401, E402 — registers process_kb_document


@app.task(name="ping")
def ping():
    return "pong"
