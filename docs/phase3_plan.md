# Phase 3 Implementation Plan: Resume-Driven Mock Interview

## Status: IMPLEMENTED WITH CELERY WORKER RUNTIME

---

## 0. KB Integration Decision

**Phase 3 v1 MUST include lightweight KB retrieval.**

The resume mock interview is not a standalone LLM question generator. It should:

1. Parse the resume and extract structured information.
2. Derive retrieval queries from the structured resume (tech stack, projects, internships, target role, risk points).
3. Search the existing `kb_chunks_current` Milvus collection for relevant historical interview questions and knowledge chunks.
4. Use retrieved results as the **preferred source** for generating interview questions.
5. Fall back to LLM-generated questions **only when** retrieval results are insufficient (too few hits, low relevance scores, or retrieval disabled by config).

This approach:
- Grounds questions in real interview data rather than LLM imagination.
- Makes the product immediately more valuable even before Phase 4 (experience ingestion).
- Reuses the existing Phase 2 KB infrastructure with no new Milvus collections required in v1.
- The retrieval enrichment is a core feature, not an afterthought.

### Future upgrade path

- Phase 3 v1: Use `kb_chunks_current` for retrieval.
- Phase 4+: When interview experience ingestion (Nowcoder, Xiaohongshu, Douyin) is online, switch to a dedicated `experience_chunks_current` or `interview_questions_current` collection for more targeted retrieval. The abstraction layer (`VectorStoreProvider.search()`) makes this a configuration change, not a code rewrite.

---

## 1. Files to Add/Modify

### New Files (Backend)

| File | Purpose |
|------|---------|
| `apps/api/src/interview_api/modules/resume/__init__.py` | Module init |
| `apps/api/src/interview_api/modules/resume/models.py` | SQLAlchemy models: `Resume`, `ResumeReport` |
| `apps/api/src/interview_api/modules/resume/schemas.py` | Pydantic request/response schemas |
| `apps/api/src/interview_api/modules/resume/repository.py` | `ResumeRepository`, `ResumeReportRepository` |
| `apps/api/src/interview_api/modules/resume/service.py` | Business logic orchestration |
| `apps/api/src/interview_api/modules/resume/router.py` | API endpoints (user-facing) |
| `apps/api/src/interview_api/modules/resume/parser.py` | `ResumeParser` (txt/pdf/docx) |
| `apps/api/src/interview_api/modules/resume/retrieval.py` | `ResumeRetrievalService` — query generation + Milvus search + result aggregation |
| `apps/api/prompt_templates/resume_parse_v1.md` | Prompt: resume text → structured JSON |
| `apps/api/prompt_templates/resume_retrieval_queries_v1.md` | Prompt: structured resume → retrieval queries JSON |
| `apps/api/prompt_templates/resume_interview_questions_v1.md` | Prompt: structured resume + retrieved_context → interview questions JSON |
| `apps/api/alembic/versions/0005_add_resume_tables.py` | Migration for `resumes` + `resume_reports` |

### New Files (Worker)

| File | Purpose |
|------|---------|
| `apps/worker/src/interview_worker/tasks/resume_tasks.py` | Celery task: `process_resume` |

### New Files (Frontend)

| File | Purpose |
|------|---------|
| `apps/web/src/pages/ResumeListPage.vue` | Resume upload + list page |
| `apps/web/src/pages/ResumeReportPage.vue` | Report detail page with retrieval process display |
| `apps/web/src/api/resume.ts` | API client functions |
| `apps/web/src/types/resume.ts` | TypeScript interfaces |

### Modified Files

| File | What changes |
|------|-------------|
| `apps/api/src/interview_api/main.py` | Register resume router |
| `apps/api/src/interview_api/core/config.py` | Add resume config fields (§10) |
| `apps/api/src/interview_api/core/exceptions.py` | Add resume-specific exception classes |
| `apps/api/src/interview_api/infrastructure/tasks/celery_client.py` | Add `dispatch_process_resume()` |
| `apps/worker/src/interview_worker/celery_app.py` | Import `resume_tasks` module |
| `apps/worker/pyproject.toml` | Add `pypdf`, `python-docx` dependencies |
| `apps/api/pyproject.toml` | Add `pypdf`, `python-docx` dependencies |
| `apps/web/src/router/index.ts` | Add `/resumes`, `/resumes/:id` routes |
| `apps/web/src/pages/DashboardPage.vue` | Add resume card navigation |
| `apps/web/src/layouts/UserLayout.vue` | Add "简历面试" nav link |
| `apps/web/src/types/qa.ts` | Add resume types (or new file) |
| `.env.example` | Add resume config entries (§10) |

### Documentation Updates

| File | What changes |
|------|-------------|
| `README.md` | Update feature status, add Phase 3 entries |
| `docs/01_prd.md` | Mark Module C as implemented |
| `docs/02_architecture.md` | Update component diagram |
| `docs/03_backend_design.md` | Add resume module to directory structure |
| `docs/04_frontend_design.md` | Update routes, add Resume pages |
| `docs/05_database_design.md` | Add resumes/reports tables |
| `docs/08_api_contract.md` | Update Resume API section |
| `docs/11_prompt_spec.md` | Add resume parse + retrieval queries + interview questions prompts |
| `docs/14_local_development_and_deployment.md` | Update startup guide |
| `docs/15_roadmap.md` | Mark Phase 3 complete |
| `docs/16_decision_log.md` | Add Phase 3 decisions |

---

## 2. Database Design

### 2.1 `resumes` Table

```sql
CREATE TABLE resumes (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename        VARCHAR(500) NOT NULL,
    storage_key     VARCHAR(1000) NOT NULL,
    content_hash    VARCHAR(128),
    file_type       VARCHAR(10) NOT NULL,          -- pdf, docx, txt
    file_size       BIGINT,
    status          VARCHAR(20) NOT NULL DEFAULT 'UPLOADED',  -- UPLOADED, PROCESSING, COMPLETED, FAILED
    error_message   TEXT,
    task_id         VARCHAR(64),
    raw_text        TEXT,                            -- parsed raw text, populated on completion
    processing_started_at   TIMESTAMPTZ,
    processing_finished_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_resumes_user_id ON resumes(user_id);
CREATE INDEX idx_resumes_status ON resumes(status);
```

### 2.2 `resume_reports` Table

