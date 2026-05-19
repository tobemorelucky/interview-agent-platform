# CLAUDE.md

## 1. Project Identity

This repository implements **Interview Agent Platform**, an intelligent interview preparation system for job seekers.

The product has three core user-facing capabilities:

1. **Interview Knowledge Q&A**
   - RAG-based question answering over curated interview knowledge.
   - Covers backend, AI application, RAG, Agent, engineering, project explanation, and common interview topics.

2. **Resume-Driven Mock Interview**
   - Users upload resumes.
   - The system parses the resume, identifies projects and technical claims, and generates likely interviewer questions with answer guidance and follow-up questions.

3. **Published Interview Experience Library**
   - Administrators create ingestion tasks for content sources such as Nowcoder, Xiaohongshu, and Douyin.
   - The system collects raw content, parses text/media, applies OCR/ASR when needed, extracts structured interview experiences, scores reliability, deduplicates, sends results to a candidate pool, and only published records become searchable to normal users.

**Critical boundary:**  
Normal users **do not trigger live crawling**. They can only query already published interview experiences stored in the formal experience library.

---

## 2. Non-Negotiable Product Boundaries

Claude Code must not violate these boundaries:

1. Do not convert the user-side experience search into real-time web search or live crawling.
2. Do not put raw or unreviewed candidate content into Milvus.
3. Do not expose raw collected data directly to ordinary users.
4. Do not mix the online query path with the offline ingestion path.
5. Do not silently expand scope into real-time voice interview, job delivery tracking, automatic job recommendation, or full social media operations.
6. Do not replace the defined stack without explicit instruction.
7. Do not hard-code one LLM / ASR / OCR / embedding provider inside domain logic.

---

## 3. Target Architecture

### 3.1 Architecture Style

Use:

- **Modular Monolith API** for business services.
- **Separate Worker Service** for asynchronous ingestion and heavy processing.
- **Provider / Adapter abstractions** for external services and models.
- **PostgreSQL** for structured business data and workflow state.
- **Milvus** for vector indexes of **published searchable knowledge only**.
- **Redis** for caching, task coordination, and queue-related support.
- **MinIO / S3-compatible object storage** for uploaded files, raw snapshots, images, videos, audios, OCR/ASR artifacts.

### 3.2 Major Runtime Components

```text
apps/web      -> Vue3 + TypeScript frontend
apps/api      -> FastAPI API service
apps/worker   -> Celery worker service
PostgreSQL   -> business DB
Milvus       -> vector DB
Redis        -> cache / broker-related support
MinIO        -> object storage
```

---

## 4. Development Tooling Standards

### 4.1 Python Environment

Use:

- Python 3.11+
- `uv` for Python dependency and virtual environment management

Do not introduce Poetry, Pipenv, or Conda as the project standard unless explicitly instructed.

Expected commands should be designed around:

```bash
uv venv
uv sync
uv run ...
```

### 4.2 Frontend Package Manager

Use:

- `pnpm` as the frontend package manager

Do not mix npm/yarn/pnpm lockfiles.

### 4.3 Infrastructure

Use Docker Compose for local infrastructure.

Phase 0 must prepare at least:

- PostgreSQL
- Redis
- Milvus Standalone
- MinIO

The API / Worker / Frontend may initially run locally during development, but the repository should preserve a clear path to containerization.

---

## 5. Backend Module Boundaries

Expected API-side modules:

```text
auth
users
rag_qa
resume_analysis
experience_query
admin_collection
admin_review
shared/common
```

Expected infrastructure-side abstractions:

```text
db
redis
milvus
storage
llm
embedding
reranker
ocr
asr
source_adapters
```

---

## 6. Offline Ingestion Pipeline

The ingestion pipeline must stay explicit and decomposed.

Canonical stages:

1. Discovery
2. Collection
3. Raw storage
4. Parsing
5. OCR / ASR when applicable
6. Normalized text fusion
7. Structured extraction
8. Reliability scoring
9. Deduplication
10. Candidate pool persistence
11. Admin review or automatic publish rule
12. Published library persistence
13. Milvus indexing for published data only

Each stage should be:

- Idempotent
- Observable
- Retriable where reasonable
- State-driven
- Able to preserve intermediate outputs

---

## 7. Provider / Adapter Principles

Required abstractions:

```text
LLMProvider
EmbeddingProvider
RerankProvider
OCRProvider
ASRProvider
ObjectStorageProvider
VectorStoreProvider
SourceAdapter
```

Platform integrations must follow `SourceAdapter` style.

Candidate source adapters:

```text
NowcoderAdapter
XiaohongshuAdapter
DouyinAdapter
MockSourceAdapter
```

The business layer should consume normalized content objects, not platform-specific raw schemas.

---

## 8. Milvus Rules

Milvus stores only searchable, published vectorized entities.

Allowed searchable collections:

```text
kb_chunks_current
experience_chunks_current
experience_questions_current
```

Potential later extension:

```text
resume_chunks_current
```

But resume vectors are private and must not mix with public content.

Forbidden:

- Raw crawled content directly into Milvus
- Candidate pool content directly into Milvus
- Failed parsing output directly into Milvus
- Unpublished experience content directly into Milvus

---

## 9. Security and Permission Rules

Roles:

```text
USER
ADMIN
```

Normal user:

- Query published data
- Upload own resume
- View own chat and analysis history

Admin:

- Create collection tasks
- Review candidate content
- Publish / reject / unpublish experience content
- Trigger selected reprocessing operations

Use permission dependencies in API routes.  
Never trust frontend-only checks.

---

## 10. Documentation as Source of Truth

Before making major changes, read the relevant documents under `docs/`.

If implementation and docs conflict:

1. Do not guess.
2. Surface the conflict.
3. Propose a resolution.
4. Update docs when architectural behavior changes.

---

## 11. Non-Trivial Task Workflow

For any task that:

- Touches multiple modules
- Adds a new domain feature
- Alters Docker / infrastructure
- Adds database migrations
- Adds or modifies worker pipeline
- Changes API contracts
- Changes project boundaries

Claude Code should:

1. Read relevant docs.
2. Inspect existing code.
3. Produce an implementation plan.
4. List files expected to change.
5. List risks and assumptions.
6. Wait for confirmation before editing files.

---

## 12. Delivery Reporting Standard

After completing a task, report:

1. What changed
2. Why it changed
3. Files changed
4. Database migrations
5. API changes
6. Commands executed
7. Tests / checks run
8. Known limitations
9. Recommended next step

---

## 13. Coding Standards

Backend:

- Python 3.11+
- FastAPI
- SQLAlchemy 2.x style
- Alembic
- Pydantic v2
- Type annotations when practical
- Explicit service / repository boundaries
- Structured error responses
- Structured logging

Frontend:

- Vue 3
- TypeScript
- Vite
- Pinia
- Vue Router
- Componentized pages
- Clear page-level loading/error states

---

## 14. Phase Discipline

Current development should proceed in phases:

0. Project bootstrap and infrastructure
1. Authentication and users
2. RAG interview knowledge Q&A
3. Resume-driven interview analysis
4. Admin collection framework with mock adapter
5. Nowcoder ingestion
6. Xiaohongshu text/image ingestion
7. Douyin video ingestion
8. Evaluation, observability, optimization, deployment polish

Do not skip phases unless explicitly instructed.
