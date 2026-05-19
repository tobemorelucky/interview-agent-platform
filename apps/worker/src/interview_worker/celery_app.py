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

app.autodiscover_tasks(["interview_worker.tasks"])


@app.task(name="ping")
def ping():
    return "pong"
