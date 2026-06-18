import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from interview_api.core import exceptions as legacy_exceptions
from interview_api.core.config import settings
from interview_api.core.errors import AppError, error_response
from interview_api.core.middleware import RequestContextMiddleware
from interview_api.infrastructure.db.engine import engine
from interview_api.modules.audit.router import router as audit_router
from interview_api.modules.auth.router import router as auth_router
from interview_api.modules.experience.router import router as experience_router
from interview_api.modules.interview.router import router as interview_router
from interview_api.modules.kb.admin_router import router as kb_admin_router
from interview_api.modules.memory.router import router as memory_router
from interview_api.modules.qa.router import router as qa_router
from interview_api.modules.resume.router import router as resume_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            code=exc.code,
            message=exc.message,
            request_id=_request_id(request),
            details=exc.details,
        ),
    )


@app.exception_handler(legacy_exceptions.AppError)
async def legacy_app_error_handler(
    request: Request,
    exc: legacy_exceptions.AppError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            code=exc.code,
            message=exc.message,
            request_id=_request_id(request),
        ),
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            code="HTTP_ERROR",
            message=str(exc.detail),
            request_id=_request_id(request),
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_response(
            code="VALIDATION_ERROR",
            message="Invalid request parameters",
            request_id=_request_id(request),
            details={"errors": exc.errors()},
        ),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request error")
    return JSONResponse(
        status_code=500,
        content=error_response(
            code="INTERNAL_SERVER_ERROR",
            message="Internal server error",
            request_id=_request_id(request),
        ),
    )


app.include_router(auth_router)
app.include_router(kb_admin_router)
app.include_router(qa_router)
app.include_router(resume_router)
app.include_router(interview_router)
app.include_router(experience_router)
app.include_router(memory_router)
app.include_router(audit_router)


@app.get("/")
async def root():
    return {"name": settings.app_name, "version": "0.1.0"}


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
