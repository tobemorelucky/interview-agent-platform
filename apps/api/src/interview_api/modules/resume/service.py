"""Resume service: upload, parsing orchestration, and report retrieval."""

import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.core.config import settings
from interview_api.modules.resume.repository import ResumeRepository, ResumeReportRepository
from interview_api.modules.resume.models import ResumeReport

logger = logging.getLogger(__name__)

PROMPT_DIR = (
    Path(__file__).parent.parent.parent.parent.parent
    / "prompt_templates"
)


class ResumeService:
    """Orchestrates resume upload and report retrieval.

    The heavy async processing (parse → extract → KB retrieval → question generation)
    runs in the Celery worker. This service only handles the API-layer operations:
    upload, list, get, delete, and report retrieval.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.resume_repo = ResumeRepository(db)
        self.report_repo = ResumeReportRepository(db)

    # ── Upload ──

    async def upload(
        self,
        user_id: int,
        filename: str,
        file_bytes: bytes,
    ) -> dict:
        """Validate, store to MinIO, create DB record. Returns resume dict."""
        from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider

        file_type = self._get_file_type(filename)
        self._validate_file(file_bytes, file_type)

        content_hash = hashlib.sha256(file_bytes).hexdigest()
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else file_type
        storage_key = f"resumes/{user_id}/{uuid4().hex}.{ext}"

        storage = MinioObjectStorageProvider()
        await storage.upload(
            bucket_name=settings.minio_bucket,
            object_key=storage_key,
            data=file_bytes,
            content_type=self._content_type(file_type),
        )

        resume = await self.resume_repo.create(
            user_id=user_id,
            filename=filename,
            storage_key=storage_key,
            file_type=file_type,
            file_size=len(file_bytes),
            content_hash=content_hash,
        )

        return {
            "id": resume.id,
            "user_id": resume.user_id,
            "filename": resume.filename,
            "file_type": resume.file_type,
            "file_size": resume.file_size,
            "status": resume.status,
            "task_id": resume.task_id,
            "created_at": resume.created_at.isoformat() if resume.created_at else None,
        }

    # ── List / Get ──

    async def list_resumes(self, user_id: int, page: int = 1, page_size: int = 20) -> dict:
        offset = (page - 1) * page_size
        items, total = await self.resume_repo.list_by_user(user_id, offset=offset, limit=page_size)
        return {
            "items": [
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "filename": r.filename,
                    "file_type": r.file_type,
                    "file_size": r.file_size,
                    "status": r.status,
                    "error_message": r.error_message,
                    "task_id": r.task_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in items
            ],
            "total": total,
        }

    async def get_resume(self, resume_id: int, user_id: int) -> dict:
        resume = await self.resume_repo.get_by_id_and_user(resume_id, user_id)
        if resume is None:
            from interview_api.core.exceptions import ResumeNotFoundError
            raise ResumeNotFoundError()

        raw_text_preview = None
        if resume.raw_text:
            raw_text_preview = resume.raw_text[:500]

        return {
            "id": resume.id,
            "user_id": resume.user_id,
            "filename": resume.filename,
            "file_type": resume.file_type,
            "file_size": resume.file_size,
            "status": resume.status,
            "error_message": resume.error_message,
            "task_id": resume.task_id,
            "raw_text_preview": raw_text_preview,
            "created_at": resume.created_at.isoformat() if resume.created_at else None,
            "updated_at": resume.updated_at.isoformat() if resume.updated_at else None,
        }

    # ── Report ──

    async def get_report(self, resume_id: int, user_id: int) -> dict:
        resume = await self.resume_repo.get_by_id_and_user(resume_id, user_id)
        if resume is None:
            from interview_api.core.exceptions import ResumeNotFoundError
            raise ResumeNotFoundError()

        if resume.status != "COMPLETED":
            from interview_api.core.exceptions import ResumeReportNotReadyError
            raise ResumeReportNotReadyError(
                message=f"简历分析尚未完成，当前状态：{resume.status}"
            )

        report = await self.report_repo.get_by_resume_id(resume_id)
        if report is None:
            from interview_api.core.exceptions import ResumeNotFoundError
            raise ResumeNotFoundError(message="报告未生成")

        return {
            "id": report.id,
            "resume_id": report.resume_id,
            "summary_json": report.summary_json,
            "retrieval_queries_json": report.retrieval_queries_json,
            "retrieved_context_json": report.retrieved_context_json,
            "questions_json": report.questions_json,
            "suggestions_json": report.suggestions_json,
            "created_at": report.created_at.isoformat() if report.created_at else None,
        }

    # ── Delete ──

    async def delete_resume(self, resume_id: int, user_id: int) -> None:
        resume = await self.resume_repo.get_by_id_and_user(resume_id, user_id)
        if resume is None:
            from interview_api.core.exceptions import ResumeNotFoundError
            raise ResumeNotFoundError()

        from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider

        storage = MinioObjectStorageProvider()
        try:
            await storage.delete(bucket_name=settings.minio_bucket, object_key=resume.storage_key)
        except Exception:
            logger.warning("Failed to delete MinIO object: %s", resume.storage_key)

        await self.report_repo.delete_by_resume_id(resume_id)
        await self.resume_repo.delete(resume_id)

    # ── Helpers ──

    @staticmethod
    def _get_file_type(filename: str) -> str:
        if "." not in filename:
            raise ValueError("无法识别文件类型")
        ext = filename.rsplit(".", 1)[-1].lower()
        allowed = [t.strip() for t in settings.resume_allowed_types.split(",")]
        if ext not in allowed:
            from interview_api.core.exceptions import ResumeFileTypeInvalidError
            raise ResumeFileTypeInvalidError()
        return ext

    @staticmethod
    def _validate_file(data: bytes, file_type: str) -> None:
        if not data:
            from interview_api.core.exceptions import ResumeFileTooLargeError
            raise ResumeFileTooLargeError(message="文件为空")

        max_bytes = settings.resume_max_file_size_mb * 1024 * 1024
        if len(data) > max_bytes:
            from interview_api.core.exceptions import ResumeFileTooLargeError
            raise ResumeFileTooLargeError(
                message=f"文件大小超过限制（最大 {settings.resume_max_file_size_mb}MB）"
            )

    @staticmethod
    def _content_type(file_type: str) -> str:
        mapping = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "txt": "text/plain",
        }
        return mapping.get(file_type, "application/octet-stream")