```sql
CREATE TABLE resume_reports (
    id                    BIGSERIAL PRIMARY KEY,
    resume_id             BIGINT NOT NULL UNIQUE REFERENCES resumes(id) ON DELETE CASCADE,
    user_id               BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    summary_json          JSONB,          -- resume structured summary
    retrieval_queries_json JSONB,         -- queries sent to KB for retrieval
    retrieved_context_json JSONB,         -- aggregated retrieval results
    questions_json        JSONB,          -- array of interview questions (each with source)
    suggestions_json      JSONB,          -- overall suggestions
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_resume_reports_user_id ON resume_reports(user_id);
```

### 2.3 `questions_json` Item Schema

Each question object:

```json
{
  "question": "你在项目 X 中使用了 Redis，具体是如何保证缓存与数据库一致性的？",
  "category": "project_depth",
  "difficulty": "MEDIUM",
  "reason": "简历提到Redis但没有说明具体使用场景和一致性策略",
  "source": "KB_RETRIEVED",
  "suggested_answer": "可以从 Cache-Aside、Write-Through 等模式切入...",
  "follow_up_questions": [
    "如果缓存雪崩怎么办？",
    "为什么不用本地缓存？"
  ],
  "evidence": {
    "title": "Redis缓存一致性面试题",
    "preview": "面试官常问：如何保证Redis与数据库的一致性...",
    "score": 0.87,
    "source_type": "kb_chunks_current",
    "chunk_id": 42,
    "doc_id": 7
  }
}
```

**`source` field values:**

| Value | Meaning |
|-------|---------|
| `KB_RETRIEVED` | Question derived from a KB retrieval hit with sufficient relevance |
| `LLM_GENERATED` | No relevant KB hit; LLM generated based on resume content alone |
| `HYBRID` | KB hit exists but was supplemented or significantly adapted by LLM |

**`evidence` field:**

- Present for `KB_RETRIEVED` and `HYBRID` questions.
- `null` or absent for `LLM_GENERATED` questions.
- `score` is the **COSINE similarity** returned by Milvus. Higher = more relevant. Range: [-1, 1], but in practice with normalized embeddings it stays in [0, 1]. The threshold `RESUME_RETRIEVAL_MIN_SCORE` (default 0.55) filters out low-relevance hits.

### 2.4 Status State Machine

```
UPLOADED  --→  PROCESSING  --→  COMPLETED
                           --→  FAILED
```

- `UPLOADED`: File saved to MinIO, record created, Celery task dispatched.
- `PROCESSING`: Worker has picked up the task.
- `COMPLETED`: Parse + structured extraction + KB retrieval + question generation all succeeded. `resume_reports` row created.
- `FAILED`: Any stage failed. `error_message` populated.

### 2.5 Why JSON Fields Instead of Separate `resume_questions` Table

1. **v1 simplicity**: JSON fields avoid an extra table, extra migration complexity, extra CRUD operations.
2. **Read pattern**: Reports are always read as a whole unit — never queried question-by-question.
3. **Schema flexibility**: The structured output schema may evolve during prompt iteration; JSONB handles this without migrations.
4. **Docs already agree**: `docs/05_database_design.md` already proposes JSON columns for resume analysis results.

If we later need to query individual questions (e.g., "show me all behavioral questions"), we can migrate to a `resume_questions` table in a future phase.

---

## 3. API Design

All endpoints require authentication via `get_current_user` dependency. All responses use the standard `success(data)` / `error(code, msg)` envelope.

### 3.1 `POST /api/v1/resumes/upload`

- **Auth**: USER
- **Request**: `multipart/form-data` with `file` field
- **Validation**:
  - File type must be in `RESUME_ALLOWED_TYPES` (pdf, docx, txt)
  - File size must be ≤ `RESUME_MAX_FILE_SIZE_MB` (default 10MB)
  - Empty file rejected
- **Flow**:
  1. Validate file
  2. Compute content hash (SHA-256)
  3. Upload to MinIO: `resumes/{user_id}/{uuid}.{ext}`
  4. Create `resumes` row with status `UPLOADED`
  5. `await db.commit()` (makes row visible)
  6. `dispatch_process_resume(resume_id)` via Celery
  7. Save `task_id` on resume row
  8. `await db.commit()`
  9. Return `ResumeResponse`
- **Response**: `201 Created`
  ```json
  {
    "code": "OK",
    "message": "success",
    "data": {
      "id": 1,
      "filename": "my_resume.pdf",
      "file_type": "pdf",
      "file_size": 123456,
      "status": "UPLOADED",
      "task_id": "abc123...",
      "created_at": "2026-05-22T..."
    }
  }
  ```

### 3.2 `GET /api/v1/resumes`

- **Auth**: USER
- **Query params**: `page` (default 1), `page_size` (default 20)
- **Returns**: Paginated list of current user's resumes, newest first
- **Response**:
  ```json
  {
    "code": "OK",
    "data": {
      "items": [
        {
          "id": 1,
          "filename": "my_resume.pdf",
          "file_type": "pdf",
          "file_size": 123456,
          "status": "COMPLETED",
          "task_id": "abc123...",
          "created_at": "2026-05-22T..."
        }
      ],
      "total": 5
    }
  }
  ```

### 3.3 `GET /api/v1/resumes/{resume_id}`

- **Auth**: USER (own resume only)
- **Returns**: Single resume detail (includes `raw_text` preview — first 500 chars)
- **404** if not found or not owned

### 3.4 `GET /api/v1/resumes/{resume_id}/report`

- **Auth**: USER (own resume only)
- **Returns**: Full report with structured summary, retrieval info, questions with source labels, suggestions
- **404** if resume not found or not owned
- **409** if resume not yet `COMPLETED` (returns current `status`)

**Response format:**

