# 05 PostgreSQL 数据库设计

## 1. 设计原则

1. PostgreSQL 是业务事实来源；
2. Milvus 仅是正式发布内容的检索索引；
3. Raw / Candidate / Published 三层内容必须分开；
4. 状态流可追踪；
5. 文件实际内容放 MinIO，DB 存元数据与对象 key；
6. 所有重要操作保留审计信息。

---

# 2. 主要表分组

```text
A. 用户与权限
B. 知识库问答
C. 简历分析
D. 采集任务
E. 原始内容与媒体资产
F. 候选面经
G. 正式发布面经
H. 评分、去重、审核
I. 系统与日志
```

---

# 3. 用户与权限表

## 3.1 users

字段建议：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint / uuid | 主键 |
| email | varchar | 唯一 |
| username | varchar | 唯一可选 |
| password_hash | varchar | 密码哈希 |
| role | enum(USER, ADMIN) | 角色 |
| is_active | bool | 是否启用 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

---

# 4. 知识库与聊天表

## 4.1 kb_documents

| 字段 | 说明 |
|---|---|
| id | 文档 ID |
| title | 文档标题 |
| source_type | markdown/pdf/manual/... |
| storage_key | 原始文件对象 key |
| status | IMPORTED / CHUNKED / INDEXED / FAILED |
| created_at | 创建时间 |

## 4.2 kb_chunks

| 字段 | 说明 |
|---|---|
| id | chunk ID |
| document_id | 文档 ID |
| chunk_index | 序号 |
| content | 文本 |
| token_count | token 数 |
| embedding_status | NOT_INDEXED / INDEXED / FAILED |
| created_at | 创建时间 |

## 4.3 chat_sessions

| 字段 | 说明 |
|---|---|
| id | 会话 ID |
| user_id | 用户 ID |
| title | 自动或手动标题 |
| created_at | 创建时间 |
| updated_at | 更新时间 |

## 4.4 chat_messages

| 字段 | 说明 |
|---|---|
| id | 消息 ID |
| session_id | 会话 ID |
| role | user / assistant / system |
| content | 内容 |
| citations_json | 引用 |
| created_at | 创建时间 |

---

# 5. 简历模块表

## 5.1 resumes

| 字段 | 说明 |
|---|---|
| id | 简历 ID |
| user_id | 所属用户 |
| original_filename | 原文件名 |
| storage_key | MinIO key |
| mime_type | 类型 |
| file_size | 大小 |
| parse_status | PENDING / SUCCESS / FAILED |
| created_at | 上传时间 |

## 5.2 resume_parse_results

| 字段 | 说明 |
|---|---|
| id | 结果 ID |
| resume_id | 简历 ID |
| raw_text | 解析文本 |
| structured_json | 结构化简历 |
| parser_version | 解析版本 |
| created_at | 创建时间 |

## 5.3 resume_analysis_reports

| 字段 | 说明 |
|---|---|
| id | 报告 ID |
| resume_id | 简历 ID |
| user_id | 用户 ID |
| status | PENDING / RUNNING / SUCCESS / FAILED |
| summary | 报告摘要 |
| report_json | 完整报告 |
| error_message | 失败原因 |
| created_at | 创建时间 |
| updated_at | 更新时间 |

## 5.4 resume_questions

| 字段 | 说明 |
|---|---|
| id | 问题 ID |
| report_id | 报告 ID |
| category | project / skill / engineering / pressure |
| question | 问题 |
| answer_points | 要点 |
| sample_answer | 参考答案 |
| follow_up_questions_json | 继续追问 |
| created_at | 创建时间 |

---

# 6. 采集任务表

## 6.1 crawl_plans

用于保存可重复使用的采集计划。

| 字段 | 说明 |
|---|---|
| id | 计划 ID |
| name | 计划名 |
| created_by | 管理员 ID |
| config_json | 平台、关键词、参数 |
| is_active | 是否启用 |
| created_at | 创建时间 |

## 6.2 crawl_runs

一次实际运行。

