from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from interview_api.api.deps import get_current_user, require_admin
from interview_api.core.response import success
from interview_api.infrastructure.db.session import get_db
from interview_api.infrastructure.storage.provider import MinioObjectStorageProvider
from interview_api.modules.kb.admin_schemas import (
    KbDocumentDetailResponse,
    KbDocumentListResponse,
    KbDocumentResponse,
)
from interview_api.modules.kb.ingestion_service import KbIngestionService
from interview_api.modules.kb.repository import (
    KbChunkRepository,
    KbDocumentRepository,
)
from interview_api.modules.users.models import User

router = APIRouter(prefix="/api/v1/admin/kb", tags=["admin-kb"])


@router.post("/documents/upload", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    storage = MinioObjectStorageProvider()
    repo = KbDocumentRepository(db)
    file_bytes = await file.read()
    content_hash = KbIngestionService.compute_hash(file_bytes)
    filename = file.filename or "untitled"

    # Check duplicate
    existing = await repo.get_by_content_hash(content_hash)
    service = KbIngestionService(db, storage, None, None)
    if service.is_duplicate(existing):
        from interview_api.core.exceptions import AppError
        raise AppError(
            code="DUPLICATE_DOCUMENT",
            message=f"Document already exists with status '{existing.status}'",
            status_code=409,
        )

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    source_type = "markdown" if ext in ("md", "markdown") else "txt"

    # Upload to MinIO
    storage_key = f"kb_documents/{content_hash}/{filename}"
    await storage.ensure_bucket("interview-agent")
    await storage.upload(
        "interview-agent", storage_key, file_bytes,
        "text/markdown" if source_type == "markdown" else "text/plain",
    )

    doc = await repo.create(
        title=filename,
        source_type=source_type,
        storage_key=storage_key,
        content_hash=content_hash,
        uploaded_by=current_user.id,
    )

    # Dispatch async processing to Celery
    try:
        from interview_worker.tasks.kb_tasks import process_kb_document
        process_kb_document.delay(doc.id)
    except Exception:
        pass  # Celery not available; admin can retry later

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
