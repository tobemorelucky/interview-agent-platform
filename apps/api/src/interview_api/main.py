from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from interview_api.core.config import settings

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"name": settings.app_name, "version": "0.1.0"}


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
