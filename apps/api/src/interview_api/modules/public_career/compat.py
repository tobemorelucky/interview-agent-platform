"""Compatibility boundary for the legacy experience collection task."""

from interview_api.modules.experience.models import ExperienceCollectionTask
from interview_api.modules.public_career.models import PublicDataRefreshTask
from interview_api.modules.public_career.repository import (
    PublicDataRefreshTaskRepository,
)


class ExperienceRefreshTaskAdapter:
    """Bind one legacy task to one public refresh execution boundary."""

    def __init__(self, repository: PublicDataRefreshTaskRepository) -> None:
        self.repository = repository

    async def get_or_create(
        self, legacy_task: ExperienceCollectionTask
    ) -> PublicDataRefreshTask:
        if legacy_task.public_refresh_task_id is not None:
            existing = await self.repository.get(legacy_task.public_refresh_task_id)
            if existing is not None:
                return existing

        idempotency_key = f"experience_collection_task:{legacy_task.id}"
        existing = await self.repository.get_by_idempotency_key(idempotency_key)
        if existing is None:
            existing = await self.repository.create(
                target_type="EXPERIENCE_COLLECTION_TASK",
                target_ref=str(legacy_task.id),
                reason="Legacy experience collection compatibility entry",
                idempotency_key=idempotency_key,
            )
        legacy_task.public_refresh_task_id = existing.id
        await self.repository.db.flush()
        return existing