| 字段 | 说明 |
|---|---|
| id | run ID |
| plan_id | 可为空，表示即时任务 |
| created_by | 管理员 ID |
| run_type | MANUAL / SCHEDULED |
| status | CREATED / RUNNING / PARTIAL_SUCCESS / SUCCESS / FAILED |
| config_snapshot_json | 运行时配置快照 |
| stats_json | 统计 |
| started_at | 开始时间 |
| finished_at | 结束时间 |

## 6.3 crawl_tasks

单个任务拆分单元。

| 字段 | 说明 |
|---|---|
| id | task ID |
| run_id | 所属 run |
| platform | NOWCODER / XHS / DOUYIN |
| keyword | 关键词 |
| status | 状态 |
| stage | DISCOVERY / COLLECTION / PARSING / ... |
| retry_count | 重试次数 |
| last_error | 最近错误 |
| created_at | 创建时间 |
| updated_at | 更新时间 |

---

# 7. 原始内容与资产表

## 7.1 raw_contents

| 字段 | 说明 |
|---|---|
| id | raw 内容 ID |
| task_id | 来源任务 |
| platform | 平台 |
| source_item_id | 平台侧 ID |
| canonical_url | 原始 URL |
| title | 标题 |
| author_name | 作者 |
| publish_time | 发布时间 |
| raw_payload_key | 原始 JSON / HTML 对象 key |
| raw_text_snapshot | 可选简短文本快照 |
| fetch_status | SUCCESS / FAILED |
| created_at | 创建时间 |
| updated_at | 更新时间 |

唯一约束建议：

```text
(platform, source_item_id)
```

## 7.2 raw_assets

| 字段 | 说明 |
|---|---|
| id | asset ID |
| raw_content_id | raw 内容 |
| asset_type | IMAGE / VIDEO / AUDIO / HTML / JSON |
| storage_key | 对象 key |
| original_url | 原地址 |
| mime_type | 类型 |
| file_size | 大小 |
| checksum | hash |
| created_at | 创建时间 |

---

# 8. 解析与多模态中间表

## 8.1 parsed_contents

| 字段 | 说明 |
|---|---|
| id | parsed ID |
| raw_content_id | raw ID |
| parsed_text | 主体文本 |
| parser_version | 解析器版本 |
| parse_status | SUCCESS / FAILED |
| created_at | 创建时间 |

## 8.2 ocr_results

| 字段 | 说明 |
|---|---|
| id | OCR ID |
| raw_asset_id | 图片或帧资产 |
| text | OCR 文本 |
| provider | OCR provider |
| status | SUCCESS / FAILED |
| created_at | 创建时间 |

## 8.3 asr_results

| 字段 | 说明 |
|---|---|
| id | ASR ID |
| raw_asset_id | 音频或视频资产 |
| transcript | 转写文本 |
| provider | ASR provider |
| status | SUCCESS / FAILED |
| created_at | 创建时间 |

## 8.4 fused_contents

| 字段 | 说明 |
|---|---|
| id | fused ID |
| raw_content_id | raw ID |
| parsed_content_id | parsed ID |
| fused_text | 融合后的文本 |
| fusion_version | 版本 |
| created_at | 创建时间 |

---

# 9. 候选面经表

## 9.1 candidate_experiences

| 字段 | 说明 |
|---|---|
| id | candidate ID |
| raw_content_id | raw ID |
| fused_content_id | fused ID |
| platform | 平台 |
| company | 公司 |
| position | 岗位 |
| stage | 面试轮次 |
| experience_summary | 摘要 |
| extraction_json | 全部抽取结果 |
| content_quality_status | VALID / INVALID / UNCERTAIN |
| review_status | PENDING / APPROVED / REJECTED |
| publish_status | NOT_PUBLISHED / PUBLISHED |
| created_at | 创建时间 |
| updated_at | 更新时间 |

## 9.2 candidate_questions

| 字段 | 说明 |
|---|---|
| id | 问题 ID |
| candidate_id | 候选面经 |
| raw_question | 原始问题 |
| canonical_question | 归一化问题 |
| answer_clue | 原内容中提到的回答要点 |
| question_type | 技术分类 |
| confidence | 置信度 |
| created_at | 创建时间 |

