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
- 事件顺序：
  1. `status: {stage: "analyzing_query"}` — 正在理解问题
  2. `status: {stage: "embedding_query"}` — 正在生成查询向量
  3. `status: {stage: "retrieving"}` — 正在检索知识库
  4. `retrieval: {top_k, hit_count}` — 检索完成（召回片段数）
  5. `citation: [{chunk_id, doc_id, title, source_type, preview, score}]` — 引用来源预览（不含完整内容）
  6. `status: {stage: "generating"}` — 正在生成回答
  7. `token: {content}` — 流式 token
  8. `done: {message_id}` — 回答完成
- 错误：`error: {code, message}`

### SSE 事件类型

| 事件 | 数据结构 | 说明 |
|------|---------|------|
| `status` | `{"stage": "analyzing_query" \| "embedding_query" \| "retrieving" \| "generating"}` | RAG 流水线进度 |
| `retrieval` | `{"top_k": int, "hit_count": int}` | 检索结果统计 |
| `citation` | `[{"chunk_id": int, "doc_id": int, "title": str, "source_type": str, "preview": str, "score": float \| null}]` | 引用来源预览 |
| `token` | `{"content": str}` | 流式回答片段 |
| `done` | `{"message_id": int}` | 回答完成 |
| `error` | `{"code": str, "message": str}` | 错误信息 |

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

---

# 12. Phase 4：面经采集与查询 API

## 12.1 管理员 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/admin/experience/tasks` | 创建面经采集任务 |
| GET | `/api/v1/admin/experience/tasks` | 任务列表 |
| GET | `/api/v1/admin/experience/tasks/{id}` | 任务详情 |
| POST | `/api/v1/admin/experience/tasks/{id}/search` | 对 PENDING/FAILED/SEARCH_COMPLETED 任务执行 SearXNG 搜索 |
| GET | `/api/v1/admin/experience/tasks/{id}/sources` | 查看任务发现到的 Source item 列表（可按 fetch_status 筛选） |
| GET | `/api/v1/admin/experience/reviews` | 待审核面经列表 |
| POST | `/api/v1/admin/experience/reviews/{id}/approve` | 审核通过 |
| POST | `/api/v1/admin/experience/reviews/{id}/reject` | 审核拒绝 |
| GET | `/api/v1/admin/experience/keywords` | 关键词预设列表 |
| POST | `/api/v1/admin/experience/keywords` | 新增关键词预设 |
| PATCH | `/api/v1/admin/experience/keywords/{id}` | 更新关键词预设 |
| DELETE | `/api/v1/admin/experience/keywords/{id}` | 删除关键词预设 |

## 12.2 用户 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/experiences/recent` | 近期面经总结列表（岗位+时间筛选） |
| GET | `/api/v1/experiences/recent/{id}` | 面经详情（总结+题目+可信度+原帖 URL） |

---

# 13. M3：Candidate Profile（本人私有）

所有接口要求后端用户认证，禁止跨用户版本或 Evidence 访问。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/v1/candidate-profile` | current、normalized、Evidence、generations |
| GET | `/api/v1/candidate-profile/versions` | 本人版本列表 |
| GET | `/api/v1/candidate-profile/versions/{id}` | 历史完整只读版本；跨用户 404 |
| POST | `/api/v1/candidate-profile/generate` | `{resume_id, regenerate:false}`，202 入队；显式 true 创建新代 token |
| POST | `/api/v1/candidate-profile/corrections` | `{base_version_id, field_path, new_value, reason}`，201 完整 N+1 |

Correction path 仅允许已有事实的 label / statement，不允许编辑 Evidence、producer、
版本号或任意 JSON。陈旧 base 返回 `409 PROFILE_VERSION_CONFLICT`。
有 ResumeEvidence / CandidateProfileVersion / ProfileGeneration 引用的 Resume 删除
返回 `409 RESUME_REFERENCED_BY_PROFILE`，检查发生在 MinIO 删除之前。
画像失败独立记录，不改变成功 ResumeReport 的状态。
详见 [M3 实现与验收](21_candidate_profile_m3.md)。

# 14. M4 Mock Interview（替代早期可变 Resume/岗位交互流程）

原 `/api/v1/interview` 和 SSE 保留，均要求本人权限。GET `/targets` 仅读公共数据库。
POST `/sessions` 要求 company_id/job_id，可选 Profile/JD version 和题量 1–40；创建
即固定 Snapshot。POST `/sessions/{id}/start` 提首题；POST `/messages/stream` 的完整
路径仍为 `/sessions/{id}/messages/stream`，请求包含 content/question_id/idempotency_key。
同 key 重试必须同题同正文，已成功结果复用；GET Session 提供 pending_answer 原 key。
SSE status 在调用前，evaluation/follow_up/question/complete/done 必须在 DB 提交后。
失败有明确 error，不生成默认评分。complete 为 DB 确定性统计，不是 Final Report。
questions/current/detail/skip 及本人 Session 删除保留；旧 target-position/resume/
questions-generate mutation 返回 409，legacy Session 只读。
详见 [M4 API、SSE 与验收](22_mock_interview_core_m4.md)。

# 15. M5 Report 与受控 Memory approval

本人接口 `/api/v1/interview-reports` 支持历史列表，`/sessions/{id}` 读取完成面试统计/
当前报告/生成历史，`/sessions/{id}/generate` POST 生成；主动 regenerate 必须附稳定
request_key。`/{report_id}` 读取历史详情。报告失败不改变 Session COMPLETED，统计仍可见。
`/api/v1/memory-proposals` 列出本人提案；`/{id}/approve`、`/reject`、`/correct` POST 为明确
用户决定，correct 要求 content/reason，修正与批准同事务。重复相同决定幂等，冲突 409。
新式 Session 的旧 `/memory/consolidate` 返回 409 CONTROLLED_MEMORY_REQUIRED。
已有任意 Report generation 的 Session 删除返回 409 INTERVIEW_REPORT_REFERENCED。
详见 [M5 详细 API 和验收](23_interview_coach_m5.md)。

# M6 Job Matching

本人接口 `/api/v1/job-matches` POST 用 request_key/filters 固定 Run；GET 用 limit/offset
分页读取历史。`/{id}` GET 读取固定输入/候选/排除/推荐，`/{id}/generate` POST 生成或安全
重试。FAILED 是已保存且可读取的执行状态；同 key 不同请求或正在生成返回 409，跨用户 404。
Coverage 不足不调用 Research。推荐按钮只交接 Company/Job 到原 M4 新建接口。
详见 [M6 API、Eligibility 与验收](24_job_matching_m6.md)。
