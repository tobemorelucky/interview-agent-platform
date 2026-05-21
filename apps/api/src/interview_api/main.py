import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from interview_api.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
from interview_api.core.exceptions import AppError
from interview_api.core.response import error
from interview_api.infrastructure.db.engine import engine
from interview_api.modules.auth.router import router as auth_router
from interview_api.modules.kb.admin_router import router as kb_admin_router
from interview_api.modules.qa.router import router as qa_router


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


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error(code=exc.code, message=exc.message),
    )


app.include_router(auth_router)
app.include_router(kb_admin_router)
app.include_router(qa_router)


@app.get("/")
async def root():
    return {"name": settings.app_name, "version": "0.1.0"}


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