---

# 10. 评分与去重

## 10.1 reliability_scores

| 字段 | 说明 |
|---|---|
| id | score ID |
| candidate_id | 候选 ID |
| rule_score | 规则分 |
| ai_score | AI 分 |
| marketing_risk_score | 营销风险 |
| freshness_score | 新鲜度 |
| cross_source_score | 多源一致性预留 |
| final_score | 最终分 |
| explanation_json | 评分解释 |
| created_at | 创建时间 |

## 10.2 dedup_clusters

| 字段 | 说明 |
|---|---|
| id | cluster ID |
| cluster_key | 聚类 key |
| representative_candidate_id | 代表内容 |
| created_at | 创建时间 |

## 10.3 dedup_members

| 字段 | 说明 |
|---|---|
| id | member ID |
| cluster_id | cluster |
| candidate_id | 候选 |
| similarity_score | 相似度 |
| created_at | 创建时间 |

---

# 11. 正式发布面经表

## 11.1 published_experiences

| 字段 | 说明 |
|---|---|
| id | published ID |
| source_candidate_id | 来源候选 ID |
| platform | 平台 |
| company | 公司 |
| position | 岗位 |
| stage | 轮次 |
| title | 展示标题 |
| summary | 展示摘要 |
| reliability_score | 最终可信度 |
| reliability_explanation_json | 可信度解释 |
| publish_status | PUBLISHED / UNPUBLISHED |
| index_status | NOT_INDEXED / INDEXING / INDEXED / FAILED |
| published_by | 管理员 ID |
| published_at | 发布时间 |

## 11.2 published_experience_questions

| 字段 | 说明 |
|---|---|
| id | published question ID |
| published_experience_id | 关联正式面经 |
| canonical_question | 归一化问题 |
| raw_question | 原文问题 |
| answer_clue | 回答线索 |
| question_type | 类型 |
| created_at | 创建时间 |

---

# 12. 审核与审计

## 12.1 review_actions

| 字段 | 说明 |
|---|---|
| id | action ID |
| candidate_id | 候选 ID |
| reviewer_id | 管理员 ID |
| action | APPROVE / REJECT / PUBLISH / UNPUBLISH / RETRY |
| note | 备注 |
| created_at | 时间 |

---

# 13. 索引日志

## 13.1 vector_index_jobs

| 字段 | 说明 |
|---|---|
| id | job ID |
| entity_type | KB_CHUNK / EXPERIENCE / EXPERIENCE_QUESTION |
| entity_id | 业务 ID |
| target_collection | collection |
| status | PENDING / INDEXED / FAILED |
| error_message | 错误 |
| created_at | 创建时间 |

---

# 14. 关键关系摘要

```text
raw_contents
  -> raw_assets
  -> parsed_contents
  -> fused_contents
  -> candidate_experiences
       -> candidate_questions
       -> reliability_scores
       -> review_actions
       -> published_experiences
            -> published_experience_questions
```

---

# 15. 关键约束

1. raw_contents 唯一性必须控制；
2. published_experiences 应保留 source_candidate_id；
3. candidate 与 published 状态不能混淆；
4. index_status 不是 publish_status；
5. 用户搜索仅依赖 publish_status = PUBLISHED 的内容；
6. Milvus 写入成功后更新 index_status；
7. 审核动作要保留 review_actions。

---

# 16. 迁移策略

Phase 0：

- 基础 DB 连接
- Alembic 初始化

Phase 1：

- users

Phase 2：

- kb_documents
- kb_chunks
- chat_sessions
- chat_messages

Phase 3：

- resumes
- resume_parse_results
- resume_analysis_reports
- resume_questions

Phase 4：

- crawl_plans
- crawl_runs
- crawl_tasks

Phase 5：

- raw_contents
- raw_assets
- parsed_contents
- fused_contents
- candidate_experiences
- candidate_questions
- reliability_scores
- review_actions
- published_experiences
- published_experience_questions
- vector_index_jobs
