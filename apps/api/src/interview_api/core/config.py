import os
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> Path:
    """Walk up from CWD to find the repo-root .env file.

    This is robust even when the package is installed in site-packages
    (e.g. the Worker imports interview_api as a dependency).
    """
    # 1. Explicit override
    if override := os.getenv("ENV_FILE"):
        return Path(override)

    # 2. Walk up from CWD
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate

    # 3. Fallback: relative to this source file
    return Path(__file__).resolve().parents[5] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_find_env_file()),
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    # App
    app_env: str = "development"
    app_debug: bool = True
    app_name: str = "Interview Agent Platform"
    sql_echo: bool = False

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
    llm_fallback_model: str = ""
    llm_request_timeout_seconds: float = 60.0
    llm_max_retries: int = 0

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

    # Experience Search
    experience_search_provider: str = "searxng"
    searxng_base_url: str = "http://localhost:8080"
    searxng_timeout_seconds: float = 20.0
    searxng_engines: str = ""
    experience_search_max_results: int = 20
    experience_search_language: str = "zh-CN"
    experience_search_safesearch: int = 0
    experience_search_enable_time_range: bool = False

    # Public data tools (LLM-independent)
    public_search_tool_timeout_seconds: float = 30.0
    public_fetch_tool_timeout_seconds: float = 30.0
    public_fetch_timeout_seconds: float = 15.0
    public_fetch_max_response_bytes: int = 2_000_000
    public_fetch_max_content_chars: int = 200_000
    public_fetch_max_redirects: int = 5
    public_fetch_allowed_content_types: str = "text/html,application/xhtml+xml"

    # Public research model routes and deterministic publication trust.
    public_research_base_url: str = ""
    public_research_api_key: str = ""
    public_research_extract_model: str = ""
    public_research_quality_model: str = ""
    public_research_model_timeout_seconds: float = 60.0
    public_research_max_output_tokens: int = 6000
    public_research_provider_concurrency: int = 2
    public_research_max_evidence_chars: int = 24000
    public_research_official_domains: dict[str, list[str]] = {}

    # Resume
    resume_profile_model: str = ""
    resume_profile_base_url: str = ""
    resume_profile_api_key: str = ""
    resume_profile_timeout_seconds: float = 60.0
    resume_profile_max_output_tokens: int = 6000
    resume_profile_provider_concurrency: int = 2
    resume_max_file_size_mb: int = 10
    resume_allowed_types: str = "pdf,docx,txt"
    resume_question_count: int = 8

    # Resume KB Retrieval
    resume_kb_retrieval_enabled: bool = True
    resume_retrieval_top_k: int = 8
    resume_retrieval_query_count: int = 5
    resume_retrieval_min_score: float = 0.55
    resume_fallback_to_llm: bool = True

    # Interview Chat
    interview_main_model: str = ""
    answer_evaluation_model: str = ""
    interview_model_base_url: str = ""
    interview_model_api_key: str = ""
    interview_model_timeout_seconds: float = 60.0
    interview_model_max_output_tokens: int = 3000
    interview_provider_concurrency: int = 2
    interview_max_rounds: int = 40
    interview_max_duration_seconds: int = 7200
    interview_max_model_tokens: int = 200000
    # M5 report synthesis is independent of the completed interview state.
    interview_coach_model: str = ""
    interview_coach_base_url: str = ""
    interview_coach_api_key: str = ""
    interview_coach_timeout_seconds: float = 60.0
    interview_coach_max_output_tokens: int = 6000
    interview_coach_provider_concurrency: int = 2
    interview_coach_max_input_bytes: int = 200000
    interview_approved_memory_limit: int = 8
    interview_approved_memory_token_budget: int = 4000
    interview_observation_limit: int = 5
    interview_observation_token_budget: int = 2000
    interview_memory_compression_trigger_turns: int = 50
    interview_memory_recent_keep_count: int = 10
    interview_retrieval_top_k: int = 8
    interview_retrieval_min_score: float = 0.55
    interview_max_context_chars: int = 8000
    interview_resume_raw_text_preview_chars: int = 2000

    # Interview Question Generation
    interview_question_count: int = 20
    interview_question_retrieval_top_k: int = 5
    interview_question_retrieval_min_score: float = 0.55
    interview_dimension_extraction_enabled: bool = True
    interview_max_follow_ups_per_question: int = 2
    interview_require_target_position: bool = True
    interview_default_mode: str = "comprehensive"
    interview_enable_dynamic_question: bool = True

    # M6 bounded matching, never performs public research or changes Profile.
    job_matching_model: str = ""
    job_matching_base_url: str = ""
    job_matching_api_key: str = ""
    job_matching_timeout_seconds: float = 60.0
    job_matching_max_output_tokens: int = 6000
    job_matching_provider_concurrency: int = 2
    job_matching_max_input_bytes: int = 200000
    job_matching_top_n: int = 20
    job_matching_retrieval_limit: int = 200
    job_matching_preference_limit: int = 8
    job_matching_preference_budget: int = 4000

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    @model_validator(mode="after")
    def protect_production_jwt(self):
        """Fail closed outside explicitly local environments; never generate keys."""
        if self.app_env.strip().lower() in {"development", "test"}:
            return self
        value = self.jwt_secret_key.strip()
        compact = value.lower().replace("-", "").replace("_", "").replace(" ", "")
        if (len(value) < 32 or len(set(value)) < 8 or
                any(marker in compact for marker in (
                    "changeme", "yourjwt", "yoursecret", "placeholder",
                    "development", "devsecret", "testsecret", "example", "replacewith",
                ))):
            raise ValueError("JWT_SECRET_KEY must be a configured strong secret outside development/test")
        return self


settings = Settings()
