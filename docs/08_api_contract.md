# 08 API 契约设计

## 1. 设计原则

- RESTful 风格；
- `/api/v1` 前缀；
- 管理员 API 使用 `/api/v1/admin/...`；
- 标准响应结构；
- 任务型接口返回 task/report/run ID；
- SSE 仅用于流式生成；
- 权限校验由后端实现。

---

# 2. 标准响应

成功：

```json
{
  "code": "OK",
  "message": "success",
  "data": {}
}
```

失败：

```json
{
  "code": "VALIDATION_ERROR",
  "message": "Invalid request",
  "data": null
}
```

---

# 3. Auth API

## POST `/api/v1/auth/register`

请求：

```json
{
  "email": "user@example.com",
  "username": "user01",
  "password": "password"
}
```

响应：

```json
{
  "user_id": "..."
}
```

---

## POST `/api/v1/auth/login`

请求：

```json
{
  "email": "user@example.com",
  "password": "password"
}
```

响应：

```json
{
  "access_token": "...",
  "token_type": "bearer"
}
```

---

## GET `/api/v1/auth/me`

响应：

```json
{
  "id": "...",
  "email": "...",
  "username": "...",
  "role": "USER"
}
```

---

# 4. RAG QA API

## POST `/api/v1/qa/sessions`

创建会话。

响应：

```json
{
  "session_id": "..."
}
```

## GET `/api/v1/qa/sessions`

获取我的会话列表。

## GET `/api/v1/qa/sessions/{session_id}`

获取会话详情与历史消息。

## POST `/api/v1/qa/chat/stream`

请求：

```json
{
  "session_id": "...",
  "message": "RAG 中 rerank 有什么作用？"
}
```

响应：

- `text/event-stream`
- 事件建议：
  - `token`
  - `citation`
  - `done`
  - `error`

---

# 5. Resume API

## POST `/api/v1/resumes/upload`

multipart/form-data：

- file

响应：

```json
{
  "resume_id": "...",
  "analysis_report_id": "...",
  "status": "PENDING"
}
```

## GET `/api/v1/resume-reports/{report_id}`

响应：

```json
{
  "id": "...",
  "status": "SUCCESS",
  "summary": "...",
  "skills": [],
  "projects": [],
  "questions": []
}
```

## GET `/api/v1/resume-reports`

获取当前用户历史报告。

---

# 6. Experience Query API

## GET `/api/v1/experiences/search`

查询参数建议：

```text
q=
company=
position=
platform=
stage=
min_score=
start_date=
end_date=
page=
page_size=
```

响应：

```json
{
  "query_summary": "...",
  "matched_questions": [],
  "experiences": [],
  "facets": {}
}
```

## GET `/api/v1/experiences/{experience_id}`

返回正式发布面经详情。

---

# 7. Admin Collection API

## POST `/api/v1/admin/crawl-runs`

请求：

```json
{
  "name": "字节大模型应用岗面经采集",
  "platforms": ["NOWCODER"],
  "keywords": ["字节 大模型应用 实习 面经"],
  "limit_per_platform": 50,
  "download_images": false,
  "download_videos": false,
  "enable_ocr": false,
  "enable_asr": false,
  "enable_llm_extraction": true
}
```

响应：

```json
{
  "crawl_run_id": "...",
  "status": "CREATED"
}
```

## GET `/api/v1/admin/crawl-runs`

任务列表。

## GET `/api/v1/admin/crawl-runs/{run_id}`

任务详情。

## POST `/api/v1/admin/crawl-runs/{run_id}/retry`

重试失败部分。

---

# 8. Admin Candidate Review API

## GET `/api/v1/admin/candidates`

查询候选列表。

筛选：

```text
platform=
review_status=
min_score=
marketing_risk=
page=
page_size=
```

## GET `/api/v1/admin/candidates/{candidate_id}`

候选详情，包括：

- raw 内容摘要
- parsed 文本
- OCR 文本
- ASR 文本
- fused 文本
- extraction_json
- score explanation
- dedup info

## POST `/api/v1/admin/candidates/{candidate_id}/publish`

发布候选内容。

请求：

```json
{
  "note": "内容完整且可信"
}
```

## POST `/api/v1/admin/candidates/{candidate_id}/reject`

拒绝候选内容。

## POST `/api/v1/admin/candidates/{candidate_id}/rescore`

重新评分。

## POST `/api/v1/admin/candidates/{candidate_id}/reprocess`

重新解析或重新抽取，具体参数可后续扩展。

---

# 9. Admin Published Content API

## POST `/api/v1/admin/experiences/{experience_id}/unpublish`

下架正式面经。

## POST `/api/v1/admin/experiences/{experience_id}/reindex`

重新索引。

---

# 10. 错误码建议

```text
AUTH_INVALID_CREDENTIALS
AUTH_TOKEN_EXPIRED
AUTH_PERMISSION_DENIED

USER_NOT_FOUND

RESUME_FILE_TYPE_INVALID
RESUME_PARSE_FAILED
RESUME_REPORT_NOT_FOUND

QA_SESSION_NOT_FOUND

CRAWL_RUN_NOT_FOUND
CRAWL_RUN_PERMISSION_DENIED
CRAWL_TASK_RETRY_NOT_ALLOWED

CANDIDATE_NOT_FOUND
CANDIDATE_ALREADY_PUBLISHED
CANDIDATE_REJECTED

PUBLISH_FAILED
INDEX_JOB_FAILED
```

---

# 11. API 契约演进原则

1. 后端实现必须与本文档同步；
2. 若修改请求/响应字段，更新文档；
3. 前端与后端字段应共识后调整；
4. Phase 0 只需健康检查 API；
5. Phase 1 起逐步落地正式接口。