```json
{
  "code": "OK",
  "data": {
    "id": 1,
    "resume_id": 1,
    "summary_json": {
      "basic_info": { "name": "...", "target_role": "后端开发工程师" },
      "skills": { "languages": ["Python", "Go"], "frameworks": ["FastAPI", "Django"], "databases": ["PostgreSQL", "Redis"] },
      "projects": [{"name": "RAG问答系统", "tech_stack": ["LangChain", "Milvus", "OpenAI"]}],
      "risk_points": [{"area": "Redis", "description": "简历提到Redis但没有说明持久化策略", "severity": "MEDIUM"}]
    },
    "retrieval_queries_json": [
      {"query": "Python FastAPI 后端开发 面试题", "target": "tech_stack"},
      {"query": "RAG Milvus 向量数据库 面试题", "target": "project"},
      {"query": "Redis 缓存 持久化 面试题", "target": "risk_point"}
    ],
    "retrieved_context_json": {
      "total_hits": 12,
      "queries": [
        {
          "query": "Python FastAPI 后端开发 面试题",
          "target": "tech_stack",
          "hit_count": 5,
          "top_hits": [
            {"chunk_id": 10, "doc_id": 2, "title": "FastAPI面试常见问题", "preview": "...", "score": 0.87, "source_type": "kb_chunks_current"},
            {"chunk_id": 15, "doc_id": 2, "title": "FastAPI面试常见问题", "preview": "...", "score": 0.82, "source_type": "kb_chunks_current"}
          ]
        }
      ]
    },
    "questions_json": {
      "questions": [
        {
          "question": "FastAPI 的依赖注入系统是如何工作的？",
          "category": "tech_stack",
          "difficulty": "MEDIUM",
          "reason": "KB中有FastAPI高频面试题命中",
          "source": "KB_RETRIEVED",
          "suggested_answer": "FastAPI 使用 Depends() 构建依赖树...",
          "follow_up_questions": ["依赖注入和中间件有什么区别？"],
          "evidence": {
            "title": "FastAPI面试常见问题",
            "preview": "面试官常问：FastAPI的依赖注入...",
            "score": 0.87,
            "source_type": "kb_chunks_current",
            "chunk_id": 10,
            "doc_id": 2
          }
        },
        {
          "question": "请介绍你在简历中提到的内部工具平台的具体架构",
          "category": "project_depth",
          "difficulty": "HARD",
          "reason": "简历中的内部工具项目没有公开面经匹配，基于简历内容生成",
          "source": "LLM_GENERATED",
          "suggested_answer": "可以从业务背景、技术选型、关键挑战切入...",
          "follow_up_questions": ["这个工具的用户量级是多少？"],
          "evidence": null
        }
      ]
    },
    "suggestions_json": {
      "strengths": ["..."],
      "weaknesses_to_prepare": ["..."],
      "interview_tips": ["..."]
    }
  }
}
```

### 3.5 `DELETE /api/v1/resumes/{resume_id}`

