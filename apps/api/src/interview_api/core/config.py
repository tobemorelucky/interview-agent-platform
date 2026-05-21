import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve repo root relative to this file:
# config.py → core → interview_api → src → api → apps → repo_root
_ROOT = Path(__file__).resolve().parents[5]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", str(_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: str = "development"
    app_debug: bool = True
    app_name: str = "Interview Agent Platform"

    # Database
    database_url: str = (
        "postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/interview_agent"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "interview-agent"
    minio_secure: bool = False

    # JWT
    jwt_secret_key: str = "change_me_to_a_random_string"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 120

    # LLM
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # Embedding
    embedding_provider: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_dim: int = 768

    # KB chunking
    kb_chunk_size: int = 800
    kb_chunk_overlap: int = 120
    kb_chunk_min_size: int = 80
    kb_embedding_batch_size: int = 20

    # RAG retrieval
    rag_retrieval_top_k: int = 3
    rag_context_max_chars: int = 4000
    rag_citation_preview_chars: int = 240

    # OCR / ASR
    ocr_provider: str = ""
    asr_provider: str = ""

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"


settings = Settings()
