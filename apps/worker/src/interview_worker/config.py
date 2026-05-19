import os


class WorkerSettings:
    def __init__(self):
        self.celery_broker_url = os.getenv(
            "CELERY_BROKER_URL", "redis://localhost:6379/1"
        )
        self.celery_result_backend = os.getenv(
            "CELERY_RESULT_BACKEND", "redis://localhost:6379/2"
        )


settings = WorkerSettings()