- **Auth**: USER (own resume only)
- **Flow**:
  1. Verify ownership
  2. Delete from MinIO (log warning on failure, don't block)
  3. Delete `resume_reports` row (if exists)
  4. Delete `resumes` row
  5. `await db.commit()`
- **Response**: `200` with deleted resume id

### Error Codes

```
RESUME_FILE_TYPE_INVALID    — file type not in allowed list
RESUME_FILE_TOO_LARGE        — exceeds max size
RESUME_NOT_FOUND             — resume doesn't exist or not owned
RESUME_REPORT_NOT_READY      — resume not yet COMPLETED
RESUME_PARSE_FAILED          — text extraction failed
```

---

## 4. Worker Task Flow (Resume-Driven Retrieval Pipeline)

### 4.1 `process_resume` Task — Full Pipeline

```
Celery receives task
  → Mark status = PROCESSING (own tx, commit immediately)
  → Download file from MinIO
  → Parse file based on file_type (PDF/DOCX/TXT)
  → Save raw_text on resume row
  → Call LLM: resume_parse_v1 prompt → structured resume JSON
  → Call LLM: resume_retrieval_queries_v1 prompt → retrieval queries JSON
  → Call KB Retrieval: for each query, embed + search Milvus kb_chunks_current
  → Aggregate retrieval results; compute per-query hit counts and top scores
  → Call LLM: resume_interview_questions_v1 prompt (structured resume + retrieved_context + fallback policy) → questions JSON
  → Create resume_reports row (summary_json, retrieval_queries_json, retrieved_context_json, questions_json, suggestions_json)
  → Mark status = COMPLETED (commit)
  → On ANY error: rollback work, mark FAILED in fresh session, re-raise
```

### 4.2 Detailed Retrieval Pipeline

```
structured_resume = {skills, projects, internships, target_role, risk_points, ...}

queries[] = LLM generates from structured_resume:
  - tech_stack query     → "Python FastAPI 后端开发 面试题"
  - project_1 query      → "RAG Milvus 向量数据库 面试"
  - project_2 query      → "Agent 大模型 工具调用 面试"
  - target_role query    → "后端开发工程师 面试题"
  - risk_point query     → "Redis 缓存 一致性 持久化 面试"

for each query in queries (up to RESUME_RETRIEVAL_QUERY_COUNT):
    query_vec = embedding.embed_query(query)
    hits[] = vector_store.search("kb_chunks_current", query_vec, top_k=RESUME_RETRIEVAL_TOP_K)
    filter hits where score >= RESUME_RETRIEVAL_MIN_SCORE
    save hits in retrieved_context_json

retrieved_context_json = {
    "total_hits": <sum of all hit counts>,
    "queries": [{query, target, hit_count, top_hits[]}]
}

sufficiency check:
    total_qualified_hits = sum(query.hit_count for query in retrieved_context)
    if total_qualified_hits < RESUME_QUESTION_COUNT / 2:
        fallback_policy = "HIGH_FALLBACK"   # most questions will be LLM_GENERATED
    else:
        fallback_policy = "KB_PREFERRED"     # KB hits first, LLM fills gaps

Pass fallback_policy into resume_interview_questions_v1.md prompt
```

### 4.3 Pseudo-code

```python
@app.task(name="process_resume", bind=True)
def process_resume(self, resume_id: int):
    try:
        run_async(_process(resume_id))
    except Exception:
        logger.exception("Task FAILED resume_id=%s", resume_id)
        raise

async def _process(resume_id: int):
    # Phase 0: mark PROCESSING
    async with async_session_factory() as db0:
        repo0 = ResumeRepository(db0)
        await repo0.mark_processing_started(resume_id)
        await db0.commit()

    # Phase 1: do the work
    async with async_session_factory() as db:
        repo = ResumeRepository(db)
        report_repo = ResumeReportRepository(db)
        try:
            storage = MinioObjectStorageProvider()
            llm = OpenAICompatibleLLMProvider()
            embedding = OpenAICompatibleEmbeddingProvider()
            vector_store = MilvusVectorStoreProvider(embedding_dim=settings.embedding_dim)

            # 1. Download & parse
            resume = await repo.get_by_id(resume_id)
            file_bytes = await storage.download(bucket, resume.storage_key)
            parser = ResumeParser()
            raw_text = parser.parse(file_bytes, resume.file_type)
            await repo.update_raw_text(resume_id, raw_text)

            # 2. Structured extraction
            parse_prompt = load_prompt("resume_parse_v1").format(resume_text=raw_text)
            structured = await llm.chat([{"role": "user", "content": parse_prompt}])
            structured_json = json.loads(structured)

            # 3. Generate retrieval queries
            queries_prompt = load_prompt("resume_retrieval_queries_v1").format(
                structured_resume=json.dumps(structured_json, ensure_ascii=False),
                query_count=settings.resume_retrieval_query_count,
            )
            queries = await llm.chat([{"role": "user", "content": queries_prompt}])
            queries_json = json.loads(queries)

            # 4. KB Retrieval (if enabled)
            retrieval = ResumeRetrievalService(embedding, vector_store)
            if settings.resume_kb_retrieval_enabled:
                retrieved_context = await retrieval.retrieve(
                    queries=queries_json["queries"],
                    top_k=settings.resume_retrieval_top_k,
                    min_score=settings.resume_retrieval_min_score,
                )
                fallback_policy = retrieval.determine_fallback_policy(
                    retrieved_context,
                    question_count=settings.resume_question_count,
                )
            else:
                retrieved_context = {"total_hits": 0, "queries": []}
                fallback_policy = "NO_KB"

            # 5. Generate interview questions
            questions_prompt = load_prompt("resume_interview_questions_v1").format(
                structured_resume=json.dumps(structured_json, ensure_ascii=False),
                retrieved_context=json.dumps(retrieved_context, ensure_ascii=False),
                fallback_policy=fallback_policy,
                question_count=settings.resume_question_count,
            )
            questions = await llm.chat([{"role": "user", "content": questions_prompt}])
            questions_json = json.loads(questions)

            # 6. Save results
            await report_repo.create(ResumeReport(
                resume_id=resume_id,
                user_id=resume.user_id,
                summary_json=structured_json,
                retrieval_queries_json=queries_json,
                retrieved_context_json=retrieved_context,
                questions_json=questions_json,
                suggestions_json=questions_json.get("overall_suggestions", {}),
            ))

            await repo.mark_processing_finished(resume_id, "COMPLETED")
            await db.commit()

        except Exception:
            await db.rollback()
            error_text = _format_error()
            async with async_session_factory() as db2:
                repo2 = ResumeRepository(db2)
                await repo2.mark_processing_finished(resume_id, "FAILED", error_message=error_text)
                await db2.commit()
            raise
```

### 4.4 `ResumeRetrievalService` Class

New file: `apps/api/src/interview_api/modules/resume/retrieval.py`

```python
class ResumeRetrievalService:
    def __init__(self, embedding: EmbeddingProvider, vector_store: VectorStoreProvider):
        self.embedding = embedding
        self.vector_store = vector_store

    async def retrieve(
        self,
        queries: list[dict],
        top_k: int = 8,
        min_score: float = 0.55,
    ) -> dict:
        """For each query dict {query, target}, embed and search Milvus.

        Returns:
            {
                "total_hits": <int>,
                "queries": [
                    {
                        "query": "Python FastAPI 面试题",
                        "target": "tech_stack",
                        "hit_count": 5,
                        "top_hits": [{chunk_id, doc_id, title, preview, score, source_type}, ...]
                    },
                    ...
                ]
            }
        """
        all_results = {"total_hits": 0, "queries": []}
        for q in queries:
            query_text = q["query"]
            target = q.get("target", "general")
            vec = await self.embedding.embed_query(query_text)
            hits = self.vector_store.search(
                "kb_chunks_current",
                vec,
                top_k=top_k,
                output_fields=["id", "doc_id", "chunk_id", "title", "content", "source_type"],
            )
            qualified = [h for h in hits if h.get("score", 0) >= min_score]
            preview_chars = settings.rag_citation_preview_chars
            top_hits = [
                {
                    "chunk_id": h["id"],
                    "doc_id": h["doc_id"],
                    "title": h.get("title", ""),
                    "preview": (h.get("content", "") or "")[:preview_chars],
                    "score": round(h["score"], 4),
                    "source_type": h.get("source_type", ""),
                }
                for h in qualified
            ]
            all_results["total_hits"] += len(top_hits)
            all_results["queries"].append({
                "query": query_text,
                "target": target,
                "hit_count": len(top_hits),
                "top_hits": top_hits,
            })
        return all_results

    @staticmethod
    def determine_fallback_policy(
        retrieved_context: dict,
        question_count: int = 20,
    ) -> str:
        """Determine fallback policy based on retrieval sufficiency."""
        total_hits = retrieved_context.get("total_hits", 0)
        if total_hits >= question_count:
            return "KB_PREFERRED"
        elif total_hits >= question_count / 2:
            return "KB_SUPPLEMENT"
        else:
            return "HIGH_FALLBACK"
```

### 4.5 Reuse of Phase 2 Patterns

- Same `bind=True` decorator pattern as `process_kb_document`.
- Same `run_async()` helper to run async body.
- Same three-session pattern: mark PROCESSING → do work → mark result (fresh session on failure).
- Same `_format_error()` for error truncation.
- Same `send_task("process_resume", args=[resume_id])` dispatch from API via Celery client.
- Worker imports `interview_api` modules (repository, service, providers) — already proven pattern.
- `ResumeRetrievalService` reuses the same `OpenAICompatibleEmbeddingProvider` and `MilvusVectorStoreProvider` used by `QaService`.
- No new Milvus collections — searches `kb_chunks_current` directly.

---

## 5. Resume Parsing Design

### 5.1 `ResumeParser` Class

```python
class ResumeParser:
    def parse(self, data: bytes, file_type: str) -> str:
        if file_type == "txt":
            return self._parse_txt(data)
        elif file_type == "pdf":
            return self._parse_pdf(data)
        elif file_type == "docx":
            return self._parse_docx(data)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")

    def _parse_txt(self, data: bytes) -> str:
        return data.decode("utf-8", errors="replace")

    def _parse_pdf(self, data: bytes) -> str:
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)

    def _parse_docx(self, data: bytes) -> str:
        from io import BytesIO
        from docx import Document
        doc = Document(BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
```

### 5.2 Parsing Notes

- No OCR in v1 — image-based PDFs will return empty/minimal text, which will be surfaced as a parsing failure.
- `pypdf` is chosen over `pdfplumber` for lighter weight; `pdfplumber` can be swapped in later if table extraction is needed.
- `python-docx` handles `.docx` only; legacy `.doc` is not supported in v1.
- Parsing failures are caught and result in `FAILED` status with clear error message.

---

## 6. Prompt Template Design

### 6.1 `resume_parse_v1.md`

**Purpose**: Resume text → structured resume JSON

**Input**: `{resume_text}` (raw parsed text from the resume file)

**Output**: JSON with:
```json
{
  "basic_info": {
    "name": "",
    "email": "",
    "phone": "",
    "location": "",
    "years_of_experience": null,
    "current_role": "",
    "target_role": ""
  },
  "education": [
    {"school": "", "degree": "", "major": "", "start_year": null, "end_year": null}
  ],
  "skills": {
    "languages": [],
    "frameworks": [],
    "databases": [],
    "tools": [],
    "ai_ml": [],
    "other": []
  },
  "projects": [
    {
      "name": "",
      "role": "",
      "duration": "",
      "description": "",
      "tech_stack": [],
      "key_contributions": [],
      "quantitative_results": []
    }
  ],
  "internships": [
    {
      "company": "",
      "role": "",
      "duration": "",
      "responsibilities": [],
      "tech_stack": []
    }
  ],
  "publications": [],
  "highlights": [],
  "risk_points": [
    {
      "area": "",
      "description": "",
      "severity": "HIGH|MEDIUM|LOW"
    }
  ]
}
```

**Prompt requirements**:
- Do NOT fabricate information not in the resume.
- Return empty arrays/objects for missing fields.
- Identify potential interview risk points (vague descriptions, tenure gaps, skill mismatches, overclaimed tech without supporting project detail).

---

### 6.2 `resume_retrieval_queries_v1.md`

**Purpose**: Structured resume → retrieval queries for KB search

**Input**: `{structured_resume}`, `{query_count}`

**Output**: JSON with:
```json
{
  "queries": [
    {
      "query": "Python FastAPI 后端开发 面试题",
      "target": "tech_stack"
    },
    {
      "query": "RAG 检索增强生成 向量数据库 面试",
      "target": "project"
    },
    {
      "query": "Redis 缓存 一致性 持久化 面试",
      "target": "risk_point"
    }
  ]
}
```

**Prompt requirements**:
- Generate up to `{query_count}` search queries (default 5).
- Each query targets a specific area: tech_stack, project, internship, target_role, risk_point.
- Queries should be optimized for retrieval — use keywords likely to match interview knowledge base content.
- Queries should include the word "面试" to bias toward interview-related content.
- Prioritize areas with risk_points for deeper coverage.

---

### 6.3 `resume_interview_questions_v1.md`

**Purpose**: Structured resume + retrieved KB context → interview questions with source labels

**Input**: `{structured_resume}`, `{retrieved_context}`, `{fallback_policy}`, `{question_count}`

**Output**: JSON with:
```json
{
  "questions": [
    {
      "question": "FastAPI 的依赖注入系统是如何工作的？",
      "category": "tech_stack",
      "difficulty": "MEDIUM",
      "reason": "KB中有FastAPI高频面试题命中，且简历中使用FastAPI开发了核心服务",
      "source": "KB_RETRIEVED",
      "suggested_answer": "FastAPI 使用 Depends() 构建依赖树...",
      "follow_up_questions": ["依赖注入和中间件有什么区别？"],
      "evidence": {
        "title": "FastAPI面试常见问题",
        "preview": "面试官常问：FastAPI的依赖注入...",
        "score": 0.87,
        "source_type": "kb_chunks_current",
        "chunk_id": 10,
        "doc_id": 2
      }
    }
  ],
  "overall_suggestions": {
    "strengths": ["..."],
    "weaknesses_to_prepare": ["..."],
    "interview_tips": ["..."]
  }
}
```

**Critical prompt constraints**:

1. **Source priority**: Questions MUST be prioritized based on `{fallback_policy}`:
   - `KB_PREFERRED` (rich KB hits): Generate primarily from retrieved_context. Mark as `KB_RETRIEVED`. Only fill remaining slots with `LLM_GENERATED`.
   - `KB_SUPPLEMENT` (moderate KB hits): Use KB hits for matching areas, supplement with resume-generated questions. Mark accordingly.
   - `HIGH_FALLBACK` (insufficient KB hits): Generate mostly from resume content. Mark as `LLM_GENERATED`. Only use KB hits that are highly specific matches.

2. **No fabrication**:
   - Do NOT fabricate experiences, projects, or skills not in the resume.
   - If retrieved_context mentions technologies the candidate doesn't use, do NOT ask about those technologies.

3. **Evidence preservation**:
   - When a question is derived from KB retrieval, copy the evidence object from retrieved_context into the question.
   - The evidence fields (title, preview, score, source_type, chunk_id, doc_id) trace the question back to its source.

4. **Question distribution**:
   - Target `{question_count}` questions total (default 20).
   - Distribute across: tech_stack (30%), project_depth (30%), internship/behavioral (20%), risk_follow_up (10%), system_design (10%).
   - Each question must have a `reason` explaining why it was asked.

5. **Answer quality**:
   - `suggested_answer` should reference the candidate's actual project context, not generic textbook answers.
   - `follow_up_questions` should probe deeper on the specific answer.
   - Mark uncertain areas with "[需根据实际情况补充]".

**Question categories**: `tech_stack`, `project_depth`, `internship`, `behavioral`, `risk_follow_up`, `system_design`, `coding_concept`

---

## 7. Frontend Design

### 7.1 Route Changes

```typescript
// router/index.ts — add:
{
  path: "/resumes",
  name: "ResumeList",
  component: () => import("../pages/ResumeListPage.vue"),
  meta: { requiresAuth: true },
},
{
  path: "/resumes/:id",
  name: "ResumeReport",
  component: () => import("../pages/ResumeReportPage.vue"),
  meta: { requiresAuth: true },
},
```

### 7.2 `UserLayout.vue` — Add Nav Link

```typescript
const navItems = [
  { path: "/dashboard", label: "首页" },
  { path: "/qa", label: "知识问答" },
  { path: "/resumes", label: "简历面试" },  // NEW
];
```

### 7.3 `DashboardPage.vue` — Add Card

Make the "简历模拟面试" feature card clickable, navigating to `/resumes`.

### 7.4 `ResumeListPage.vue`

Same as original plan:
- Upload zone at top (drag-and-drop or click-to-upload)
- Accept `.pdf`, `.docx`, `.txt`
- Table with filename, file type badge, status tag, task_id, created time, actions
- Polling every 3s for UPLOADED/PROCESSING items
- Empty/error states

### 7.5 `ResumeReportPage.vue` — Enhanced with Retrieval Display

**Layout (updated from original plan):**

1. **简历摘要 (Resume Summary)**
   - Basic info, skills tags, education, highlights

2. **风险点 (Risk Points)**
   - Risk cards with severity badges (HIGH/MEDIUM/LOW)

3. **检索过程 (Retrieval Process)** — NEW SECTION
   - List of retrieval queries sent to KB
   - Each query shows:
     - Query text
     - Target area label
     - Hit count badge (e.g., "命中 5 条")
     - Expandable top hits with title, preview, relevance score
   - Summary: total KB hits across all queries
   - If KB retrieval was disabled, show "KB 检索未启用，问题全部由大模型生成"

4. **面试问题列表 (Interview Questions)**
   - Collapsible question cards, each showing:
     - Question number + text
     - **Source badge** (prominent, color-coded):
       - 🏷️ "历史面经 / 知识库召回" (green) for `KB_RETRIEVED`
       - 🤖 "大模型生成" (blue) for `LLM_GENERATED`
       - 🔄 "混合生成" (orange) for `HYBRID`
     - Category badge + Difficulty badge
     - "追问原因" (reason)
     - "参考回答" (suggested answer, expandable)
     - "后续追问" (follow_up_questions, as list)
     - **Evidence section** (for KB_RETRIEVED/HYBRID):
       - Source title, preview text, relevance score, link to source

5. **综合建议 (Overall Suggestions)**
   - Strengths, weaknesses to prepare, interview tips

**States**:
- Loading: spinner while fetching report
- Processing: show "简历正在分析中..." with polling
- Failed: show error message with retry hint
- Empty: "报告未生成"

### 7.6 TypeScript Types (`types/resume.ts`)

```typescript
export interface Resume {
  id: number
  user_id: number
  filename: string
  file_type: string
  file_size: number
  status: "UPLOADED" | "PROCESSING" | "COMPLETED" | "FAILED"
  error_message: string | null
  task_id: string | null
  created_at: string
  updated_at: string
}

export interface ResumeList {
  items: Resume[]
  total: number
}

export interface ResumeReport {
  id: number
  resume_id: number
  summary_json: ResumeSummary | null
  retrieval_queries_json: RetrievalQuery[] | null
  retrieved_context_json: RetrievedContext | null
  questions_json: InterviewQuestions | null
  suggestions_json: InterviewSuggestions | null
  created_at: string
}

export interface ResumeSummary {
  basic_info: BasicInfo
  education: Education[]
  skills: Skills
  projects: Project[]
  internships: Internship[]
  highlights: string[]
  risk_points: RiskPoint[]
}

export interface BasicInfo {
  name: string
  email: string
  phone: string
  location: string
  years_of_experience: number | null
  current_role: string
  target_role: string
}

export interface Skills {
  languages: string[]
  frameworks: string[]
  databases: string[]
  tools: string[]
  ai_ml: string[]
  other: string[]
}

export interface RiskPoint {
  area: string
  description: string
  severity: "HIGH" | "MEDIUM" | "LOW"
}

export interface RetrievalQuery {
  query: string
  target: string
}

export interface RetrievedContext {
  total_hits: number
  queries: RetrievedQueryResult[]
}

export interface RetrievedQueryResult {
  query: string
  target: string
  hit_count: number
  top_hits: RetrievalHit[]
}

export interface RetrievalHit {
  chunk_id: number
  doc_id: number
  title: string
  preview: string
  score: number
  source_type: string
}

export interface InterviewQuestions {
  questions: InterviewQuestion[]
}

export interface InterviewQuestion {
  question: string
  category: string
  difficulty: "EASY" | "MEDIUM" | "HARD"
  reason: string
  source: "KB_RETRIEVED" | "LLM_GENERATED" | "HYBRID"
  suggested_answer: string
  follow_up_questions: string[]
  evidence: Evidence | null
}

export interface Evidence {
  title: string
  preview: string
  score: number
  source_type: string
  chunk_id: number
  doc_id: number
}

export interface InterviewSuggestions {
  strengths: string[]
  weaknesses_to_prepare: string[]
  interview_tips: string[]
}
```

---

## 8. Status Flow Diagram

```
User uploads file
       │
       ▼
   UPLOADED  ─────────────────────┐
       │                          │
       │ Celery picks up task     │ Celery fails immediately
       ▼                          │ (e.g., file not found)
   PROCESSING                     │
       │                          │
       ├── Parse success ──┐      │
       │                    ▼      │
       │              LLM extract │
       │                    │     │
       │              LLM queries │
       │                    │     │
       │           KB retrieval   │
       │                    │     │
       │         LLM questions    │
       │                    │     │
       │                    ├── success ──→ COMPLETED
       │                    │              (report created)
       │                    │
       │                    └── error ────→ FAILED
       │                                   (error_message set)
       │
       └── Parse error ─────────────────→ FAILED
                                          (error_message set)
```

Frontend polling:
- Polls every 3s when any resume in list has `UPLOADED` or `PROCESSING` status
- Stops polling when all are `COMPLETED` or `FAILED`
- On report page: polls the specific resume status

---

## 9. Error Handling

### 9.1 API Layer

| Scenario | Error Code | HTTP Status | User Message |
|----------|-----------|-------------|--------------|
| Invalid file type | `RESUME_FILE_TYPE_INVALID` | 422 | "不支持的文件类型，仅支持 PDF、DOCX、TXT" |
| File too large | `RESUME_FILE_TOO_LARGE` | 422 | "文件大小超过限制（最大 10MB）" |
| Empty file | `RESUME_FILE_TOO_LARGE` | 422 | "文件为空" |
| Resume not found / not owned | `RESUME_NOT_FOUND` | 404 | "简历不存在" |
| Report not ready | `RESUME_REPORT_NOT_READY` | 409 | "简历分析尚未完成，当前状态：{status}" |
| Parse failed | `RESUME_PARSE_FAILED` | 422 | "简历解析失败：{error}" |
| MinIO upload failed | `STORAGE_ERROR` | 500 | "文件上传失败，请稍后重试" |

### 9.2 Worker Layer

- All exceptions caught → status `FAILED` with `error_message` truncated to 2000 chars
- Three-session pattern ensures FAILED status is always committed
- Celery re-raise ensures task is recorded as failed in Celery results
- KB retrieval failures are **non-fatal**: if retrieval fails, log warning and proceed with `HIGH_FALLBACK` (all LLM-generated questions)
- Specific error messages:
  - MinIO download failure → "无法读取简历文件: {detail}"
  - PDF parse failure → "PDF 解析失败: {detail}"
  - DOCX parse failure → "DOCX 解析失败: {detail}"
  - LLM call failure → "LLM 调用失败: {detail}"
  - JSON parse failure → "LLM 返回格式异常: {detail}"
  - KB retrieval error → logged as warning, task continues

### 9.3 Frontend

- Same as original plan (upload errors as toast, failed status with error_message, network error handling, 401 redirect)

---

## 10. Configuration Items

Add to `Settings` class and `.env.example`:

```env
# ===== Resume =====
RESUME_MAX_FILE_SIZE_MB=10
RESUME_ALLOWED_TYPES=pdf,docx,txt
RESUME_QUESTION_COUNT=20

# ===== Resume KB Retrieval =====
RESUME_KB_RETRIEVAL_ENABLED=true
RESUME_RETRIEVAL_TOP_K=8
RESUME_RETRIEVAL_QUERY_COUNT=5
RESUME_RETRIEVAL_MIN_SCORE=0.55
RESUME_FALLBACK_TO_LLM=true
```

```python
# In core/config.py Settings class:
# --- Resume ---
resume_max_file_size_mb: int = 10
resume_allowed_types: str = "pdf,docx,txt"
resume_question_count: int = 20

# --- Resume KB Retrieval ---
resume_kb_retrieval_enabled: bool = True
resume_retrieval_top_k: int = 8
resume_retrieval_query_count: int = 5
resume_retrieval_min_score: float = 0.55
resume_fallback_to_llm: bool = True
```

### Configuration semantics

| Config | Default | Meaning |
|--------|---------|---------|
| `RESUME_KB_RETRIEVAL_ENABLED` | `true` | Master switch; when `false`, skips KB retrieval entirely |
| `RESUME_RETRIEVAL_TOP_K` | `8` | Number of top hits to fetch per query from Milvus |
| `RESUME_RETRIEVAL_QUERY_COUNT` | `5` | Number of retrieval queries the LLM generates from the resume |
| `RESUME_RETRIEVAL_MIN_SCORE` | `0.55` | Minimum COSINE similarity score for a hit to be considered "qualified" |
| `RESUME_FALLBACK_TO_LLM` | `true` | When `true`, LLM fills in questions where KB hits are insufficient |
| `RESUME_QUESTION_COUNT` | `20` | Target total number of interview questions |

### Score direction clarification

Milvus uses **COSINE** metric. Score = cosine similarity between the query embedding vector and the document embedding vector.

- **Range**: [-1, 1]
- **Higher = more relevant**: A score of 0.92 means the vectors are nearly parallel (very similar). A score of 0.10 means nearly orthogonal (not similar).
- `RESUME_RETRIEVAL_MIN_SCORE=0.55` means: only accept hits where cosine similarity ≥ 0.55. This filters out weakly-related or noise results.
- In practice, with normalized embeddings from modern embedding models, scores typically fall in [0.3, 0.95] for real content matches.

---

## 11. Verification Steps

### 11.1 Backend Verification

1. Run migration: `uv run alembic upgrade head` → `resumes` and `resume_reports` tables created
2. Start API: `uv run uvicorn interview_api.main:app` → health check OK
3. Upload resume via Swagger UI → 201 with resume_id, status `UPLOADED`
4. Check DB: `resumes` row exists, `storage_key` is set, `task_id` is set
5. Start Worker: `uv run celery -A interview_worker.celery_app worker -l info`
6. Worker picks up task → status `PROCESSING` → `COMPLETED`
7. Check DB: `resume_reports` row exists with all JSON fields populated:
   - `summary_json` has structured resume
   - `retrieval_queries_json` has query list
   - `retrieved_context_json` has KB hits per query
   - `questions_json` has questions with `source` field
   - `suggestions_json` has overall suggestions
8. GET `/api/v1/resumes` → list includes the resume
9. GET `/api/v1/resumes/{id}/report` → full report with retrieval process and source labels
10. DELETE `/api/v1/resumes/{id}` → resume and report deleted, MinIO file removed
11. Upload invalid file type → 422 error
12. Upload oversized file → 422 error
13. Access another user's resume → 404

### 11.2 KB Retrieval Verification

1. Prepare a resume that includes skills like RAG, Redis, Agent (matching existing KB content).
2. Ensure KB has indexed documents containing RAG/Redis/Agent interview questions.
3. Upload resume → after processing, verify in `retrieved_context_json`:
   - Queries related to RAG/Redis/Agent are present.
   - Each query has `hit_count > 0` with relevant `top_hits`.
   - Hit scores are ≥ 0.55 (or whichever `RESUME_RETRIEVAL_MIN_SCORE` is set to).
4. Verify in `questions_json`:
   - Questions about RAG/Redis/Agent have `source: "KB_RETRIEVED"`.
   - Each KB_RETRIEVED question has a non-null `evidence` object.
   - `evidence.score` matches the retrieval hit score.
5. Set `RESUME_KB_RETRIEVAL_ENABLED=false` → re-upload → all questions should be `LLM_GENERATED`.
6. Set `RESUME_RETRIEVAL_MIN_SCORE=0.99` (very high threshold) → re-upload → KB hits should be filtered out, fallback to `LLM_GENERATED`.
7. Set `RESUME_RETRIEVAL_QUERY_COUNT=3` → verify only 3 queries in `retrieval_queries_json`.
8. Delete a KB document that was previously generating KB_RETRIEVED questions → re-upload resume → those questions should now be `LLM_GENERATED`.

### 11.3 Frontend Verification

1. Login → Dashboard shows "简历模拟面试" card (clickable)
2. Navigate to `/resumes` → empty state
3. Upload a PDF → appears in list as "待处理"
4. Polling indicator visible → status changes to "处理中" → "已完成"
5. Click "查看报告" → navigates to `/resumes/{id}`
6. **Report page shows new sections**:
   - Resume summary with skills, projects, risk points
   - **Retrieval process section**: query list, hit counts, expandable top hits
   - **Interview questions with source badges**:
     - Green "历史面经 / 知识库召回" for KB_RETRIEVED
     - Blue "大模型生成" for LLM_GENERATED
     - Orange "混合生成" for HYBRID
   - Evidence section for KB_RETRIEVED/HYBRID questions
   - Overall suggestions
7. Upload a .docx → same flow works
8. Upload a .txt → same flow works
9. Delete a resume → removed from list
10. Failed resume shows error message on report page

### 11.4 Regression Checks (Phase 2 must not break)

1. QA chat still works (POST `/api/v1/qa/chat/stream`)
2. SSE streaming still works
3. Citation preview still works
4. Admin KB upload still works
5. Admin KB batch delete still works
6. KB document list/status still correct
7. Worker still processes KB documents (`process_kb_document` unaffected)

### 11.5 Edge Cases

1. Upload same file twice → two separate records (allowed, different storage keys)
2. Very large PDF (near 10MB limit) → should be accepted
3. Image-only PDF (no text) → parsing returns minimal text → graceful error
4. Special characters in filename → stored properly, displayed escaped
5. User uploads then immediately deletes before processing → deletion should work
6. KB retrieval returns 0 hits → fallback_policy = `HIGH_FALLBACK`, all questions LLM_GENERATED
7. KB retrieval has hits but embedding API fails mid-search → log warning, continue with remaining queries
8. Resume has technologies with zero KB coverage → LLM generates resume-grounded questions
9. Milvus is down → retrieval fails non-fatally, task continues with `HIGH_FALLBACK`

---

## 12. Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| LLM JSON output not parseable | `FAILED` status, no report | Prompt explicitly requests JSON; add retry with stricter prompt on first failure |
| KB retrieval returns irrelevant hits | Low-quality KB_RETRIEVED questions | `RESUME_RETRIEVAL_MIN_SCORE` threshold filters noise; prompt instructs LLM to skip irrelevant hits |
| KB retrieval adds significant latency | Slow processing | Each query is a single vector search (fast); all queries run sequentially but each is ~50ms; total retrieval < 2s for 5 queries |
| `pypdf` fails on complex PDFs | Parse returns empty text → `FAILED` | Log warning; recommend pdfplumber as upgrade path |
| `python-docx` fails on .doc (legacy format) | `FAILED` | Clear error message explaining .doc not supported |
| LLM generates poor quality questions | User dissatisfaction | KB grounding should improve quality; prompt iteration; log quality samples |
| Worker import of `interview_api` modules breaks | Worker can't start | Already proven pattern in Phase 2; same dependency structure |
| Large files cause memory issues in Worker | OOM, task failure | 10MB limit is conservative; file processed in-memory |
| Embedding dimension mismatch | Milvus search fails | Same `OpenAICompatibleEmbeddingProvider` used by both QA and Resume; validated on init |

---

## 13. Implementation Order

1. **Config + Exceptions** — settings fields and error types
2. **Migration + Models** — `resumes` and `resume_reports` tables
3. **Repository + Schemas** — data access + Pydantic validation
4. **Prompt Templates** — `resume_parse_v1.md`, `resume_retrieval_queries_v1.md`, `resume_interview_questions_v1.md`
5. **Parser** — `ResumeParser` class
6. **Retrieval Service** — `ResumeRetrievalService` class
7. **Service + Router** — business logic + API endpoints
8. **Celery Client** — `dispatch_process_resume()`
9. **Worker Task** — `process_resume` task with full retrieval pipeline
10. **Register Router + Import Task** — wire up main.py and celery_app.py
11. **Frontend Types + API Client** — TypeScript interfaces + API functions
12. **ResumeListPage** — upload + list + polling
13. **ResumeReportPage** — report with retrieval process + source badges
14. **Router + Layout + Dashboard** — navigation wiring
15. **Documentation Update** — all docs listed in §1
16. **End-to-End Verification** — full flow test including KB retrieval + fallback scenarios

---

## 14. Known Limitations (v1)

1. **No OCR** — image-based PDFs and scanned resumes will not produce useful results.
2. **No `.doc` support** — legacy Word format not supported.
3. **Reuses `kb_chunks_current`** — retrieval searches the general knowledge base, not a dedicated interview question collection. This means some retrieved chunks may be general knowledge rather than interview-specific Q&A. Phase 4+ can switch to a dedicated `experience_chunks_current` collection.
4. **Single LLM call per stage** — no retry on malformed JSON output (can be added in v1.1).
5. **No resume versioning** — each upload creates a new independent record; no update-in-place.
6. **No admin visibility** — admins cannot view all users' resumes (by design per spec).
7. **No re-generate endpoint** — user must delete and re-upload to get a new report.
8. **No partial progress** — frontend only sees UPLOADED/PROCESSING/COMPLETED/FAILED, not "retrieving KB" substage.
9. **KB retrieval is read-only** — no feedback loop to improve KB coverage based on what questions were commonly generated.
10. **No question-level feedback** — users can't rate or flag individual questions as helpful/unhelpful.

---

## 15. Runtime Correction Notes

The implemented Phase 3 runtime uses Celery for resume processing:

```text
Frontend upload
  -> API saves file and resume row
  -> API dispatches Celery task `process_resume`
  -> Worker imports live API source from `apps/api/src`
  -> Worker updates processing_stage and status
  -> Frontend polls resume status and unlocks interview chat after COMPLETED
```

Important local development rule: the worker must not load a stale installed
copy of `interview_api` from `apps/worker/.venv/Lib/site-packages`. It must
prefer the source checkout. The worker bootstrap module
`interview_worker._paths` and `.runtime/dev/run-worker.bat` both ensure
`apps/api/src` appears before site-packages on `sys.path`.

If a resume remains `QUEUED`, inspect the worker log for:

```text
Task process_resume[...] received
ModuleNotFoundError: No module named 'interview_api.modules.resume.processor'
Cannot connect to redis://localhost:6379/1
```

These messages mean the task never reached the resume processor, so the user
interface will keep polling until the resume is marked `FAILED` or re-uploaded.

Additional runtime fixes:

- Worker and API DB sessions import `interview_api.modules.models` so SQLAlchemy
  metadata contains all tables before ORM flush. Without this, saving
  `resume_reports.user_id` can fail in the worker with `NoReferencedTableError`
  because only `resume.models` was imported and the `users` table was missing
  from the process-local metadata registry.
- The resume processor uses short DB sessions. It reads resume metadata, saves
  raw text, updates each stage, and saves the final report in separate
  transactions. Slow external calls do not hold a DB transaction open.
- Celery passes the current task id into the processor. If Redis later delivers
  an older unacknowledged task for the same resume, the processor skips it when
  the resume row already points to a newer task id.
- Resume processing exceptions are re-raised after best-effort `FAILED` status
  persistence so Celery results reflect task failure instead of reporting
  `succeeded` for a failed resume.
- The async SQLAlchemy engine uses connection pre-ping and recycle settings to
  reduce failures after local Docker infrastructure is restarted.
