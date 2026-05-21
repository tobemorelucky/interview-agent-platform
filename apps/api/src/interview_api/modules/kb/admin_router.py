import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import get_current_user, require_admin
from interview_api.core.config import settings
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.infrastructure.milvus.provider import MilvusVectorStoreProvider
from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
from interview_api.modules.kb.admin_schemas import (
    KbDocumentDetailResponse,
    KbDocumentListResponse,
    KbDocumentResponse,
)
from interview_api.modules.kb.admin_service import KbAdminService
from interview_api.modules.kb.ingestion_service import KbIngestionService
from interview_api.modules.kb.repository import (
    KbChunkRepository,
    KbDocumentRepository,
)
from interview_api.modules.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/kb", tags=["admin-kb"])


def _build_admin_service(db: AsyncSession) -> KbAdminService:
    storage = MinioObjectStorageProvider()
    vector_store = MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim)
    return KbAdminService(db, storage, vector_store)


@router.post("/documents/upload", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    storage = MinioObjectStorageProvider()
    service = KbIngestionService(db, storage)
    file_bytes = await file.read()
    filename = file.filename or "untitled"

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    source_type = "markdown" if ext in ("md", "markdown") else "txt"

    doc = await service.upload_and_create_document(
        filename=filename,
        file_bytes=file_bytes,
        source_type=source_type,
        uploaded_by=current_user.id,
    )

    # Commit so the document row is visible to the worker before we dispatch
    await db.commit()

    # Dispatch async processing to Celery (via broker, not direct import)
    repo = KbDocumentRepository(db)
    try:
        from interview_api.infrastructure.tasks.celery_client import (
            dispatch_process_kb_document,
        )

        task_id = dispatch_process_kb_document(doc.id)
        logger.info(
            "Upload doc_id=%s filename=%s -> Celery task_id=%s",
            doc.id,
            filename,
            task_id,
        )
    except Exception as e:
        logger.warning("Failed to dispatch Celery task for doc %s: %s", doc.id, e)
        await repo.update_status(
            doc.id, "UPLOADED",
            error_message=f"Celery dispatch failed: {e}",
        )

    return success(data=KbDocumentResponse.model_validate(doc).model_dump())


@router.get("/documents")
async def list_documents(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    repo = KbDocumentRepository(db)
    items = await repo.list_all(offset=offset, limit=limit)
    total = await repo.count_all()
    return success(
        data=KbDocumentListResponse(
            items=[KbDocumentResponse.model_validate(d).model_dump() for d in items],
            total=total,
        ).model_dump()
    )


@router.get("/documents/{document_id}")
async def get_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    repo = KbDocumentRepository(db)
    doc = await repo.get_by_id(document_id)
    if doc is None:
        from interview_api.core.exceptions import NotFoundError
        raise NotFoundError(message="Document not found")

    chunk_repo = KbChunkRepository(db)
    chunks = await chunk_repo.get_by_document_id(document_id)

    from interview_api.modules.kb.admin_schemas import KbChunkResponse
    detail = KbDocumentDetailResponse.model_validate(doc)
    detail.chunks = [KbChunkResponse.model_validate(c).model_dump() for c in chunks]
    return success(data=detail.model_dump())


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    service = _build_admin_service(db)
    await service.delete_document(document_id)
    await db.commit()
    return success(message="Document deleted")


@router.post("/documents/{document_id}/reindex")
async def reindex_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    service = _build_admin_service(db)
    doc_state = await service.reindex_document(document_id)
    await db.commit()

    # Dispatch Celery task AFTER commit so the worker can see the reset document
    try:
        from interview_api.infrastructure.tasks.celery_client import (
            dispatch_process_kb_document,
        )
        task_id = dispatch_process_kb_document(document_id)
        logger.info(
            "Reindex doc_id=%s -> Celery task_id=%s", document_id, task_id
        )
    except Exception:
        logger.exception(
            "Failed to dispatch Celery task for reindex of doc %s", document_id
        )
        kb_repo = KbDocumentRepository(db)
        await kb_repo.update_status(
            document_id,
            "FAILED",
            error_message="Celery dispatch failed during reindex",
        )
        await db.commit()

    return success(data=doc_state, message="Reindex dispatched")
