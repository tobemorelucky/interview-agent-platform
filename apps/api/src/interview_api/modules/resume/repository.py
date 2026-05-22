from datetime import datetime, timezone

from sqlalchemy import select, update, delete, func as sql_func
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.modules.resume.models import Resume, ResumeReport


class ResumeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, resume_id: int) -> Resume | None:
        result = await self.db.execute(
            select(Resume).where(Resume.id == resume_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_and_user(self, resume_id: int, user_id: int) -> Resume | None:
        result = await self.db.execute(
            select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: int,
        filename: str,
        storage_key: str,
        file_type: str,
        file_size: int | None = None,
        content_hash: str | None = None,
    ) -> Resume:
        resume = Resume(
            user_id=user_id,
            filename=filename,
            storage_key=storage_key,
            file_type=file_type,
            file_size=file_size,
            content_hash=content_hash,
        )
        self.db.add(resume)
        await self.db.flush()
        return resume

    async def update_task_id(self, resume_id: int, task_id: str) -> None:
        await self.db.execute(
            update(Resume)
            .where(Resume.id == resume_id)
            .values(task_id=task_id)
        )
        await self.db.flush()

    async def mark_processing_started(self, resume_id: int) -> None:
        await self.db.execute(
            update(Resume)
            .where(Resume.id == resume_id)
            .values(
                status="PROCESSING",
                error_message=None,
                processing_started_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()

    async def update_raw_text(self, resume_id: int, raw_text: str) -> None:
        await self.db.execute(
            update(Resume)
            .where(Resume.id == resume_id)
            .values(raw_text=raw_text)
        )
        await self.db.flush()

    async def mark_processing_finished(
        self,
        resume_id: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        values: dict = {
            "status": status,
            "processing_finished_at": datetime.now(timezone.utc),
        }
        if status == "FAILED" and error_message is not None:
            values["error_message"] = error_message[:2000]
        elif status == "COMPLETED":
            values["error_message"] = None

        await self.db.execute(
            update(Resume).where(Resume.id == resume_id).values(**values)
        )
        await self.db.flush()

    async def list_by_user(
        self, user_id: int, offset: int = 0, limit: int = 20
    ) -> tuple[list[Resume], int]:
        result = await self.db.execute(
            select(Resume)
            .where(Resume.user_id == user_id)
            .order_by(Resume.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list(result.scalars().all())

        count_result = await self.db.execute(
            select(sql_func.count()).select_from(Resume).where(Resume.user_id == user_id)
        )
        total = count_result.scalar() or 0
        return items, total

    async def delete(self, resume_id: int) -> None:
        await self.db.execute(
            delete(Resume).where(Resume.id == resume_id)
        )
        await self.db.flush()


class ResumeReportRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_resume_id(self, resume_id: int) -> ResumeReport | None:
        result = await self.db.execute(
            select(ResumeReport).where(ResumeReport.resume_id == resume_id)
        )
        return result.scalar_one_or_none()

    async def create(self, report: ResumeReport) -> ResumeReport:
        self.db.add(report)
        await self.db.flush()
        return report

    async def delete_by_resume_id(self, resume_id: int) -> None:
        await self.db.execute(
            delete(ResumeReport).where(ResumeReport.resume_id == resume_id)
        )
        await self.db.flush()
