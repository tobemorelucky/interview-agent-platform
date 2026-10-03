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

Phase 4（重新定义）：

- experience_keyword_presets（岗位/公司关键词预设）
- experience_collection_tasks（管理员创建的采集任务：时间范围+关键词+平台范围）
- experience_source_items（搜索结果 URL → 抓取 → 解析 → 审核状态追踪）
- interview_experiences（抽取/审核通过的面经记录）
- interview_questions（从面经中抽取的题目和答案）
- experience_recent_summaries（用户端近期面经总结，聚合展示）

Phase 5（合并至 Phase 4.6）：

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

---

# 17. M1 公共职业数据域（2026-09-30）

M1 将公司、岗位、JD、来源和证据建模为共享公共数据。以下公共表均禁止
`user_id`：

```text
Company -> CompanyAlias
Company -> Job -> JobDescriptionVersion -> JobRequirement
              -> JobTag
Source -> SourceSnapshot -> ClaimEvidence -> Claim
Claim -> JobRequirement
Job -> InterviewExperience / InterviewQuestion
```

## 17.1 Company 与 Job

- `companies.normalized_name` 全局唯一；规范名使用确定性 NFKC、空白归一化和
  casefold 处理。
- 公司别名存放在独立的 `company_aliases` 表，
  `(company_id, normalized_alias)` 唯一；别名不要求全局唯一。
- `jobs.company_id` 非空，`(company_id, normalized_title)` 唯一。
- M1 的 `job_family`、`campus_type`、`level` 均为可空文本，不引入复杂枚举。

## 17.2 JD 版本与要求

- `job_description_versions` 只追加版本，不原地覆盖历史正文。
- `(job_id, version_number)` 与 `(job_id, content_fingerprint)` 均唯一。
- JD 可选关联 `source_snapshot_id`，并保留结构化数据、元数据和观察/有效时间。
- `job_requirements.claim_id` 可空；存在时通过
  `Claim -> ClaimEvidence -> SourceSnapshot` 追溯，不复制 Evidence 正文。

## 17.3 来源与证据

- `sources.url_hash` 唯一，canonical URL 的跟踪参数会在 hash 前移除。
- `source_snapshots` 以 `(source_id, content_hash)` 去重。
- `content_hash` 另建非唯一全局索引，用于识别不同 URL 的镜像或转载；跨 Source
  相同 hash 可以同时保留。
- `claims`、`claim_evidence` 是纯公共 provenance 层，不依赖 ResearchRun、Artifact
  或用户实体。

## 17.4 面经兼容与指纹

- `interview_experiences` 和 `interview_questions` 新增可空 `company_id`、`job_id`。
- 原 `company`、`position` 字符串继续保留，历史数据无需强制关联。
- `interview_experiences.content_fingerprint` 和
  `interview_questions.question_fingerprint` 使用统一的确定性文本归一化 + SHA-256。
- 同时写入 company/job 外键时，应用层必须验证 `Job.company_id == company_id`。

## 17.5 Backfill

M1 backfill 只允许：

1. 从已有正文和问题确定性生成 fingerprint；
2. 对 canonical name 与 alias 建立规范化名称候选集合；
3. 只有名称唯一指向一个 Company 时才关联；
4. 只有 Company 已唯一确定、且岗位标题精确匹配时才关联 Job。

歧义或无法精确匹配的记录保留旧字符串，外键保持 `NULL`。禁止使用 LLM、
embedding 或模糊匹配推测归属。

---

# 18. M2A 公共采集审计（2026-09-30）

- `tool_invocations` 审计每一次 Gateway 调用（成功和失败），并以非空
  `refresh_task_id` 关联 `public_data_refresh_tasks`。
- Search 结果只创建/复用 `sources`，搜索标题、snippet、engine 等保存在
  `sources.metadata_json`；Search 不创建 `source_snapshots`。
- Fetch 成功后才按 `(source_id, content_hash)` 创建/复用 `source_snapshots`；
  `created_by_tool_invocation_id` 记录首次创建该快照的调用。
- `experience_collection_tasks.public_refresh_task_id` 一对一绑定公共刷新任务；旧表
  继续承担管理 UI 兼容字段，不再独立维护同一轮网络采集状态。
- 以上变更由 Alembic `0019` 引入，`0017`/`0018` 保持不变。

---

# 19. M3 私有 Candidate Profile

当前实现的 Resume 源材料是 `resumes.raw_text` + `resume_reports.summary_json`，
不是前文早期规划中的 parse_results 表。M3 保留实现，不新增 ResumeVersion。
`candidate_profiles.user_id` 唯一，current_version_id 可空且只能指向本聚合根版本。
Version 是 immutable 全量快照，version number 与 generation key 有唯一约束。
ResumeEvidence 确定性生成；UserConfirmedEvidence 区分本人确认。
规范化经历/技能通过 CandidateFactEvidence 的真实 FK 溯源；历史由 immutable/sealed
triggers 保护，Resume 来源 FK 为 RESTRICT。只新增 `0021`。
详见 [M3 数据模型、事务和删除保护](21_candidate_profile_m3.md)。

# 20. M4 面试上下文与回答评价

`interview_context_snapshots` 固定私有 CandidateProfileVersion、公共 Company/Job/JD、
Requirement、批准题目/面经的实际内容与 fingerprint。Snapshot 禁止 UPDATE，Session
禁止换 Snapshot；选中公共 refs 不可删除。历史 Session 保持 legacy，不猜测关联。
`interview_answers` 独立保存回答/幂等键，`interview_evaluations` 每 Answer 唯一且五维
CHECK 1–5；`interview_agent_calls` 关联共享 ExperienceAgentRun/StepRun。
active_question_id + status/sequence 是状态真相源，index 只由 Controller 同步。
只新增 0022；详见 [M4 数据模型与验收](22_mock_interview_core_m4.md)。

# 21. M5 Report / Observation / MemoryProposal

`final_interview_reports` 为每场保存 generation/key、固定 Snapshot、数据库统计和独立
Coach synthesis；partial unique 保证最多一个成功 current。`interview_report_attempts`
保留失败/重试审计。成功内容 immutable，完整发布事务最后切换 current。
`interview_performance_observations` 与 Evaluation FK junction 保存单场表现，不修改
CandidateProfile/UserSkill。`memory_proposals` 和支持 junction 保存 PENDING 建议、原文/
修正文、USER 确认来源；批准后单事务写现有 UserMemoryItem/Event，没有第二套 Memory。
Report/Session/支持评价采用 RESTRICT provenance 保护，只新增 0023，不补历史报告。
详见 [M5 schema、事务与验收](23_interview_coach_m5.md)。

# 22. M6 Frozen Job Matching

`job_match_runs` 固定私有 ProfileVersion、显式过滤、本人确认 preference/goal、公共 Job/JD
版本/Requirement/Fingerprint、实际 Evidence 输入和 policy/prompt。`job_match_candidates`
保存确定性 Eligibility/Matrix/排序组成、pre/agent/final；NOT_SATISFIED 不允许有 rank。
`job_match_recommendations` 每候选唯一，`job_match_attempts` 关联共享 Agent audit。
数据库触发器保护 Run 输入和已完成历史；完整发布前不得 COMPLETED。来源 FK RESTRICT。
只新增 0024，不补历史 Run，不修改 0017–0023。详见 [M6 schema](24_job_matching_m6.md)。
